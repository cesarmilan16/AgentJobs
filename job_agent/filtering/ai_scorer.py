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
    "indefinido. Modalidad deseada: 100% remoto en España, O híbrido/presencial "
    "en Sevilla.\n"
    "\n"
    "Stack del candidato: React/Next.js, Angular, Node.js/Express, TypeScript, "
    "Java/Spring Boot, Python/FastAPI, PostgreSQL/Supabase, Docker, AWS S3. "
    "Experiencia integrando OpenAI API y Claude API en producción. "
    "Certificación MCP de Anthropic. Roles objetivo: junior/mid full-stack, "
    "frontend, backend, AI engineer/AI builder junior.\n"
    "\n"
    "REGLA DE UBICACIÓN (importante): si el puesto es 100% remoto/teletrabajo "
    "dentro de España, la CIUDAD de la empresa es IRRELEVANTE: NO penalices por "
    "ella (un remoto con empresa en Madrid es perfecto). Solo penaliza la "
    "ubicación si es presencial/híbrido y NO es Sevilla, o si exige estar fuera "
    "de España sin remoto.\n"
    "\n"
    "Rúbrica de puntuación (sé realista, usa todo el rango):\n"
    "- 80-100: rol de desarrollo, stack alineado con el candidato, nivel "
    "junior/mid, y remoto en España (o Sevilla). Pegas nulas o mínimas.\n"
    "- 60-79: rol de desarrollo alineado pero con UNA pega menor (stack solo "
    "parcialmente coincidente, inglés 'valorable', empresa muy grande, etc.).\n"
    "- 40-59: rol de desarrollo con pegas serias (stack bastante distinto, "
    "híbrido/presencial fuera de Sevilla, inglés alto deseable).\n"
    "- 0-39: descartar. Aplica a: NO es desarrollo de software (ingeniería "
    "industrial/mecánica/civil, soporte/helpdesk, QA manual, consultoría "
    "funcional, BI sin programar); senior/lead/architect o +4 años exigidos; "
    "stack totalmente ajeno (.NET puro, PHP puro, SAP/ABAP, mainframe); fuera "
    "de España sin remoto; presencial fuera de Sevilla; inglés C1/nativo "
    "imprescindible; salario claramente de senior (>55.000€/año).\n"
    "\n"
    "Aunque el título diga 'junior' o tenga buen stack, MIRA la descripción: el "
    "rol debe ser programar y el nivel debe encajar con un junior/mid.\n"
    "\n"
    "Devuelve EXCLUSIVAMENTE un JSON con esta forma exacta:\n"
    '{"score": <int 0-100>, "razon": "<una frase breve en español>", '
    '"remoto": "si"|"no"|"desconocido"}\n'
    "\n"
    "El score expresa cómo de buena es la oferta PARA ESTE CANDIDATO, no la "
    "calidad absoluta de la empresa."
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
