# Coso Server

Piccolo servizio Flask pensato per ricevere testo e immagini e inoltrarli alla Responses API di OpenAI. È eseguibile con Docker Compose in locale e con Gunicorn su un Raspberry Pi.

Per l'installazione completa su Raspberry Pi senza Docker, la configurazione di `systemd`, gli aggiornamenti e il troubleshooting, consulta [setup.md](setup.md).

## Avvio locale con Docker

Requisiti: Docker con il plugin Compose e una API key OpenAI.

```bash
cp .env.example .env
# modifica .env e inserisci OPENAI_API_KEY e COSO_API_KEY
docker compose up --build
```

Su PowerShell, il primo comando equivalente è:

```powershell
Copy-Item .env.example .env
```

Verifica lo stato:

```bash
curl http://localhost:8000/health
```

### Hello e sintesi vocale

`GET /hello` genera con `espeak-ng` la frase `Ciao, sono Coso, come stai?` e restituisce all'ESP un comando con l'URL pubblico del WAV:

```bash
curl http://localhost:8000/hello
```

```json
{
  "type": "command",
  "wav": "http://localhost:8000/wav/ciao-sono-coso-come-stai.wav"
}
```

I file vengono salvati in `data/wav` e serviti da `GET /wav/<filename>`. Se il server è dietro un reverse proxy, imposta `WAV_PUBLIC_BASE_URL` con l'origine pubblica, senza slash finale.

## API

### Testo

```bash
curl -X POST http://localhost:8000/api/v1/ask \
  -H "Content-Type: application/json" \
  -H "X-API-Key: cambia-questa-chiave" \
  -d '{"text":"Perché il cielo è blu?"}'
```

### Immagine caricata

```bash
curl -X POST http://localhost:8000/api/v1/ask \
  -H "X-API-Key: cambia-questa-chiave" \
  -F "text=Che cosa vedi in questa immagine?" \
  -F "image=@foto.jpg"
```

Sono accettati JPEG, PNG, WEBP e GIF. Il limite predefinito per una singola immagine è 5 MiB.

### Immagine via JSON

Si può inviare `image_url` con un URL HTTP/HTTPS pubblico, oppure `image_base64` insieme a `image_mime_type`:

```json
{
  "text": "Che cosa vedi?",
  "image_url": "https://example.com/foto.jpg"
}
```

Risposta:

```json
{
  "answer": "...",
  "model": "gpt-4.1-mini",
  "response_id": "resp_..."
}
```

## Configurazione

Le variabili principali sono documentate in `.env.example`.

- `OPENAI_API_KEY`: obbligatoria per le richieste AI.
- `OPENAI_BASE_URL`: base URL del servizio; normalmente non va modificata.
- `OPENAI_MODEL`: modello usato; predefinito `gpt-4.1-mini`.
- `COSO_API_KEY`: protegge l'endpoint tramite header `X-API-Key`. In produzione non lasciarla vuota.
- `OPENAI_INSTRUCTIONS`: istruzioni generali date al modello.
- `OPENAI_MAX_OUTPUT_TOKENS`, `OPENAI_TIMEOUT_SECONDS`: limiti della chiamata upstream.
- `MAX_REQUEST_BYTES`, `MAX_IMAGE_BYTES`, `MAX_TEXT_LENGTH`: limiti degli input.

La chiave OpenAI non viene mai inviata al client. Le richieste usano `store=false`; Coso Server non salva localmente testi o immagini. Il collegamento a OpenAI usa il protocollo HTTP della Responses API direttamente, evitando dipendenze native pesanti sul Raspberry Pi.

## Test

I test non effettuano chiamate reali a OpenAI:

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pytest
```

## Raspberry Pi Zero

La procedura completa consigliata è documentata in [setup.md](setup.md).

Su Raspberry Pi Zero 2 W, usa Raspberry Pi OS 64 bit e avvia lo stesso `compose.yaml` con `docker compose up -d --build`.

Il Raspberry Pi Zero originale usa ARMv6: le immagini Docker Python moderne possono non essere disponibili per questa architettura. In quel caso installa Python, `espeak-ng` e le dipendenze direttamente su Raspberry Pi OS, poi esegui Gunicorn:

```bash
sudo apt install python3-venv espeak-ng
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
gunicorn --bind 0.0.0.0:8000 --workers 1 --threads 2 --timeout 90 wsgi:app
```

Per esporre il servizio fuori dalla rete locale aggiungi HTTPS tramite un reverse proxy e mantieni sempre valorizzata `COSO_API_KEY`.
