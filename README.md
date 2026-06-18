# AgentJobs 🤖

> Agente autónomo que busca, filtra y puntúa ofertas de empleo con IA, y te las envía por Telegram dos veces al día.

![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python&logoColor=white)
![OpenAI](https://img.shields.io/badge/OpenAI-gpt--4o--mini-412991?logo=openai&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-Chromium-45ba4b?logo=playwright&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green)

---

## ¿Qué hace?

Cada día a las **08:00 y 20:00** (hora de Madrid), el agente:

1. **Recopila** ofertas de LinkedIn, Indeed, InfoJobs y Tecnoempleo
2. **Descarta** con filtros duros (seniority, stacks irrelevantes, países fuera de España)
3. **Puntúa** cada oferta del 0 al 100 con `gpt-4o-mini` según tu perfil
4. **Notifica** por Telegram solo las que superan el umbral, ordenadas por score

El resultado: recibes únicamente las ofertas que realmente encajan, sin ruido.

## Ejemplo de notificación

```
🔍 Nuevas ofertas · 18 jun · 08:00

━━━ ⭐ 85-100 ━━━
[90] Full Stack Developer – Remoto · Experis IT
     linkedin.com/jobs/view/...

[85] React Developer · M&GT Consulting
     infojobs.net/...

━━━ 🟢 70-84 ━━━
[75] Full Stack Node.js · sg tech
     tecnoempleo.com/...

📊 223 recopiladas · 161 tras filtros · 9 notificadas
```

## Arquitectura

```
┌─────────────────────────────────────────────────────┐
│                    job-agent                        │
│                                                     │
│  Collectors          Filtering         Output       │
│  ──────────          ─────────         ──────       │
│  LinkedIn  ──┐                                      │
│  Indeed    ──┼──► Hard filters ──► AI scorer ──► Telegram
│  InfoJobs  ──┤    (sin coste)     (gpt-4o-mini)     │
│  Tecnoempleo┘                         │             │
│                                       ▼             │
│                                    SQLite           │
│                               (dedupe + historial)  │
└─────────────────────────────────────────────────────┘
```

```
job_agent/
├── collectors/
│   ├── base.py              # Collector ABC con manejo de errores
│   ├── linkedin_indeed.py   # python-jobspy
│   ├── infojobs.py          # Playwright headed (bypass Distil/Imperva)
│   └── tecnoempleo.py       # scraping HTML + enriquecimiento de descripciones
├── filtering/
│   ├── hard_filters.py      # reglas de descarte por keywords
│   └── ai_scorer.py         # scoring con gpt-4o-mini (JSON mode) + reintentos
├── storage/db.py            # SQLite — dedupe por URL normalizada
├── notify/telegram.py       # formato HTML agrupado por tramos de score
├── config.py                # config.yaml + .env con validación
├── models.py                # JobOffer (Pydantic)
└── main.py                  # orquestador y CLI
```

## Retos técnicos destacados

**Bypass de Distil/Imperva en InfoJobs**
InfoJobs bloquea `requests`, `curl_cffi` y Playwright headless. La solución es Playwright en modo headed bajo `xvfb` en el servidor — navegador real con pantalla virtual.

| Método | Resultado |
|---|---|
| `requests` / `curl_cffi` | ❌ Captcha |
| Playwright headless | ❌ Bloqueado |
| Playwright headed + xvfb | ✅ Funciona |

**xvfb en Docker**
`xvfb-run` cuelga en algunos kernels Linux dentro de Docker porque Xvfb no envía `SIGUSR1` al proceso padre. Solución: script personalizado que arranca Xvfb directamente y espera al socket.

**Deduplicación persistente**
Las ofertas se identifican por hash de URL normalizada. La base de datos SQLite actúa como filtro entre ejecuciones para no notificar la misma oferta dos veces.

## Stack

| Capa | Tecnología |
|---|---|
| Runtime | Python 3.12 |
| Scraping dinámico | Playwright + Chromium |
| Scraping estático | requests + BeautifulSoup |
| Aggregador LinkedIn/Indeed | python-jobspy |
| IA scoring | OpenAI gpt-4o-mini (JSON mode) |
| Validación de modelos | Pydantic v2 |
| Persistencia | SQLite |
| Notificaciones | Telegram Bot API |
| Infraestructura | Docker Compose + cron |

## Instalación

### Requisitos

- Docker >= 24 y Docker Compose v2
- Cuenta OpenAI con acceso a `gpt-4o-mini`
- Bot de Telegram (créalo con [@BotFather](https://t.me/BotFather))

### Puesta en marcha

```bash
git clone https://github.com/cesarmilan16/AgentJobs.git
cd AgentJobs
cp .env.example .env
# Edita .env con tus credenciales
mkdir -p data
docker compose build
docker compose run --rm agent --dry-run   # prueba sin enviar Telegram
docker compose run --rm agent             # ejecución real
```

### Variables de entorno

| Variable | Requerida | Descripción |
|---|---|---|
| `OPENAI_API_KEY` | ✅ | API key de OpenAI |
| `TELEGRAM_BOT_TOKEN` | ✅ | Token del bot (@BotFather) |
| `TELEGRAM_CHAT_ID` | ✅ | Tu chat ID |
| `PROXY_LIST` | ❌ | Proxies separados por coma (mejora LinkedIn) |

### Programar con cron

```bash
crontab -e
```

```
0 8,20 * * * docker compose -f /ruta/AgentJobs/docker-compose.yml --project-directory /ruta/AgentJobs run --rm -T agent >> /ruta/AgentJobs/data/cron.log 2>&1
```

### Configuración (`config.yaml`)

| Parámetro | Descripción |
|---|---|
| `threshold` | Score mínimo (0-100) para notificar. Empieza en 70, sube si hay ruido. |
| `search_terms` | Términos para LinkedIn/Indeed |
| `infojobs.enabled` | Desactiva si Imperva endurece el bloqueo |
| `hard_filters` | Keywords para descartar sin llamar a la IA |

## Desarrollo local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
python -m job_agent --dry-run --log-level=DEBUG
pytest
```

## Coste estimado

Con los filtros duros activos, pasan ~10-20 ofertas al scorer por ejecución.
Con `gpt-4o-mini` (2 ejecuciones/día): **< 0,10 € / día**.

## Troubleshooting

| Síntoma | Causa | Solución |
|---|---|---|
| LinkedIn devuelve 0 resultados | IP de datacenter bloqueada | Añade proxies en `PROXY_LIST` |
| InfoJobs devuelve 0 resultados | Imperva endureció el bloqueo | `infojobs.enabled: false` en config.yaml |
| `Missing required env var` | Falta credencial | Revisa `.env` contra `.env.example` |
| Cron no dispara a las 8:00 reales | TZ del servidor incorrecta | `sudo timedatectl set-timezone Europe/Madrid` |

---

*Proyecto personal — desarrollado para automatizar mi propia búsqueda de empleo como desarrollador full-stack junior.*
