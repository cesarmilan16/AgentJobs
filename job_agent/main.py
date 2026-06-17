from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from job_agent.collectors.base import Collector
from job_agent.collectors.infojobs import InfoJobsCollector
from job_agent.collectors.linkedin_indeed import JobSpyCollector
from job_agent.collectors.tecnoempleo import TecnoempleoCollector, enrich_descriptions
from job_agent.config import AppConfig, load_config
from job_agent.filtering.ai_scorer import AIScorer
from job_agent.filtering.hard_filters import HardFilterConfig, partition
from job_agent.models import JobOffer
from job_agent.notify.telegram import TelegramNotifier
from job_agent.storage.db import Database

log = logging.getLogger("job_agent")
MADRID = ZoneInfo("Europe/Madrid")


def cli() -> None:
    parser = argparse.ArgumentParser(prog="job-agent")
    parser.add_argument("--dry-run", action="store_true", help="No envía Telegram; imprime por consola")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    cfg = load_config(args.config, require_secrets=not args.dry_run)
    _setup_logging(args.log_level, cfg.log_path)

    notifier: TelegramNotifier | None = None
    if not args.dry_run:
        notifier = TelegramNotifier(
            bot_token=cfg.secrets.telegram_bot_token,
            chat_id=cfg.secrets.telegram_chat_id,
        )

    try:
        run(cfg, notifier=notifier, dry_run=args.dry_run)
    except Exception as exc:
        log.exception("run failed")
        if notifier is not None:
            try:
                notifier.send_error(exc)
            except Exception:
                log.exception("could not even send the error message")
        sys.exit(1)


def run(cfg: AppConfig, *, notifier: TelegramNotifier | None, dry_run: bool) -> None:
    now_madrid = datetime.now(MADRID)
    log.info("=== job-agent run start %s ===", now_madrid.isoformat())

    db = Database(cfg.db_path)

    collectors = _build_collectors(cfg)
    all_offers: list[JobOffer] = []
    for c in collectors:
        all_offers.extend(c.collect_safe())
    log.info("collected_total=%d", len(all_offers))

    # In-run dedupe (in case two collectors return same URL).
    by_id: dict[str, JobOffer] = {}
    for o in all_offers:
        by_id.setdefault(o.id, o)
    all_offers = list(by_id.values())

    fresh = db.filter_new(all_offers)
    log.info("after_db_dedupe=%d", len(fresh))

    # Tecnoempleo no trae descripción en el listado; se baja de la página de
    # detalle solo para las ofertas nuevas, así los filtros y la IA ven el texto
    # real (seniority, idioma, tipo de rol) y no juzgan solo por el título.
    fresh = enrich_descriptions(fresh)

    hf_cfg = HardFilterConfig.from_dict(cfg.raw.get("hard_filters", {}))
    kept, discarded = partition(fresh, hf_cfg)
    log.info("hard_filters kept=%d discarded=%d", len(kept), len(discarded))
    db.upsert_many(discarded)

    # AI scoring only for survivors.
    scored: list[JobOffer] = []
    if kept:
        if dry_run and not cfg.secrets.openai_api_key:
            log.warning("dry-run without OPENAI_API_KEY: skipping AI scoring")
            scored = kept
        else:
            scorer = AIScorer(api_key=cfg.secrets.openai_api_key)
            scored = scorer.score_many(kept)
    db.upsert_many(scored)

    threshold = cfg.threshold
    above = [o for o in scored if (o.score or 0) >= threshold]
    above.sort(key=lambda o: o.score or 0, reverse=True)
    log.info("above_threshold=%d threshold=%d", len(above), threshold)

    if dry_run:
        _print_console(above, all_offers, kept, scored, threshold)
        return

    assert notifier is not None
    notifier.send_offers(above, when=now_madrid)
    db.mark_sent([o.id for o in above])
    log.info("=== job-agent run end ===")


def _build_collectors(cfg: AppConfig) -> list[Collector]:
    collectors: list[Collector] = []
    tecno_terms = cfg.raw.get("tecnoempleo", {}).get("search_terms", [])
    if tecno_terms:
        collectors.append(TecnoempleoCollector(search_terms=tecno_terms))

    infojobs_cfg = cfg.raw.get("infojobs", {})
    if infojobs_cfg.get("enabled") and infojobs_cfg.get("search_terms"):
        collectors.append(InfoJobsCollector(
            search_terms=infojobs_cfg["search_terms"],
            headless=infojobs_cfg.get("headless", False),
        ))

    jobspy_cfg = cfg.raw.get("jobspy", {})
    if jobspy_cfg and cfg.raw.get("search_terms"):
        collectors.append(JobSpyCollector(
            sites=jobspy_cfg.get("sites", ["linkedin", "indeed"]),
            search_terms=cfg.raw["search_terms"],
            hours_old=jobspy_cfg.get("hours_old", 14),
            results_wanted=jobspy_cfg.get("results_wanted", 50),
            country_indeed=jobspy_cfg.get("country_indeed", "spain"),
            proxies=cfg.secrets.proxy_list or None,
        ))
    return collectors


def _print_console(
    above: list[JobOffer],
    all_offers: list[JobOffer],
    kept: list[JobOffer],
    scored: list[JobOffer],
    threshold: int,
) -> None:
    print(f"\n--- DRY RUN ---")
    print(f"collected={len(all_offers)} survived_hard={len(kept)} scored={len(scored)} above_threshold={len(above)} (>= {threshold})")
    for o in above:
        print(f"[{o.score:>3}] {o.title}  ·  {o.company}  ·  {o.source.value}")
        print(f"      {o.url}")
        print(f"      {o.score_reason}")
    print("--- /DRY RUN ---\n")


def _setup_logging(level: str, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(logging.handlers.RotatingFileHandler(
            log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8",
        ))
    except OSError:
        # If the volume is read-only or path invalid, fall back to stdout only.
        pass
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
        handlers=handlers,
        force=True,
    )
