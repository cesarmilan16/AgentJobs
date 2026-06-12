from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable

import httpx

from job_agent.models import JobOffer

log = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/sendMessage"
_CHUNK_MAX = 3800  # leave headroom under Telegram's 4096 char limit


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id

    def send_text(self, text: str) -> None:
        for chunk in _chunk(text, _CHUNK_MAX):
            try:
                resp = httpx.post(
                    _API.format(token=self.bot_token),
                    data={
                        "chat_id": self.chat_id,
                        "text": chunk,
                        "parse_mode": "Markdown",
                        "disable_web_page_preview": True,
                    },
                    timeout=20,
                )
                if resp.status_code >= 400:
                    log.error("telegram error %s: %s", resp.status_code, resp.text[:300])
            except Exception:
                log.exception("telegram: failed to send chunk")

    def send_offers(self, offers: list[JobOffer], *, when: datetime) -> None:
        if not offers:
            self.send_text(
                f"✅ Agente OK · {when:%Y-%m-%d %H:%M} · 0 ofertas nuevas sobre el umbral."
            )
            return
        header = f"*Ofertas nuevas* · {when:%Y-%m-%d %H:%M} · {len(offers)} resultados\n\n"
        body = "\n\n".join(_format_offer(o) for o in offers)
        self.send_text(header + body)

    def send_error(self, exc: BaseException) -> None:
        self.send_text(f"⚠️ *Agente falló*: `{type(exc).__name__}`\n```\n{str(exc)[:1500]}\n```")


def _format_offer(o: JobOffer) -> str:
    score = o.score if o.score is not None else "—"
    reason = (o.score_reason or "").replace("`", "")
    title = _md_escape(o.title)
    company = _md_escape(o.company)
    location = _md_escape(o.location or "—")
    return (
        f"*[{title}]({o.url})*\n"
        f"{company} · {o.source.value} · *{score}/100* · {location}\n"
        f"_{reason}_"
    )


def _md_escape(s: str) -> str:
    # Lightweight escape for Markdown v1: enough to avoid most parse breakage.
    return s.replace("[", "(").replace("]", ")").replace("*", "·").replace("_", " ")


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
