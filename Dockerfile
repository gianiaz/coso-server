FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --system coso && adduser --system --ingroup coso coso

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY --chown=coso:coso data ./data
COPY scripts ./scripts
RUN python scripts/download_piper_voice.py && chown -R coso:coso data/voices
COPY wsgi.py .

USER coso
EXPOSE 8000

CMD ["gunicorn", "--bind=0.0.0.0:8000", "--workers=1", "--threads=2", "--timeout=90", "--access-logfile=-", "wsgi:app"]
