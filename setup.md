# Setup di Coso Server

Questa guida descrive l'installazione di Coso Server direttamente su Raspberry Pi OS, senza Docker, l'avvio automatico con `systemd` e la configurazione dell'ambiente locale di sviluppo.

## 1. Requisiti

- Raspberry Pi Zero 2 W o successivo con Raspberry Pi OS 64 bit e accesso alla rete.
- Python 3.10 o successivo (Raspberry Pi OS Bookworm include Python 3.11).
- Una API key OpenAI.
- Accesso al terminale del Raspberry, direttamente oppure tramite SSH.

Verifica l'utente e l'indirizzo IP del Raspberry:

```bash
whoami
hostname -I
```

Gli esempi seguenti assumono:

- utente Linux: `giovanni`;
- directory del progetto: `/home/giovanni/coso-server`;
- porta HTTP: `8000`.

Se il tuo nome utente è diverso, sostituisci `giovanni` nei percorsi e nel servizio `systemd`.

## 2. Installa i pacchetti di sistema

```bash
sudo apt update
sudo apt install -y git python3 python3-pip python3-venv python3-dev ffmpeg sox
```

`python3-dev` fornisce gli header di Python necessari alla compilazione delle
dipendenze native; durante l'installazione sul server è stato necessario
installare anche questo pacchetto. Se usi una versione di Python diversa da
quella di sistema, installa il pacchetto di sviluppo corrispondente (per esempio
`python3.13-dev` per Python 3.13).

Il pacchetto `ffmpeg` include anche `ffprobe`: entrambi devono essere disponibili
per la trascrizione audio. Verifica l'installazione:

```bash
ffmpeg -version
ffprobe -version
sox --version
```

La sintesi vocale usa `piper-tts`, installato nei passi successivi, con la
voce italiana Serena high. Non serve installare il comando `espeak-ng`.

## 3. Scarica il progetto

Se il repository è pubblico, sul Raspberry puoi clonarlo tramite HTTPS senza configurare una chiave SSH:

```bash
cd /home/giovanni
git clone https://github.com/gianiaz/coso-server.git
cd coso-server
```

Se in futuro il repository diventa privato, configura una deploy key SSH sul Raspberry e usa invece:

```bash
git clone git@github.com:gianiaz/coso-server.git
```

## 4. Crea l'ambiente Python

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/download_piper_voice.py
.venv/bin/python scripts/download_whistle.py
```

Non è necessario attivare la virtualenv: nei comandi di questa guida viene sempre usato il percorso completo di Python o Gunicorn.

Esegui il download Whistle con lo stesso utente che avviera il servizio:
prepara pesi e runtime nella sua cache locale. Il download richiede Internet;
durante le trascrizioni il server forza invece la modalita offline e non
scarica file. Se usi una directory diversa con `--output-dir`, configura
`NEEDLE_WHISTLE_WEIGHTS` e `NEEDLE3_LIB_PATH` con i percorsi stampati dal comando.

Lo script scarica modello e configurazione in `data/voices` e riutilizza i
file se presenti. Per una prova di sintesi:

```bash
.venv/bin/python -m piper -m data/voices/it_IT-serena-high.onnx -f /tmp/coso-test.wav -- "Ciao, sono Coso, come stai?"
ls -lh /tmp/coso-test.wav
```

## 5. Configura l'applicazione

Crea il file locale `.env`:

```bash
cp .env.example .env
nano .env
```

Configurazione minima consigliata:

```dotenv
OPENAI_API_KEY=la-tua-api-key-openai
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4.1-mini
TRANSCRIPTION_BACKEND=whistle
OPENAI_TRANSCRIPTION_MODEL=gpt-4o-mini-transcribe

COSO_API_KEY=la-stessa-chiave-configurata-in-buddy

WAV_OUTPUT_DIR=/home/giovanni/coso-server/data/wav
PIPER_MODEL_PATH=/home/giovanni/coso-server/data/voices/it_IT-serena-high.onnx
PIPER_LENGTH_SCALE=1.25

# Imposta l'indirizzo raggiungibile dall'ESP, senza slash finale.
WAV_PUBLIC_BASE_URL=http://192.168.1.50:8000
```

Sostituisci `192.168.1.50` con l'indirizzo IP del Raspberry. Se `WAV_PUBLIC_BASE_URL` rimane vuoto, Coso Server costruisce l'URL usando l'host della richiesta ricevuta.

Usa una chiave casuale non vuota, identica a `BUDDY_COSO_API_KEY` nel file
privato `src/config/ServerSecrets.h` del firmware. Per generare una chiave:
`python3 -c "import secrets; print(secrets.token_hex(32))"`.

Proteggi il file, perché contiene credenziali:

```bash
chmod 600 .env
mkdir -p data/wav
```

Il file `.env` e i WAV generati sono esclusi da Git.

## 6. Avvio manuale di prova

Carica le variabili dal file `.env`:

```bash
set -a
source .env
set +a
```

Avvia Gunicorn:

```bash
.venv/bin/gunicorn \
  --bind 0.0.0.0:8000 \
  --workers 1 \
  --threads 2 \
  --timeout 90 \
  --access-logfile - \
  wsgi:app
```

La configurazione con un solo worker mantiene basso l'uso di memoria sul Raspberry Pi Zero. Lascia il processo aperto durante le prove; per fermarlo usa `Ctrl+C`.

## 7. Verifica gli endpoint

Dal Raspberry:

```bash
curl -H "X-API-Key: $COSO_API_KEY" http://127.0.0.1:8000/health
curl -H "X-API-Key: $COSO_API_KEY" http://127.0.0.1:8000/hello
```

Risposta attesa da `/health`:

```json
{"service":"coso-server","status":"ok"}
```

Risposta indicativa da `/hello`:

```json
{
  "type": "command",
  "wav": "http://192.168.1.50:8000/wav/hello/ciao.wav"
}
```

Verifica che il file sia scaricabile:

```bash
curl -H "X-API-Key: $COSO_API_KEY" -o /tmp/coso-hello.wav \
  http://127.0.0.1:8000/wav/hello/ciao.wav
ls -lh /tmp/coso-hello.wav
```

Da un altro dispositivo nella stessa rete usa l'IP del Raspberry al posto di `127.0.0.1`.

Per provare l'endpoint OpenAI:

```bash
curl -H "X-API-Key: $COSO_API_KEY" -X POST http://127.0.0.1:8000/api/v1/ask \
  -H "Content-Type: application/json" \
  -d '{"text":"Ciao, rispondi con una frase breve."}'
```

Per provare la stessa pipeline vocale usata dall'ESP32, prepara un WAV PCM mono,
16 bit, 16 kHz e invialo come body della richiesta:

```bash
curl -H "X-API-Key: $COSO_API_KEY" -X POST http://127.0.0.1:8000/ask \
  -H "Content-Type: audio/wav" \
  --data-binary @domanda.wav
```

## 8. Avvio automatico con systemd

Crea il servizio:

```bash
sudo nano /etc/systemd/system/coso-server.service
```

Inserisci:

```ini
[Unit]
Description=Coso Server
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=giovanni
Group=giovanni
WorkingDirectory=/home/giovanni/coso-server
EnvironmentFile=/home/giovanni/coso-server/.env
ExecStart=/home/giovanni/coso-server/.venv/bin/gunicorn --bind 0.0.0.0:8000 --workers 1 --threads 2 --timeout 90 --access-logfile - wsgi:app
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Se non usi l'utente `giovanni`, modifica `User`, `Group`, `WorkingDirectory`, `EnvironmentFile` ed `ExecStart`.

Attiva e avvia il servizio:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now coso-server
sudo systemctl status coso-server
```

Il servizio verrà avviato automaticamente a ogni accensione del Raspberry.

### Creare il servizio video `coso-camera.service`

Il relay video deve essere un servizio separato da `coso-server.service`, così
le connessioni video non occupano i thread delle domande vocali. **`systemctl`
crea le condizioni di avvio, avvia e gestisce il servizio; `journalctl` consulta
i suoi log.** I comandi seguenti vanno eseguiti sul Raspberry, senza Docker.

L'esempio usa l'utente e il gruppo `giovanni`, il repository in
`/home/giovanni/coso-server` e Python in `.venv/bin/python`. Se l'installazione
usa un altro utente o percorso, adeguare `User`, `Group`, `WorkingDirectory`,
`EnvironmentFile` ed `ExecStart`. L'utente del servizio deve poter leggere
`camera_relay.py` e il `.env`, e avviare quell'interprete Python. Il relay usa
solo la libreria standard: non richiede l'installazione di altri pacchetti.

Per attivare il buffer e la replica MJPEG, impostare nel `.env`:

```dotenv
CAMERA_STREAM_URL=http://192.168.1.137:81/stream
CAMERA_RELAY_URL=http://127.0.0.1:8001
CAMERA_FPS=5
CAMERA_MAX_AGE_SECONDS=3
CAMERA_MAX_FRAME_BYTES=524288
CAMERA_MAX_VIEWERS=2
CAMERA_SNAPSHOT_TIMEOUT_SECONDS=0.75
```

Nel `.env` deve essere già presente una `COSO_API_KEY` non vuota, identica a
quella usata da Buddy e dal servizio vocale. Il servizio legge questo file
tramite `EnvironmentFile`; non inserire la chiave direttamente nel `.service`.

Dal repository, installare il file fornito e aprirlo per verificare i percorsi:

```bash
cd /home/giovanni/coso-server
sudo cp deploy/coso-camera.service /etc/systemd/system/coso-camera.service
sudo nano /etc/systemd/system/coso-camera.service
```

In alternativa, creare direttamente quel file con `sudo nano`. Il contenuto
completo è:

```ini
[Unit]
Description=Coso Camera MJPEG Relay
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=giovanni
Group=giovanni
WorkingDirectory=/home/giovanni/coso-server
EnvironmentFile=/home/giovanni/coso-server/.env
ExecStart=/home/giovanni/coso-server/.venv/bin/python camera_relay.py
Restart=on-failure
RestartSec=5
# camera_relay.py imposta nice +10 prima di avviare i thread.
CPUWeight=10
MemoryMax=64M
TasksMax=32
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

`Restart=on-failure` riavvia il processo se termina con un errore; le
disconnessioni della ESPCam sono invece gestite dal relay stesso, senza
riavviare Coso. `CPUWeight=10` riduce la priorità relativa sotto contesa,
`MemoryMax=64M` limita la memoria e `TasksMax=32` limita processi/thread.
Il programma imposta anche `nice +10`. Questi limiti danno precedenza al
servizio vocale; le prestazioni vanno comunque misurate sul Raspberry Pi 3.

Verificare il file, ricaricare le unit e attivare il servizio anche al boot:

```bash
sudo systemd-analyze verify /etc/systemd/system/coso-camera.service
sudo systemctl daemon-reload
sudo systemctl enable --now coso-camera.service
sudo systemctl restart coso-server
sudo systemctl status coso-camera.service --no-pager
```

Il riavvio di `coso-server` gli fa leggere `CAMERA_RELAY_URL` dal `.env`.
Per avviare il relay senza abilitarlo al boot, usare invece
`sudo systemctl start coso-camera.service`.

### Log e gestione del servizio video con `journalctl`

Systemd raccoglie l'output del processo nel journal. Per seguire i nuovi log:

```bash
sudo journalctl -u coso-camera.service -f -o cat
```

Per leggere le ultime 100 righe o i log dall'ultimo avvio del Raspberry:

```bash
sudo journalctl -u coso-camera.service -n 100 --no-pager
sudo journalctl -u coso-camera.service -b --no-pager
```

Ctrl+C interrompe soltanto la visualizzazione dei log; il servizio continua
a funzionare. Per gestirlo:

```bash
sudo systemctl restart coso-camera.service
sudo systemctl stop coso-camera.service
sudo systemctl start coso-camera.service
sudo systemctl disable --now coso-camera.service
```

Dopo modifiche al `.service`, eseguire `sudo systemctl daemon-reload` e
`sudo systemctl restart coso-camera.service`. Dopo modifiche al codice video
o alle sue variabili nel `.env`, basta riavviare `coso-camera.service`;
riavviare anche Coso se sono cambiati URL, timeout o limiti del suo client camera.

Se lo stato è `failed`, controllare i log: chiave vuota, percorsi sbagliati,
permessi e porta 8001 già occupata impediscono l'avvio. Se il servizio è
`active` ma la camera è scollegata, nei log compaiono i tentativi di
riconnessione. Per confrontare il lavoro video con le domande vocali:

```bash
sudo journalctl -u coso-camera.service -u coso-server.service -f -o short-iso
```

Il relay usa la stessa `COSO_API_KEY`, priorità CPU inferiore e un limite di
memoria di 64 MiB; non carica i modelli di trascrizione o sintesi. La porta
8001 fornisce `/health`, `/snapshot` e `/stream`, tutti autenticati con
`X-API-Key`. Il campo `camera_ready` di `/health` indica se esiste un fotogramma
recente; lo stato del servizio da solo non garantisce che la ESPCam sia collegata.

Dal PC, `python scripts/view_camera.py --url http://192.168.1.104:8001`
apre il viewer nel browser e chiede la chiave senza inserirla nell'URL.
I dettagli del comportamento e delle frasi visive riconosciute sono nel
[README](README.md#telecamera-di-buddy).

## 9. Log e gestione del servizio

Segui i log in tempo reale:

```bash
journalctl -u coso-server -f
```

Altri comandi utili:

```bash
sudo systemctl restart coso-server
sudo systemctl stop coso-server
sudo systemctl start coso-server
sudo systemctl disable coso-server
```

Dopo aver modificato il file `.service`, esegui sempre:

```bash
sudo systemctl daemon-reload
sudo systemctl restart coso-server
```

## 10. Aggiornamento dell'applicazione

Per automatizzare questi aggiornamenti con un webhook GitHub firmato e un
servizio separato sul Raspberry, segui [deploy.md](deploy.md).

```bash
cd /home/giovanni/coso-server
git pull --ff-only
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/download_piper_voice.py
sudo systemctl restart coso-server
sudo systemctl status coso-server
```

## 11. Permessi della directory WAV

Il servizio deve poter scrivere in `data/wav` per sintetizzare le risposte di `/ask`
e leggere i saluti preregistrati in `data/wav/hello` e `data/wav/wakeup`.
Per correggere proprietario e permessi:

```bash
sudo mkdir -p /home/giovanni/coso-server/data/wav
sudo chown -R giovanni:giovanni /home/giovanni/coso-server/data/wav
chmod 755 /home/giovanni/coso-server/data/wav
```

## 12. Risoluzione dei problemi

### Il servizio non parte

```bash
sudo systemctl status coso-server --no-pager
journalctl -u coso-server -n 100 --no-pager
```

Controlla che tutti i percorsi nel file `coso-server.service` corrispondano all'utente e alla directory effettivi.

### `/hello` restituisce errore 503

Verifica che `data/wav/hello` (o `WAV_OUTPUT_DIR/hello`) contenga file `.wav`
leggibili dal servizio. L'errore JSON `hello_unavailable` indica che non sono
presenti saluti disponibili.

### `/ask` restituisce errore 502 durante la trascrizione

Se i log riportano `Couldn't find ffmpeg or avconv`,
`Couldn't find ffprobe or avprobe` oppure
`FileNotFoundError: [Errno 2] No such file or directory: 'ffprobe'`, mancano
i programmi di sistema necessari a leggere l'audio. In questo caso il problema
si verifica prima della chiamata di trascrizione a OpenAI.

Installa il pacchetto sul server e riavvia il servizio:

```bash
sudo apt update
sudo apt install -y ffmpeg
ffmpeg -version
ffprobe -version
sudo systemctl restart coso-server
journalctl -u coso-server -n 100 --no-pager
```

Riprova quindi a inviare l'audio a `/ask`.

### `/ask` restituisce errore 500 durante la sintesi

Verifica il modello Piper, il JSON accanto all'ONNX e i permessi della
directory WAV. Le vecchie variabili `ESPEAK_*` vanno sostituite con
`PIPER_MODEL_PATH` e `PIPER_LENGTH_SCALE`:

```bash
.venv/bin/python scripts/download_piper_voice.py
.venv/bin/python -m piper -m data/voices/it_IT-serena-high.onnx -f /home/giovanni/coso-server/data/wav/prova.wav -- "Prova audio"
ls -lh /home/giovanni/coso-server/data/wav/prova.wav
```

### L'ESP non riesce a scaricare il WAV

- Non usare `localhost` o `127.0.0.1` come URL per l'ESP.
- Imposta `WAV_PUBLIC_BASE_URL` con l'IP del Raspberry raggiungibile dalla rete.
- Verifica dal computer o dall'ESP che `http://IP-RASPBERRY:8000/health` sia raggiungibile.
- Controlla che client e Raspberry siano sulla stessa rete e che la porta `8000` non sia bloccata.

Per verificare che Gunicorn ascolti sulla porta corretta:

```bash
sudo ss -ltnp | grep 8000
```

### `/api/v1/ask` restituisce errore 502

Controlla nei log che `OPENAI_API_KEY`, `OPENAI_MODEL` e la connessione Internet siano validi:

```bash
journalctl -u coso-server -n 100 --no-pager
```

## 13. Sicurezza di rete

Tutte le route richiedono `X-API-Key`, inclusi `/health`, `/hello`, `/wakeup`,
`/ask`, `/api/v1/ask` e tutti i WAV. Anche HEAD e OPTIONS richiedono la chiave.
Senza chiave o con chiave errata il server restituisce HTTP 401 prima di leggere
il body o avviare i servizi. Una `COSO_API_KEY` vuota impedisce l'avvio del server.
Buddy deve usare lo stesso valore in `src/config/ServerSecrets.h`; dopo averlo
configurato occorre ricompilare e caricare il firmware.

Per l'uso nella sola rete locale puoi lasciare il servizio sulla porta `8000`. Se devi esporlo su Internet, non pubblicare direttamente Gunicorn: usa HTTPS tramite un reverse proxy o un tunnel sicuro.

## 14. Setup locale per lo sviluppo

### Python nativo

```bash
python -m venv .venv
```

PowerShell:

```powershell
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pytest
```

Linux o macOS:

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

Per provare `/hello`, aggiungi file `.wav` in `data/wav/hello`.
Per la sintesi delle risposte di `/ask`, esegui anche
`python scripts/download_piper_voice.py`. I test usano una voce simulata
e non richiedono di scaricare il modello.

### Docker Compose locale

Docker è previsto solo come alternativa per lo sviluppo locale:

```bash
cp .env.example .env
docker compose up --build
```

Su PowerShell:

```powershell
Copy-Item .env.example .env
docker compose up --build
```
