"""Verificaciones de los tres puntos del plan de test del PR."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from job_agent.config import AppConfig, Secrets
from job_agent.filtering.hard_filters import HardFilterConfig, apply
from job_agent.main import run
from job_agent.models import JobOffer, Source


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _cfg(tmp_path: Path, *, api_key: str = "") -> AppConfig:
    return AppConfig(
        raw={
            "storage": {
                "db_path": str(tmp_path / "test.db"),
                "log_path": str(tmp_path / "test.log"),
            },
            "threshold": 60,
            "hard_filters": {
                "exclude_locations_non_spain": ["germany", "uk", "france", "portugal"],
            },
        },
        secrets=Secrets(
            openai_api_key=api_key,
            telegram_bot_token="",
            telegram_chat_id="",
        ),
        config_path=Path("config.yaml"),
    )


def _offer(**kw) -> JobOffer:
    base = dict(
        title="Junior Developer",
        company="Acme",
        source=Source.LINKEDIN,
        url="https://ex.com/job/1",
        location="Madrid, Spain",
        is_remote=True,
        description="React + Node.js",
    )
    base.update(kw)
    return JobOffer.build(**base)


def _collector_with(offers: list[JobOffer]):
    """Devuelve un mock de Collector que retorna las ofertas dadas."""
    col = MagicMock()
    col.collect_safe.return_value = offers
    return col


# ---------------------------------------------------------------------------
# 1. dry-run sin OPENAI_API_KEY muestra ofertas sin filtrar por umbral
# ---------------------------------------------------------------------------

def test_dryrun_without_api_key_shows_all_hard_filter_survivors(tmp_path, capsys):
    """Ofertas con score=None deben mostrarse todas en dry-run (sin filtro de umbral)."""
    offers = [
        _offer(url=f"https://ex.com/{i}", title=f"Dev {i}") for i in range(3)
    ]
    cfg = _cfg(tmp_path, api_key="")

    with patch("job_agent.main._build_collectors", return_value=[_collector_with(offers)]):
        run(cfg, notifier=None, dry_run=True)

    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "survived_hard=3" in out
    # Las 3 ofertas deben aparecer (score N/A, no filtradas por umbral)
    assert out.count("[N/A]") == 3


# ---------------------------------------------------------------------------
# 2. dry-run con OPENAI_API_KEY NO llama a OpenAI
# ---------------------------------------------------------------------------

def test_dryrun_with_api_key_never_calls_openai(tmp_path):
    """Aunque haya API key, dry-run no debe instanciar ni llamar a AIScorer."""
    offers = [_offer(url="https://ex.com/1")]
    cfg = _cfg(tmp_path, api_key="sk-fake-key-12345")

    with patch("job_agent.main._build_collectors", return_value=[_collector_with(offers)]):
        with patch("job_agent.main.AIScorer") as mock_scorer_cls:
            run(cfg, notifier=None, dry_run=True)

    mock_scorer_cls.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Empleos híbridos fuera de España se descartan correctamente
# ---------------------------------------------------------------------------

@pytest.fixture
def location_cfg() -> HardFilterConfig:
    return HardFilterConfig.from_dict({
        "exclude_title_keywords": [],
        "exclude_text_keywords": [],
        "exclude_stack_in_title": [],
        "exclude_locations_non_spain": ["germany", "uk", "france", "portugal"],
    })


def test_hybrid_outside_spain_is_discarded(location_cfg):
    """Híbrido fuera de España debe descartarse: is_remote=False activa el filtro de país."""
    o = _offer(location="München, Germany", is_remote=False)
    keep, reason = apply(o, location_cfg)
    assert keep is False
    assert "germany" in reason


def test_hybrid_in_spain_is_kept(location_cfg):
    """Híbrido en España debe pasar el filtro de localización."""
    o = _offer(location="Madrid, Spain", is_remote=False)
    keep, _ = apply(o, location_cfg)
    assert keep is True


def test_fully_remote_outside_spain_is_kept(location_cfg):
    """Remoto puro desde empresa fuera de España sigue pasando (lo decide la IA)."""
    o = _offer(location="London, UK", is_remote=True)
    keep, _ = apply(o, location_cfg)
    assert keep is True


def test_hybrid_regex_no_longer_sets_is_remote(tmp_path):
    """REMOTE_RE ya no incluye híbrido: ofertas marcadas con modality=híbrido
    deben tener is_remote=False y ser descartadas si están fuera de España."""
    from job_agent.models import REMOTE_RE

    assert not REMOTE_RE.search("híbrido")
    assert not REMOTE_RE.search("Híbrido")
    assert not REMOTE_RE.search("hibrido")
    assert REMOTE_RE.search("remoto")
    assert REMOTE_RE.search("teletrabajo")
    assert REMOTE_RE.search("100% remoto")
