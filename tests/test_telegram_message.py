from __future__ import annotations

from datetime import datetime, timezone

from job_agent.models import JobOffer, Source
from job_agent.notify.telegram import build_telegram_message

WHEN = datetime(2026, 6, 17, 17, 6, tzinfo=timezone.utc)


def _offer(*, title, company, source, url, score, modalidad, caveat,
           is_remote=None, location="Madrid, Spain") -> JobOffer:
    return JobOffer.build(
        title=title, company=company, source=source, url=url,
        location=location, is_remote=is_remote,
    ).model_copy(update={
        "score": score,
        "score_reason": caveat,      # caveat viaja en score_reason
        "remote_per_ai": modalidad,  # modalidad viaja en remote_per_ai
    })


def _sample_offers() -> list[JobOffer]:
    return [
        _offer(
            title="AI Engineer Junior", company="Nimbus AI",
            source=Source.LINKEDIN, url="https://ex.com/a",
            score=92, modalidad="remoto", caveat=None,
        ),
        _offer(
            title="Full Stack Developer (React/Node)", company="Acme",
            source=Source.INFOJOBS, url="https://ex.com/b",
            score=78, modalidad="hibrido", caveat="Híbrido en Madrid",
        ),
        _offer(
            title="Frontend Developer & Co", company="Globex <Labs>",
            source=Source.TECNOEMPLEO, url="https://ex.com/c",
            score=64, modalidad="presencial", caveat="Inglés C1 obligatorio",
        ),
        _offer(
            title="Backend Node", company="Initech",
            source=Source.INDEED, url="https://ex.com/d",
            score=71, modalidad=None, caveat=None, location=None,
        ),
    ]


def test_message_has_header_and_groups():
    msgs = build_telegram_message(_sample_offers(), when=WHEN)
    assert len(msgs) == 1
    text = msgs[0]
    assert text.startswith("🛰️ <b>Nuevas ofertas</b> · 17 jun, 17:06")
    assert "4 resultados · mejor match <b>92</b> · 1 en remoto" in text
    # Grupos presentes y ordenados (ALTA antes que BUENAS antes que OTRAS).
    assert text.index("🔥 <b>ALTA</b> (≥85)") < text.index("👍 <b>BUENAS</b> (70-84)")
    assert text.index("👍 <b>BUENAS</b> (70-84)") < text.index("🧊 <b>OTRAS</b> (&lt;70)")
    assert text.count("━━━━━━━━━━━━") == 3  # un separador por grupo


def test_offer_lines_and_emojis():
    text = build_telegram_message(_sample_offers(), when=WHEN)[0]
    # Tramo alto -> 🟢, enlace y score en negrita.
    assert '🟢 <b>92</b> · <a href="https://ex.com/a">AI Engineer Junior</a>' in text
    assert "🌐 Remoto" in text          # modalidad remoto
    assert "📍 Híbrido" in text          # modalidad híbrido
    assert "📍 No indicada" in text      # modalidad/ubicación desconocida


def test_caveat_shown_only_when_present():
    text = build_telegram_message(_sample_offers(), when=WHEN)[0]
    assert "⚠️ Híbrido en Madrid" in text
    assert "⚠️ Inglés C1 obligatorio" in text
    # La oferta de score 92 no tiene caveat -> no debe colgar un ⚠️ tras ella.
    bloque_92 = text.split("AI Engineer Junior")[1].split("━")[0]
    assert "⚠️" not in bloque_92


def test_html_escaping_of_dynamic_text():
    text = build_telegram_message(_sample_offers(), when=WHEN)[0]
    assert "Globex &lt;Labs&gt;" in text  # < y > escapados en empresa
    assert "<Labs>" not in text


def test_empty_offers_returns_ok_message():
    msgs = build_telegram_message([], when=WHEN)
    assert len(msgs) == 1
    assert "0 ofertas nuevas sobre el umbral" in msgs[0]


def test_dedupe_same_url_and_same_company_title():
    base = _sample_offers()
    dup_url = _offer(
        title="Otro título", company="Otra", source=Source.INDEED,
        url="https://ex.com/a", score=50, modalidad="remoto", caveat=None,
    )
    dup_key = _offer(
        title="ai engineer junior", company="nimbus ai", source=Source.INDEED,
        url="https://ex.com/zzz", score=40, modalidad="remoto", caveat=None,
    )
    text = build_telegram_message(base + [dup_url, dup_key], when=WHEN)[0]
    assert "4 resultados" in text  # los 2 duplicados se descartan


def test_splits_into_several_messages_when_over_limit():
    many = [
        _offer(
            title=f"Full Stack Developer numero {i}", company=f"Empresa {i}",
            source=Source.LINKEDIN, url=f"https://ex.com/job/{i}",
            score=90, modalidad="remoto", caveat="Una pega cualquiera aqui",
        )
        for i in range(120)
    ]
    msgs = build_telegram_message(many, when=WHEN)
    assert len(msgs) > 1
    assert all(len(m) <= 4096 for m in msgs)


if __name__ == "__main__":  # demo: python -m tests.test_telegram_message
    for i, m in enumerate(build_telegram_message(_sample_offers(), when=WHEN), 1):
        print(f"===== MENSAJE {i} =====")
        print(m)
        print()
