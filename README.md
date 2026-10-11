# Coso Server

Piccolo servizio Flask pensato per ricevere testo e immagini e inoltrarli alla Responses API di OpenAI tramite LangChain. È eseguibile con Docker Compose in locale e con Gunicorn su un Raspberry Pi.

Per l'installazione completa su Raspberry Pi senza Docker, la configurazione di `systemd`, gli aggiornamenti e il troubleshooting, consulta [setup.md](setup.md).

Per aggiornare automaticamente il Raspberry a ogni push GitHub, consulta
[deploy.md](deploy.md): il servizio separato espone `POST /deploy`, verifica
la firma GitHub, aggiorna codice e dipendenze e riavvia `coso-server`.

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

Per gli esempi `curl`, carica la chiave dal tuo `.env` nella shell:

```bash
set -a
source .env
set +a
```

Verifica lo stato:

```bash
curl -H "X-API-Key: $COSO_API_KEY" http://localhost:8000/health
```

### Saluto iniziale casuale

`GET /hello` sceglie casualmente un file `.wav` da `data/wav/hello`
(oppure `WAV_OUTPUT_DIR/hello` se configurato) e restituisce un comando
con il suo URL assoluto; richiede l'header X-API-Key:

```bash
curl -H "X-API-Key: $COSO_API_KEY" http://localhost:8000/hello
```

```json
{
  "type": "command",
  "wav": "http://localhost:8000/wav/hello/ciao.wav"
}
```

I saluti sono scaricabili da `GET /wav/hello/<filename>` con `Content-Type: audio/wav`.
La scelta viene ripetuta a ogni richiesta e il comando non viene memorizzato in cache.
Puoi aggiungere saluti senza riavviare il server. Se mancano WAV, `/hello`
restituisce HTTP 503 con `error: hello_unavailable`.

I WAV generati da `/ask` vengono salvati in `data/wav` e serviti da `GET /wav/<filename>`. Se il server è dietro un reverse proxy, imposta `WAV_PUBLIC_BASE_URL` con l'origine pubblica, senza slash finale.

La sintesi usa Piper (`piper-tts`) con la voce italiana Serena high. Il modello
viene caricato alla prima sintesi e riutilizzato nelle richieste successive.
Puoi regolare `PIPER_LENGTH_SCALE` nel file `.env`: `1.0` usa la velocita
del modello, valori maggiori rallentano la voce e valori minori la accelerano.
Le vecchie variabili `ESPEAK_*` non sono piu usate.
Prima della sintesi, il server rimuove la formattazione Markdown più comune
(grassetto, corsivo, titoli, elenchi e delimitatori di codice), conservando il
contenuto e il testo dei link. Questo evita che la voce legga i simboli di
formattazione, per esempio «asterisco asterisco».

Ogni testo inviato a Piper viene preceduto dal prefisso `"... "`.

Prima di pubblicare il WAV, il server elabora l'audio con SoX usando questa
catena di effetti, nell'ordine indicato:

```text
norm -3 highpass 100 pitch -40 bass +2 compand 0.1,0.2 6:-60,-30,-10 -3 -90 0.1 echo 0.8 0.8 15 0.3 vol 0.5
```

Il WAV finale rimane PCM mono a 16 bit alla frequenza originale di Piper.
L'effetto finale `vol 0.5` dimezza l'ampiezza rispetto alla catena precedente
(circa -6 dB). La riduzione avviene dopo tutti gli effetti, per evitare che
`norm` compensi un volume piu basso impostato direttamente in Piper.
Si applica ai nuovi WAV sintetizzati; i file gia presenti non vengono modificati.
Solo il file elaborato e validato viene pubblicato; se Piper o SoX falliscono,
il server restituisce un errore e conserva l'eventuale WAV precedente.
I file temporanei vengono eliminati anche in caso di errore.
I saluti preregistrati non passano attraverso questa elaborazione.
Il log temporale `sox_processing` misura la fase SoX.

Per un Raspberry gia installato, aggiungi la dipendenza di sistema prima
di distribuire questa versione:

```bash
sudo apt update
sudo apt install -y sox
sox --version
```

Il webhook aggiorna le dipendenze Python, mentre SoX va installato con `apt`.
Docker lo include nell'immagine. `SOX_PATH` indica l'eseguibile (predefinito
`sox`); su Windows puoi impostare il percorso completo a `sox.exe`.

Per l'installazione nativa, dopo `pip install -r requirements.txt` scarica
il modello ONNX e il JSON corrispondente:

```bash
python scripts/download_piper_voice.py
```

Lo script scarica entrambi da una revisione fissa di
[committa/it_IT-serena-high](https://huggingface.co/committa/it_IT-serena-high/tree/c704759b2286f5f8c56eb4476169c4e2cda10b02)
in `data/voices`, esclusa da Git. Se i file sono gia presenti li riutilizza;
`--force` li riscarica, `--output-dir` cambia directory. La configurazione
deve chiamarsi `it_IT-serena-high.onnx.json` accanto al modello ONNX.
Docker scarica la voce durante la build e non richiede download all'avvio.
Le risposte rimangono WAV PCM mono a 16 bit, 22050 Hz, compatibili con Buddy.
I saluti preregistrati di `/hello` e `/wakeup` si aggiungono come prima.

### Saluto casuale al risveglio

`GET /wakeup` sceglie casualmente uno dei file `.wav` presenti in
`data/wav/wakeup` (oppure in `WAV_OUTPUT_DIR/wakeup` se configurato).
Richiede X-API-Key e restituisce un comando come `/hello`:

```json
{
  "type": "command",
  "wav": "http://localhost:8000/wav/wakeup/dimmi-tutto.wav"
}
```

Scarica quindi l'URL in `wav` per ricevere il file con `Content-Type: audio/wav`.
Puoi aggiungere altri saluti alla cartella senza riavviare il server.
Se la cartella manca o non contiene WAV, la route restituisce HTTP 503 con
`error: wakeup_unavailable`.

### Domanda vocale con risposta vocale

`POST /ask` accetta direttamente un WAV registrato dal microfono, lo trascrive con
`OpenAIWhisperParser`, inoltra il testo al modello e sintetizza la risposta in un
nuovo file WAV. Il formato richiesto è PCM mono, 16 bit little-endian, 16 kHz.

```bash
curl -H "X-API-Key: $COSO_API_KEY" -X POST http://localhost:8000/ask \
  -H "Content-Type: audio/wav" \
  --data-binary @domanda.wav
```

La risposta ha lo stesso formato di `/hello`:

```json
{
  "type": "command",
  "wav": "http://localhost:8000/wav/risposta-generata.wav"
}
```

## API

Il contratto completo delle route è disponibile in [`openapi.yaml`](openapi.yaml)
in formato OpenAPI 3.1.

### Testo

```bash
curl -H "X-API-Key: $COSO_API_KEY" -X POST http://localhost:8000/api/v1/ask \
  -H "Content-Type: application/json" \
  -d '{"text":"Perché il cielo è blu?"}'
```

### Immagine caricata

```bash
curl -H "X-API-Key: $COSO_API_KEY" -X POST http://localhost:8000/api/v1/ask \
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

## Memoria a lungo termine

Ogni messaggio testuale passa prima attraverso un router leggero con output
strutturato. Il router distingue i fatti durevoli (per esempio componenti di un
progetto, configurazioni domotiche o informazioni familiari) dalle interazioni
transitorie. Solo i fatti durevoli vengono sintetizzati e salvati in SQLite con
categoria, tag e timestamp.

Quando una domanda richiede informazioni precedenti, il router recupera fino a
`MEMORY_RESULT_LIMIT` fatti per categoria, tag o parola chiave. Questi vengono
aggiunti come contesto di sistema alla risposta. Non sono usati database vettoriali
e il server non mantiene una connessione SQLite residente in memoria.

Nel container il database è conservato nel volume Docker `memory-data`. In una
installazione diretta il percorso predefinito è `data/memory.sqlite3`.

## Configurazione

Le variabili principali sono documentate in `.env.example`.

- `OPENAI_API_KEY`: obbligatoria per le richieste AI.
- `OPENAI_BASE_URL`: base URL del servizio; normalmente non va modificata.
- `OPENAI_MODEL`: modello usato; predefinito `gpt-4.1-mini`.
- `TRANSCRIPTION_BACKEND`: `whistle` (predefinito, locale) oppure `openai`.
- `OPENAI_TRANSCRIPTION_MODEL`: modello speech-to-text usato solo con backend `openai`; predefinito `gpt-4o-mini-transcribe`.
- `MEMORY_ROUTER_MODEL`: modello economico per classificazione ed estrazione; predefinito `gpt-4o-mini`.
- `MEMORY_DB_PATH`: percorso del database SQLite persistente.
- `MEMORY_RESULT_LIMIT`: massimo numero di ricordi iniettati in una risposta; predefinito `6`.
- `COSO_API_KEY`: obbligatoria su tutte le route tramite `X-API-Key`, inclusi healthcheck e WAV; il server non parte se e vuota.
- `OPENAI_INSTRUCTIONS`: istruzioni generali date al modello.
- `OPENAI_MAX_OUTPUT_TOKENS`, `OPENAI_TIMEOUT_SECONDS`: limiti della chiamata upstream.
- `MAX_REQUEST_BYTES`, `MAX_AUDIO_BYTES`, `MAX_IMAGE_BYTES`, `MAX_TEXT_LENGTH`: limiti degli input.
- `PIPER_MODEL_PATH`: percorso del modello ONNX; predefinito `data/voices/it_IT-serena-high.onnx` nella directory del progetto.
- `PIPER_LENGTH_SCALE`: fattore di durata della voce, positivo e finito; predefinito `1.25`.

La chiave OpenAI non viene mai inviata al client e le richieste upstream usano
`store=false`. Coso Server non conserva immagini o registrazioni; salva localmente
solo le sintesi che il router identifica come memoria durevole. Le domande vocali
vengono trascritte localmente da Whistle (`cactus-needle==3.2.0`), in italiano;
con `TRANSCRIPTION_BACKEND=openai` si usa invece `OpenAIWhisperParser`
(`langchain-community`). In entrambi i casi il testo
passa quindi a `ChatOpenAI` (`langchain-openai`) usando la Responses API. Il parser
audio OpenAI richiede `ffmpeg`, già incluso nell'immagine Docker.

### Prova Whistle e ripristino

Questa variante mantiene lo stesso contratto `/ask` e la stessa autenticazione.
Whistle riceve campioni float32 ottenuti dal WAV PCM16 di Buddy; l'audio resta
in memoria. Il modello viene inizializzato alla prima trascrizione e riutilizzato.
Un lock per processo serializza le chiamate al runtime nativo, non thread-safe.
Gli errori e le trascrizioni vuote mantengono la risposta `502 transcription_error`;
non viene effettuato un fallback automatico verso OpenAI.

Alla prima chiamata Needle scarica pesi e runtime da Hugging Face: serve accesso
a Internet, anche se le trascrizioni successive sono locali. Nel container la
cache è conservata nel volume `whistle-cache` sotto `/home/coso/.cache`, scrivibile
dall'utente del servizio. La telemetria è disabilitata per impostazione predefinita.
Il binding Python richiede un sistema a 64 bit x86_64 o ARM64; sul Raspberry Pi
usare sistema operativo e Python a 64 bit.

Per attivare la prova nel container:

```bash
docker compose up -d --build coso-server
```

Per tornare al servizio precedente, impostare `TRANSCRIPTION_BACKEND=openai`
nel file `.env` e ricreare il container con `docker compose up -d coso-server`.
Per eliminare anche codice, dipendenza e configurazione Whistle, fare il revert
del commit dedicato. `OPENAI_API_KEY` resta necessaria per risposte e memoria.

La sonda seguente usa lo stesso adapter del server, senza chiamare OpenAI.
Il WAV deve essere PCM16 mono a 16000 Hz; il primo tempo include inizializzazione
ed eventuale download, i successivi misurano il modello già caricato:

```bash
python scripts/probe_whistle.py domanda.wav --runs 3
```

Prova locale dell'11 ottobre 2026: Windows x86_64, Python 3.14.2,
`cactus-needle 3.2.0`, engine Needle 3.3.0, pesi Whistle 2.0.0 (16.919.407 byte).
Tre WAV sintetici già presenti nel repository, ricampionati a 16 kHz, hanno dato:

| Audio | Durata | Trascrizione | Tempo a modello caricato |
| --- | --- | --- | --- |
| `hello/ciao.wav` | 0,809 s | Ciao. | 36 ms |
| `wakeup/come-posso-aiutarti.wav` | 1,685 s | Come posso aiutarti? | 77–89 ms |
| Prova voce Paola | 3,541 s | Ciao Giovanni, sono Coso. Questa è una prova della voce palla. | 214–225 ms |

Il primo caricamento, inclusi i download, ha richiesto 4,576 s. La sostituzione
di "Paola" con "palla" è un errore osservato. Questi risultati non misurano
il microfono di Buddy, il rumore ambientale o le prestazioni del Raspberry Pi;
la qualità va verificata con registrazioni reali prima di adottare la variante.
Fonti del progetto: [Whistle](https://huggingface.co/Cactus-Compute/whistle)
e [runtime Needle](https://github.com/cactus-compute/needle).

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

Per Piper e ONNX Runtime usa Raspberry Pi OS 64 bit su un Pi Zero 2 W o
un modello successivo. Il Pi Zero originale ARMv6 non e una piattaforma
supportata da questa installazione. Per avviare senza Docker:

```bash
sudo apt install python3-venv ffmpeg sox
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python scripts/download_piper_voice.py
gunicorn --bind 0.0.0.0:8000 --workers 1 --threads 2 --timeout 90 wsgi:app
```

Per esporre il servizio fuori dalla rete locale aggiungi HTTPS tramite un reverse proxy e mantieni sempre valorizzata `COSO_API_KEY`.

## Log temporali

I log INFO `[Timing]` riportano ID richiesta, timestamp `utc` e durate
in millisecondi calcolate con un orologio monotono. L'header opzionale
`X-Request-ID` permette di abbinare i log HTTP a quelli del firmware Buddy;
senza header il server genera un ID. Le richieste simultanee hanno ID separati.

Ogni richiesta registra `request_start` e `response_ready` con status e
`elapsed_ms`. Le fasi registrano `phase_start` e `phase_end` con `duration_ms`
ed esito: lettura body audio, validazione WAV, trascrizione, memoria,
classificazione della memoria, accesso SQLite, risposta AI e sintesi vocale.
La prima sintesi include anche la fase `piper_model_load`; nelle successive
il modello rimane in memoria.
La durata `memory` comprende classificazione e SQLite; la validazione audio
comprende la lettura del body. Le durate annidate non vanno sommate.

`response_ready` misura fino alla preparazione della risposta Flask, non
il completamento del trasferimento sulla rete: per i WAV confrontare anche
i log di download e riproduzione sul client. Nessun testo della domanda,
audio o credenziale viene aggiunto ai log temporali. Impostare `LOG_LEVEL=INFO`
per visualizzarli. Il comportamento e i formati delle API restano invariati.

Ogni trascrizione di `/ask` aggiunge un record INFO `[Transcription]`, con lo
stesso ID richiesta e timestamp UTC, seguito da un oggetto JSON. Il formato è
uguale per `TRANSCRIPTION_BACKEND=openai` e `TRANSCRIPTION_BACKEND=whistle`:

- `backend` e `model`: servizio e modello utilizzati.
- `wav_bytes`: dimensione effettiva del body WAV, header incluso.
- `audio_seconds`, `sample_rate_hz`, `channels`, `sample_width_bits`: durata e formato.
- `transcript`: testo completo trascritto, oppure `null` in caso di errore.
- `duration_ms`: tempo della trascrizione, inclusi inizializzazione del modello,
  eventuali download, attesa del lock locale o richiesta al servizio remoto.
- `rtf`: tempo di trascrizione diviso durata dell'audio; valori sotto `1`
  indicano elaborazione più veloce della durata della registrazione.
- `outcome`: `ok` oppure `error`; in caso di errore compare anche `error_type`.

Esempio di formato (tempi illustrativi):

```text
[Transcription] id=buddy-test-123 utc=2026-10-11T12:00:00.000+00:00 {"backend": "whistle", "model": "whistle", "wav_bytes": 6444, "audio_seconds": 0.2, "sample_rate_hz": 16000, "channels": 1, "sample_width_bits": 16, "transcript": "Ciao Coso", "outcome": "ok", "duration_ms": 50.0, "rtf": 0.25}
```

Il testo trascritto è registrato solo nei record `[Transcription]`; caratteri
di controllo e ritorni a capo sono escapati nel JSON, così ogni record resta
su una sola riga. Nessun audio binario o chiave viene aggiunto al record.
Per confrontare i backend usare lo stesso WAV, separando il primo caricamento
Whistle dalle richieste successive. Restano disponibili i tempi `[Timing]`
delle altre fasi e il tempo totale `response_ready`.
