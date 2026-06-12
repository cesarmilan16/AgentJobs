# job-agent

Agente Python que, dos veces al día, recopila ofertas de empleo recientes de
**LinkedIn, Indeed, InfoJobs y Tecnoempleo**, las filtra contra un perfil
fijo de full-stack junior, las puntúa con `gpt-4o-mini` y envía las mejores
a Telegram. Pensado para ejecutarse vía cron en un Ubuntu propio dentro de
Docker, a las **08:00 y 20:00 Europe/Madrid**.

## Arquitectura

```
job_agent/
├── collectors/
│   ├── base.py              # Collector ABC (collect_safe captura errores)
│   ├── linkedin_indeed.py   # python-jobspy (LinkedIn + Indeed)
│   ├── infojobs.py          # scraping HTML + JSON-LD
│   └── tecnoempleo.py       # feedparser sobre RSS
├── filtering/
│   ├── hard_filters.py      # reglas de descarte sin coste
│   └── ai_scorer.py         # gpt-4o-mini (JSON mode) + tenacity
├── storage/db.py            # SQLite (dedupe + histórico)
├── notify/telegram.py       # mensajes Markdown, troceo, mensaje "vivo"
├── config.py                # carga config.yaml + .env
├── models.py                # JobOffer (pydantic)
└── main.py                  # orquestador, CLI
```

## Requisitos previos

- Ubuntu/Linux con Docker (>= 24) y Docker Compose v2.
- Cuentas/credenciales:
  - **OpenAI API key** — [platform.openai.com/api-keys](https://platform.openai.com/api-keys).
  - **Telegram bot** — habla con [@BotFather](https://t.me/BotFather), `/newbot`, copia el token. Para sacar tu `chat_id`: envía un mensaje al bot y abre
    `https://api.telegram.org/bot<TOKEN>/getUpdates`, busca `chat.id` en el JSON.
  - **Proxies (opcional)** — solo si quieres que LinkedIn funcione con fiabilidad desde la IP del servidor.

> **Nota InfoJobs**: este collector hace **scraping** del HTML público
> (no usa la API oficial, que requiere aprobación como partner). Si Cloudflare
> bloquea las peticiones, ver sección [Contingencia InfoJobs](#contingencia-infojobs).

## Instalación

```bash
git clone <tu-repo> /opt/job-agent
cd /opt/job-agent
cp .env.example .env
# Edita .env con tus credenciales
```

### Variables de entorno (`.env`)

| Variable | Obligatoria | Descripción |
|---|---|---|
| `OPENAI_API_KEY` | sí | API key OpenAI con acceso a `gpt-4o-mini`. |
| `TELEGRAM_BOT_TOKEN` | sí | Token devuelto por @BotFather. |
| `TELEGRAM_CHAT_ID` | sí | ID del chat (tuyo) donde el bot publicará. |
| `PROXY_LIST` | no | Proxies separados por coma. Vacío = sin proxy. |

### Configuración (`config.yaml`)

- `threshold`: nota mínima (0-100) para que una oferta entre en el mensaje. **Empieza en 60**, sube si te llega ruido.
- `search_terms`: términos para LinkedIn/Indeed. Cada uno se ejecuta dos veces (España + remoto, y Sevilla on-site).
- `jobspy.sites`: incluye `linkedin` solo si tienes proxies; si no, déjalo en `[indeed]` para reducir ruido en los logs.
- `tecnoempleo.rss_urls`: pega las URLs RSS de tus búsquedas guardadas. El icono RSS aparece en la esquina superior derecha de cualquier resultado de búsqueda de Tecnoempleo.
- `infojobs.search_slugs`: la última parte de `https://www.infojobs.net/ofertas-trabajo/<slug>`.
- `hard_filters`: listas de keywords (case-insensitive) para descartar antes de llamar a la IA.

### Probar en local (sin Telegram)

```bash
python -m venv .venv
source .venv/bin/activate    # Windows: .venv/Scripts/activate
pip install -r requirements.txt
python -m job_agent --dry-run --log-level=DEBUG
```

`--dry-run` no envía a Telegram; imprime las ofertas por consola.

### Ejecutar los tests

```bash
pytest
```

## Despliegue con Docker

```bash
docker compose build
mkdir -p data    # volumen para SQLite + logs
docker compose run --rm agent --dry-run        # prueba
docker compose run --rm agent                  # ejecución real
```

El volumen `./data` persiste entre reinicios y contiene `jobs.db` y los
logs rotativos.

## Programación con cron

Hay dos opciones; se documentan ambas y la **(a) cron en el host** es la
opción por defecto: más simple, sin daemon adicional, integra con el logging
del sistema.

### (a) Cron del host (recomendado)

```bash
crontab -e
```

Añade:

```
# job-agent — 08:00 y 20:00 hora de Madrid (cron usa la TZ del sistema)
0 8,20 * * * cd /opt/job-agent && /usr/bin/docker compose run --rm agent >> /opt/job-agent/data/cron.log 2>&1
```

> Si la TZ del servidor no es `Europe/Madrid`, ajústala con
> `sudo timedatectl set-timezone Europe/Madrid` o usa los horarios UTC
> equivalentes.

### (b) Cron dentro del contenedor

Útil si no controlas el cron del host. Hay que añadir `cron` al `Dockerfile`
y arrancar el contenedor en modo daemon. Esqueleto:

```Dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends cron && rm -rf /var/lib/apt/lists/*
COPY crontab.txt /etc/cron.d/job-agent
RUN chmod 0644 /etc/cron.d/job-agent && crontab /etc/cron.d/job-agent
CMD ["cron", "-f"]
```

`crontab.txt`:

```
SHELL=/bin/bash
0 8,20 * * * cd /app && python -m job_agent >> /app/data/cron.log 2>&1
```

Y en `docker-compose.yml` cambiar a `restart: unless-stopped`.

## Cómo añadir una fuente

1. Crea `job_agent/collectors/mifuente.py` con una clase que herede de
   `Collector` e implemente `collect() -> list[JobOffer]`.
2. Construye `JobOffer` con `JobOffer.build(...)` — el ID se calcula a partir
   de la URL normalizada, así que el dedupe es automático.
3. Regístrala en `_build_collectors()` (en `main.py`).
4. Si necesita configuración o credenciales, añádelas a `config.yaml` y
   `.env.example`.

## Contingencia InfoJobs

El collector usa `requests` + parseo de JSON-LD. Si en producción te
encuentras este log:

```
ERROR infojobs blocked by anti-bot (Cloudflare?) for ...
```

Hay dos opciones, en orden de simplicidad:

1. **`curl_cffi`**: reemplaza `requests` por `curl_cffi.requests` y pasa
   `impersonate="chrome120"`. Suele sortear el JS challenge sin coste.
2. **Playwright**: instala `playwright`, abre la URL en un Chromium headless
   y devuelve `page.content()`. Más pesado pero también más robusto.

Documento de referencia: el HTML de listado tiene varios `<script
type="application/ld+json">` con `@type: "JobPosting"`. Una vez obtenido el
HTML, el parseo del collector vale tal cual.

## Coste estimado

Con los filtros duros agresivos y `gpt-4o-mini`, la factura típica por
ejecución es de **céntimos**: ~5-15 ofertas pasan al scorer por ejecución
(2 ejecuciones/día).

## Troubleshooting rápido

| Síntoma | Causa probable | Acción |
|---|---|---|
| Telegram falla con 400 | Markdown roto en un título | Mira el log, `_md_escape` debería capturar; reporta el caso. |
| LinkedIn 0 ofertas, Indeed OK | LinkedIn bloquea la IP | Configura `PROXY_LIST` o quita `linkedin` de `jobspy.sites`. |
| InfoJobs 0 ofertas | Cloudflare | Ver [Contingencia InfoJobs](#contingencia-infojobs). |
| `Missing required env var ...` | falta credencial en `.env` | Copia `.env.example` y rellena. |
| Cron no dispara a las 08:00 reales | TZ del host distinta a Madrid | `timedatectl set-timezone Europe/Madrid`. |
