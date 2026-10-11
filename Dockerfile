FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg sox \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --system coso && adduser --system --ingroup coso --home /home/coso coso \
    && mkdir -p /home/coso/.cache && chown -R coso:coso /home/coso

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY --chown=coso:coso data ./data
COPY scripts ./scripts
RUN python scripts/download_piper_voice.py && chown -R coso:coso data/voices
RUN python scripts/download_whistle.py --output-dir data/whistle \
    && chown -R coso:coso data/whistle
ENV NEEDLE_WHISTLE_WEIGHTS=/app/data/whistle/whistle.cact \
    NEEDLE3_LIB_PATH=/app/data/whistle/libneedle.so \
    HF_HUB_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    NEEDLE_TELEMETRY=0 \
    DO_NOT_TRACK=1
COPY wsgi.py .

USER coso
EXPOSE 8000

CMD ["gunicorn", "--bind=0.0.0.0:8000", "--workers=1", "--threads=2", "--timeout=90", "--access-logfile=-", "wsgi:app"]
