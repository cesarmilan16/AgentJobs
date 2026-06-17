from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Iterable

import httpx

from job_agent.models import JobOffer

log = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/sendMessage"
_CHUNK_MAX = 3800  # leave headroom under Telegram's 4096 char limit
_SEP = "━━━━━━━━━━━━"
_MESES = ("ene", "feb", "mar", "abr", "may", "jun",
          "jul", "ago", "sep", "oct", "nov", "dic")


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id

    def send_text(self, text: str, *, parse_mode: str = "HTML") -> None:
        for chunk in _chunk(text, _CHUNK_MAX):
            try:
                resp = httpx.post(
                    _API.format(token=self.bot_token),
                    data={
                        "chat_id": self.chat_id,
                        "text": chunk,
                        "parse_mode": parse_mode,
                        "disable_web_page_preview": True,
                    },
                    timeout=20,
                )
                if resp.status_code >= 400:
                    log.error("telegram error %s: %s", resp.status_code, resp.text[:300])
            except Exception:
                log.exception("telegram: failed to send chunk")

    def send_offers(self, offers: list[JobOffer], *, when: datetime) -> None:
        for msg in build_telegram_message(offers, when=when):
            self.send_text(msg)

    def send_error(self, exc: BaseException) -> None:
        self.send_text(
            f"⚠️ <b>Agente falló</b>: <code>{_esc(type(exc).__name__)}</code>\n"
            f"<pre>{_esc(str(exc)[:1500])}</pre>"
        )


# --- construcción del mensaje (función pura y testeable) ---

def build_telegram_message(offers: list[JobOffer], *, when: datetime) -> list[str]:
    """Construye el/los mensaje(s) HTML para Telegram a partir de las ofertas.

    Pura: no hace I/O. Ordena por score desc, deduplica, agrupa por tramos y
    trocea en varios mensajes si se supera el límite de Telegram. Devuelve una
    lista de strings HTML listos para enviar (siempre al menos uno)."""
    if not offers:
        return [f"✅ <b>Agente OK</b> · {_fecha_corta(when)} · "
                f"0 ofertas nuevas sobre el umbral."]

    ordered = sorted(_dedupe(offers), key=lambda o: o.score or 0, reverse=True)
    header = _build_header(ordered, when)

    messages: list[str] = []
    parts: list[str] = [header]
    cur_len = len(header)
    group_header = ""

    def flush() -> None:
        nonlocal parts, cur_len
        if parts:
            messages.append("\n\n".join(parts))
        parts = []
        cur_len = 0

    for label, group_offers in _group(ordered):
        group_header = f"{_SEP}\n{label}"
        if parts and cur_len + len(group_header) + 2 > _CHUNK_MAX:
            flush()
        parts.append(group_header)
        cur_len += len(group_header) + 2
        for o in group_offers:
            block = _format_offer_html(o)
            if cur_len + len(block) + 2 > _CHUNK_MAX:
                flush()
                # repetir la cabecera del grupo al continuar en otro mensaje
                parts.append(group_header)
                cur_len += len(group_header) + 2
            parts.append(block)
            cur_len += len(block) + 2

    flush()
    return messages


def _build_header(offers: list[JobOffer], when: datetime) -> str:
    n = len(offers)
    max_score = max((o.score or 0) for o in offers)
    n_remoto = sum(1 for o in offers if _is_remote(o))
    return (
        f"🛰️ <b>Nuevas ofertas</b> · {_fecha_corta(when)}\n"
        f"{n} resultados · mejor match <b>{max_score}</b> · {n_remoto} en remoto"
    )


def _group(offers: list[JobOffer]) -> list[tuple[str, list[JobOffer]]]:
    """Agrupa por tramos de score. Omite grupos vacíos. Asume offers ya
    ordenadas por score descendente."""
    alta = [o for o in offers if (o.score or 0) >= 85]
    buenas = [o for o in offers if 70 <= (o.score or 0) < 85]
    otras = [o for o in offers if (o.score or 0) < 70]
    groups: list[tuple[str, list[JobOffer]]] = []
    if alta:
        groups.append(("🔥 <b>ALTA</b> (≥85)", alta))
    if buenas:
        groups.append(("👍 <b>BUENAS</b> (70-84)", buenas))
    if otras:
        groups.append(("🧊 <b>OTRAS</b> (&lt;70)", otras))
    return groups


def _format_offer_html(o: JobOffer) -> str:
    score = o.score if o.score is not None else 0
    title = _esc(o.title)
    url = _esc(str(o.url))
    company = _esc(o.company)
    icon, modalidad = _modalidad_display(o)
    fuente = _esc(o.source.value)

    lines = [
        f'{_emoji_tramo(score)} <b>{score}</b> · <a href="{url}">{title}</a>',
        f"{company} · {icon} {modalidad} · {fuente}",
    ]
    caveat = (o.score_reason or "").strip()
    if caveat and caveat.lower() not in ("none", "null"):
        lines.append(f"⚠️ {_esc(caveat)}")
    return "\n".join(lines)


# --- helpers de presentación ---

def _emoji_tramo(score: int) -> str:
    if score >= 85:
        return "🟢"
    if score >= 70:
        return "🟡"
    return "🔴"


def _modalidad_display(o: JobOffer) -> tuple[str, str]:
    """Devuelve (icono, texto) de la modalidad. 🌐 remoto, 📍 presencial/híbrido.
    Si no se conoce, '📍 No indicada'."""
    m = (o.remote_per_ai or "").lower()
    if m == "remoto" or (m in ("", "desconocido", "none") and o.is_remote):
        return "🌐", "Remoto"
    if m == "hibrido":
        return "📍", "Híbrido"
    if m == "presencial":
        return "📍", "Presencial"
    return "📍", "No indicada"


def _is_remote(o: JobOffer) -> bool:
    return _modalidad_display(o)[1] == "Remoto"


def _fecha_corta(when: datetime) -> str:
    # "17 jun, 17:06" — mes en español, independiente del locale del sistema.
    return f"{when.day} {_MESES[when.month - 1]}, {when:%H:%M}"


def _esc(s: str) -> str:
    """Escapa solo los caracteres que rompen el parseo HTML de Telegram."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --- deduplicación ---

def _dedupe(offers: list[JobOffer]) -> list[JobOffer]:
    """Elimina ofertas repetidas: misma URL, o misma empresa+título normalizados.
    Conserva la de mayor score (procesa de mayor a menor)."""
    seen_url: set[str] = set()
    seen_key: set[tuple[str, str]] = set()
    out: list[JobOffer] = []
    for o in sorted(offers, key=lambda x: x.score or 0, reverse=True):
        url = str(o.url)
        key = (_norm(o.company), _norm(o.title))
        if url in seen_url or key in seen_key:
            continue
        seen_url.add(url)
        seen_key.add(key)
        out.append(o)
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _chunk(text: str, size: int) -> Iterable[str]:
    if len(text) <= size:
        yield text
        return
    buf: list[str] = []
    cur = 0
    for para in text.split("\n\n"):
        if cur + len(para) + 2 > size and buf:
            yield "\n\n".join(buf)
            buf = [para]
            cur = len(para)
        else:
            buf.append(para)
            cur += len(para) + 2
    if buf:
        yield "\n\n".join(buf)
