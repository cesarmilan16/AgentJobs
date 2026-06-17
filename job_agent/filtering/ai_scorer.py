from __future__ import annotations

import json
import logging
from typing import Iterable

from openai import OpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from job_agent.models import JobOffer

log = logging.getLogger(__name__)

MODEL = "gpt-4o-mini"

SYSTEM_PROMPT = (
    "Eres un evaluador de ofertas de empleo para un desarrollador full-stack "
    "junior (~1 año de experiencia: prácticas + freelance) que busca contrato "
    "indefinido 100% remoto en España o híbrido en Sevilla.\n"
    "\n"
    "Stack del candidato: React/Next.js, Angular, Node.js/Express, TypeScript, "
    "Java/Spring Boot, Python/FastAPI, PostgreSQL/Supabase, Docker, AWS S3. "
    "Experiencia integrando OpenAI API y Claude API en producción. "
    "Certificación MCP de Anthropic. Roles objetivo: junior/mid full-stack, "
    "frontend, backend, AI engineer/AI builder junior.\n"
    "\n"
    "Descartar fuerte (score < 30): senior/lead/architect, +4 años exigidos, "
    "fuera de España sin remoto, presenciales fuera de Sevilla, stacks "
    "completamente ajenos (.NET puro, PHP puro, SAP/ABAP, mainframe).\n"
    "\n"
    "Descartar también (score < 20) si NO es un puesto de desarrollo de "
    "software: ingeniería industrial/mecánica/civil/electrónica, soporte/"
    "helpdesk, QA manual, consultoría funcional, datos/BI sin desarrollo. "
    "Aunque el título diga 'junior', mira la descripción: el rol debe ser "
    "programar.\n"
    "\n"
    "Señales de seniority encubierta (penaliza fuerte, suelen implicar 'senior' "
    "aunque no lo digan): banda salarial alta (>45.000€/año), exigir 'inglés "
    "alto/fluido/C1' como imprescindible, liderar equipos, '+4 años'.\n"
    "\n"
    "Inglés del candidato: B1-B2. Penaliza (no descartes) ofertas que exijan "
    "'fluent/native English' como requisito imprescindible.\n"
    "\n"
    "Devuelve EXCLUSIVAMENTE un JSON con esta forma exacta:\n"
    '{"score": <int 0-100>, "razon": "<una frase breve en español>", '
    '"remoto": "si"|"no"|"desconocido"}\n'
    "\n"
    "El score expresa cómo de buena es la oferta PARA ESTE CANDIDATO, no la "
    "calidad absoluta de la empresa. Sé estricto."
)


class AIScorer:
    def __init__(self, api_key: str, model: str = MODEL):
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def score_offer(self, offer: JobOffer) -> JobOffer:
        try:
            data = self._call(offer)
        except Exception:
            log.exception("ai_scorer: giving up on offer id=%s", offer.id)
            return offer.model_copy(update={
                "score": 0,
                "score_reason": "Error al puntuar con IA",
                "remote_per_ai": "desconocido",
            })

        return offer.model_copy(update={
            "score": int(data.get("score", 0)),
            "score_reason": str(data.get("razon", ""))[:300],
            "remote_per_ai": str(data.get("remoto", "desconocido")),
        })

    def score_many(self, offers: Iterable[JobOffer]) -> list[JobOffer]:
        return [self.score_offer(o) for o in offers]

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    def _call(self, offer: JobOffer) -> dict:
        user_msg = (
            f"Título: {offer.title}\n"
            f"Empresa: {offer.company}\n"
            f"Ubicación: {offer.location or '—'}\n"
            f"is_remote_fuente: {offer.is_remote}\n"
            f"Descripción:\n{(offer.description or '')[:3500]}"
        )
        resp = self.client.chat.completions.create(
            model=self.model,
            response_format={"type": "json_object"},
            temperature=0.0,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
        )
        content = resp.choices[0].message.content or "{}"
        data = json.loads(content)
        if "score" not in data:
            raise ValueError(f"unexpected ai response: {content[:200]}")
        return data
