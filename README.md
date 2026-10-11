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
viene precaricato all'avvio del processo server e riutilizzato nelle richieste
successive. Se il modello o il JSON non sono disponibili, il processo non
completa l'avvio: scarica la voce scelta prima di riavviare il servizio.
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
`--force` li riscarica, `--output-dir` cambia directory. Ogni configurazione
deve chiamarsi come il modello ONNX con il suffisso aggiuntivo `.json`.

Sono disponibili anche `it_IT-serena-medium` e `it_IT-riccardo-x_low`, dal
repository `rhasspy/piper-voices` (revisione `main`). Per scaricare entrambe:

```bash
.venv/bin/python scripts/download_piper_voice.py --voice it_IT-serena-medium --voice it_IT-riccardo-x_low
# Per tutte e tre le voci:
.venv/bin/python scripts/download_piper_voice.py --all
```

Senza opzioni lo script continua a scaricare solo Serena high. Per selezionare
una voce scaricata, imposta nel `.env`, per esempio:

```text
PIPER_MODEL_PATH=data/voices/it_IT-serena-medium.onnx
```

Riavvia il servizio dopo il cambio; lo script di download non cambia la voce
configurata nel server.
Docker scarica la voce durante la build e non richiede download all'avvio.
Le risposte rimangono WAV PCM mono a 16 bit alla frequenza del modello scelto,
compatibili con Buddy.
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

Solo i messaggi che contengono una parola chiave della memoria passano attraverso
un router leggero con output
strutturato. Il router distingue i fatti durevoli (per esempio componenti di un
progetto, configurazioni domotiche o informazioni familiari) dalle interazioni
transitorie. Solo i fatti durevoli vengono sintetizzati e salvati in SQLite con
categoria, tag e timestamp.

`MEMORY_ACTIVATION_KEYWORDS` contiene parole o frasi separate da virgole:
il valore predefinito è `ricorda,ricordati,ricordi,memorizza,memoria`.
Il confronto ignora le maiuscole e rispetta i confini delle parole:
`memorizzazione` non attiva `memorizza`. Le frasi ammettono spazi multipli.
Per salvare: «Ricorda che nel progetto uso una ESP32-S3».
Per recuperare: «Ti ricordi quale scheda uso nel progetto?».
Senza parole chiave non si classifica, non si legge e non si salva la memoria,
anche se il messaggio contiene un fatto personale. Il testo completo viene
comunque inviato al modello della risposta. I ricordi già salvati restano nel
database. Una lista vuota disattiva l'attivazione della memoria.

Il riepilogo INFO `[Memory]`, correlato all'ID richiesta, riporta modello,
eventuale motivo del bypass (`no_keyword` quando manca una parola chiave),
`keyword_triggered`, numero di caratteri, durata della classificazione,
durata del modello misurata tramite callback, token quando disponibili, decisioni
`should_save`/`needs_context`, numero di ricordi e risultato del salvataggio.
Il tempo del modello include SDK, rete e servizio remoto: non misura separatamente
la sola inferenza sul server OpenAI. La classificazione include anche la pipeline
di output strutturato; `memory_response_processing` misura la validazione finale
e la normalizzazione locale. Non vengono registrati testo, riassunti o tag.
`memory_search` e `memory_save` compaiono solo se le operazioni vengono eseguite.

Quando una domanda richiede informazioni precedenti, il router recupera fino a
`MEMORY_RESULT_LIMIT` fatti per categoria, tag o parola chiave. Questi vengono
aggiunti come contesto di sistema alla risposta. Non sono usati database vettoriali
e il server non mantiene una connessione SQLite residente in memoria.

Nel container il database è conservato nel volume Docker `memory-data`. In una
installazione diretta il percorso predefinito è `data/memory.sqlite3`.

Per vedere tutti i ricordi salvati, dal repository:

```bash
python3 scripts/show_memory.py
```

Lo script usa solo la libreria standard Python e apre SQLite in sola lettura.
Mostra ID, categoria, data, tag e riassunto, dal più recente. Legge
`MEMORY_DB_PATH` dall'ambiente, oppure dal `.env` del repository; altrimenti usa
`data/memory.sqlite3`. I percorsi configurati relativi sono risolti dal repository.
Puoi specificare un database diverso con `--db /percorso/memory.sqlite3` oppure
ottenere JSON con `--json`. Se il database è vuoto mostra zero ricordi; se manca,
segnala un errore senza crearne uno nuovo.

## Configurazione

Le variabili principali sono documentate in `.env.example`.

- `OPENAI_API_KEY`: obbligatoria per le richieste AI.
- `OPENAI_BASE_URL`: base URL del servizio; normalmente non va modificata.
- `OPENAI_MODEL`: modello usato; predefinito `gpt-4.1-mini`.
- `TRANSCRIPTION_BACKEND`: `whistle` (predefinito, locale) oppure `openai`.
- `OPENAI_TRANSCRIPTION_MODEL`: modello speech-to-text usato solo con backend `openai`; predefinito `gpt-4o-mini-transcribe`.
- `MEMORY_ROUTER_MODEL`: modello economico per classificazione ed estrazione; predefinito `gpt-4o-mini`.
- `MEMORY_ACTIVATION_KEYWORDS`: parole/frasi di attivazione separate da virgole; predefinito `ricorda,ricordati,ricordi,memorizza,memoria`.
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
in memoria. Il modello viene precaricato all'avvio e riutilizzato già dalla prima
trascrizione. Il preload usa esclusivamente pesi e runtime locali: se mancano,
l'avvio fallisce con un errore che indica `scripts/download_whistle.py`.
Non effettua una trascrizione di prova e non scarica file dalla rete.
La conversione PCM16 little-endian in campioni float32 usa NumPy per evitare
un ciclo Python per ogni campione, mantenendo la stessa scala e gli stessi valori.
Un lock per processo serializza le chiamate al runtime nativo, non thread-safe.
Gli errori e le trascrizioni vuote mantengono la risposta `502 transcription_error`;
non viene effettuato un fallback automatico verso OpenAI.

Whistle funziona solo con pesi e runtime gia presenti: il server forza
`HF_HUB_OFFLINE=1` e disabilita la telemetria di Needle e Hugging Face.
Non scarica file durante le richieste e non contatta il Hub per registrare
l'utilizzo del modello. Se mancano i file locali, restituisce un errore di
trascrizione senza fallback remoto.

Per un'installazione nativa, scarica i file una volta, prima di avviare il
server, usando lo stesso utente del servizio (questo comando richiede Internet):

```bash
.venv/bin/python scripts/download_whistle.py
```

Il comando usa la cache Needle dell'utente. Puoi invece usare `--output-dir`
e impostare `NEEDLE_WHISTLE_WEIGHTS` e `NEEDLE3_LIB_PATH` con i percorsi dei
due file stampati dal comando. Il runtime deve corrispondere al sistema e
all'architettura del server.
Nel container i file vengono scaricati durante la build in `/app/data/whistle`;
le richieste usano questi file locali, senza dipendere dal volume della cache.
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
dei file locali; i successivi misurano il modello già caricato:

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

Ogni richiesta registra `request_start` all'arrivo e `response_ready` con
lo status finale. Tutti gli eventi includono ID richiesta, timestamp UTC e
`elapsed_ms` dall'arrivo della richiesta. Ogni fase registra `phase_start`
prima di iniziare e `phase_end` con `duration_ms` ed esito `ok` o `error`.

| Fase | Cosa misura |
| --- | --- |
| `audio_body_read`, `audio_validation` | Ricezione del body e verifica del WAV |
| `transcription` | Trascrizione complessiva |
| `transcription_pcm_conversion` | Conversione PCM16 in float32 per Whistle |
| `whistle_lock_wait` | Attesa di altre trascrizioni nello stesso processo |
| `whistle_preload`, `whistle_model_load` | Precaricamento locale di Whistle all'avvio |
| `whistle_inference` | Trascrizione eseguita dal modello locale |
| `memory`, `memory_classification`, `memory_response_processing`, `memory_sqlite`, `memory_search`, `memory_save` | Router della memoria, normalizzazione, ricerca e salvataggio |
| `answer_generation` | Generazione complessiva della risposta AI |
| `openai_request` | Invio dei messaggi a OpenAI e attesa della risposta |
| `openai_response_processing` | Estrazione del testo e dei metadati dalla risposta |
| `speech_generation` | Generazione complessiva del WAV |
| `speech_lock_wait` | Attesa di altre sintesi nello stesso processo |
| `piper_model_load` | Primo caricamento della voce Piper |
| `piper_synthesis` | Sintesi vocale e scrittura del WAV grezzo |
| `piper_wav_validation` | Verifica del WAV grezzo |
| `sox_processing` | Elaborazione degli effetti e riduzione del volume |
| `wav_publication` | Verifica e pubblicazione del WAV finale |

Piper registra `piper_preload`, `piper_model_load` e `[PiperRuntime]` all'avvio,
con ID `-` perche non e ancora presente una richiesta HTTP. L'attesa di
caricamento viene spostata al riavvio del servizio; non esegue una sintesi
di riscaldamento e non elimina il costo dell'inferenza della prima domanda.
Con Gunicorn il modello viene precaricato in ogni worker; la configurazione
del progetto usa un solo worker e non richiede `--preload` di Gunicorn.
I caricamenti dei modelli compaiono solo quando necessari; i modelli restano
in memoria nelle richieste successive. `openai_request` include anche il
trasporto e il decoding effettuato dal client, non misura solo il calcolo remoto.
La durata `memory` comprende classificazione e SQLite; la validazione audio
comprende la lettura del body. Le durate annidate non vanno sommate.

`response_ready` misura fino alla preparazione della risposta Flask, non
il completamento del trasferimento sulla rete: per i WAV confrontare anche
i log di download e riproduzione sul client. Nessun testo della domanda,
audio o credenziale viene aggiunto ai log temporali. Impostare `LOG_LEVEL=INFO`
per visualizzarli. Il comportamento e i formati delle API restano invariati.

Il livello INFO viene applicato esplicitamente anche se Gunicorn ha gia
inizializzato il logging. Per seguire gli eventi in tempo reale:

```bash
journalctl -u coso-server -f -o cat
# Oppure, con Docker:
docker compose logs -f coso-server
```

Esempio di una fase (tempi illustrativi):

```text
[Timing] id=buddy-test-123 utc=2026-10-11T12:00:00.000+00:00 event=phase_start phase=openai_request elapsed_ms=420.0
[Timing] id=buddy-test-123 utc=2026-10-11T12:00:01.200+00:00 event=phase_end phase=openai_request outcome=ok duration_ms=1200.0 elapsed_ms=1620.0
```

Ogni trascrizione di `/ask` aggiunge un record INFO `[Transcription]`, con lo
stesso ID richiesta e timestamp UTC, seguito da un oggetto JSON. Il formato è
uguale per `TRANSCRIPTION_BACKEND=openai` e `TRANSCRIPTION_BACKEND=whistle`:

- `backend` e `model`: servizio e modello utilizzati.
- `wav_bytes`: dimensione effettiva del body WAV, header incluso.
- `audio_seconds`, `sample_rate_hz`, `channels`, `sample_width_bits`: durata e formato.
- `transcript`: testo completo trascritto, oppure `null` in caso di errore.
- `duration_ms`: tempo della trascrizione, inclusi inizializzazione del modello,
  attesa del lock locale o richiesta al servizio remoto. Whistle non scarica
  file durante la trascrizione.
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

Al primo caricamento di Piper, il record INFO `[PiperRuntime]` riporta il
percorso del modello effettivamente caricato (per esempio `it_IT-serena-high`),
modello della scheda, architettura, bit di Python, CPU visibili e CPU consentite
dall'affinita del processo. Include provider ONNX, `intra_op_num_threads`,
`inter_op_num_threads`, modalita di esecuzione e ottimizzazione del grafo.
Il valore `0` per i thread indica la scelta automatica di ONNX Runtime, non
zero thread e non il conteggio dei thread effettivamente attivi.

Ogni generazione aggiunge un record INFO `[Speech]` con lo stesso ID richiesta:

- `model`, `length_scale`, `text_characters`: modello, velocita e lunghezza del testo, senza registrarne il contenuto.
- `model_loaded_this_request`: distingue la prima richiesta dalle successive.
- `piper_duration_ms`, `audio_seconds`, `piper_rtf`: tempo di sintesi, durata del WAV grezzo e rapporto tra i due. Un RTF di `3` significa tre secondi di elaborazione per ogni secondo di audio.
- `final_audio_seconds`, `wav_bytes`, `sample_rate_hz`: durata, dimensione e frequenza del WAV pubblicato dopo SoX.
- `generation_duration_ms`: tempo complessivo, inclusi attesa del lock, caricamento, sintesi e SoX; le durate annidate non vanno sommate.
- `cpu_temperature_c`, `cpu_temperature_end_c`, `cpu_frequency_mhz`: temperatura a inizio/fine e frequenza osservata all'inizio. I valori sono `null` se il sistema non espone questi dati.
- `outcome`, `error_type`: esito e tipo dell'eventuale errore; anche le generazioni fallite producono il record.

Sul Raspberry Pi 3 questi dati consentono di confrontare Serena high con
altre voci e distinguere il caricamento iniziale dalla sintesi. Temperatura
e frequenza sono osservazioni istantanee e da sole non dimostrano throttling.
