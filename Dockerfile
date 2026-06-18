FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Madrid

RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata ca-certificates xvfb xauth \
 && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 # Chromium + dependencias del sistema que necesita InfoJobs (Distil → navegador real).
 && python -m playwright install --with-deps chromium

COPY job_agent ./job_agent
COPY config.yaml ./config.yaml

# Persistent volume mounted at runtime: ./data (SQLite + logs)
VOLUME ["/app/data"]

# xvfb-run -a cuelga en Docker porque Xvfb no envía SIGUSR1 al padre en algunos
# kernels. Arrancamos Xvfb directamente y esperamos a que el socket esté listo.
COPY start.sh /start.sh
RUN chmod +x /start.sh
ENTRYPOINT ["/start.sh"]
