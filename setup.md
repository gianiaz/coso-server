# Setup di Coso Server

Questa guida descrive l'installazione di Coso Server direttamente su Raspberry Pi OS, senza Docker, l'avvio automatico con `systemd` e la configurazione dell'ambiente locale di sviluppo.

## 1. Requisiti

- Raspberry Pi con Raspberry Pi OS e accesso alla rete.
- Python 3.9 o successivo.
- Una API key OpenAI.
- Accesso al terminale del Raspberry, direttamente oppure tramite SSH.

Verifica l'utente e l'indirizzo IP del Raspberry:

```bash
whoami
hostname -I
```

Gli esempi seguenti assumono:

- utente Linux: `pi`;
- directory del progetto: `/home/pi/coso-server`;
- porta HTTP: `8000`.

Se il tuo nome utente è diverso, sostituisci `pi` nei percorsi e nel servizio `systemd`.

## 2. Installa i pacchetti di sistema

```bash
sudo apt update
sudo apt install -y git python3 python3-pip python3-venv espeak-ng
```

Verifica che la voce italiana sia disponibile:

```bash
espeak-ng --voices | grep -i it
```

Puoi provare la sintesi vocale direttamente:

```bash
espeak-ng -v it "Ciao, sono Coso, come stai?" -w /tmp/coso-test.wav
ls -lh /tmp/coso-test.wav
```

## 3. Scarica il progetto

Se il repository è pubblico, sul Raspberry puoi clonarlo tramite HTTPS senza configurare una chiave SSH:

```bash
cd /home/pi
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
```

Non è necessario attivare la virtualenv: nei comandi di questa guida viene sempre usato il percorso completo di Python o Gunicorn.

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

COSO_API_KEY=una-password-lunga-e-casuale

WAV_OUTPUT_DIR=/home/pi/coso-server/data/wav
ESPEAK_EXECUTABLE=espeak-ng
ESPEAK_VOICE=it

# Imposta l'indirizzo raggiungibile dall'ESP, senza slash finale.
WAV_PUBLIC_BASE_URL=http://192.168.1.50:8000
```

Sostituisci `192.168.1.50` con l'indirizzo IP del Raspberry. Se `WAV_PUBLIC_BASE_URL` rimane vuoto, Coso Server costruisce l'URL usando l'host della richiesta ricevuta.

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
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/hello
```

Risposta attesa da `/health`:

```json
{"service":"coso-server","status":"ok"}
```

Risposta indicativa da `/hello`:

```json
{
  "type": "command",
  "wav": "http://192.168.1.50:8000/wav/ciao-sono-coso-come-stai.wav"
}
```

Verifica che il file sia scaricabile:

```bash
curl -o /tmp/coso-hello.wav \
  http://127.0.0.1:8000/wav/ciao-sono-coso-come-stai.wav
ls -lh /tmp/coso-hello.wav
```

Da un altro dispositivo nella stessa rete usa l'IP del Raspberry al posto di `127.0.0.1`.

Per provare l'endpoint OpenAI:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/ask \
  -H "Content-Type: application/json" \
  -H "X-API-Key: una-password-lunga-e-casuale" \
  -d '{"text":"Ciao, rispondi con una frase breve."}'
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
User=pi
Group=pi
WorkingDirectory=/home/pi/coso-server
EnvironmentFile=/home/pi/coso-server/.env
ExecStart=/home/pi/coso-server/.venv/bin/gunicorn --bind 0.0.0.0:8000 --workers 1 --threads 2 --timeout 90 --access-logfile - wsgi:app
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Se non usi l'utente `pi`, modifica `User`, `Group`, `WorkingDirectory`, `EnvironmentFile` ed `ExecStart`.

Attiva e avvia il servizio:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now coso-server
sudo systemctl status coso-server
```

Il servizio verrà avviato automaticamente a ogni accensione del Raspberry.

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

```bash
cd /home/pi/coso-server
git pull --ff-only
.venv/bin/pip install -r requirements.txt
sudo systemctl restart coso-server
sudo systemctl status coso-server
```

## 11. Permessi della directory WAV

Il servizio deve poter scrivere in `data/wav`. Se `/hello` restituisce un errore, correggi proprietario e permessi:

```bash
sudo mkdir -p /home/pi/coso-server/data/wav
sudo chown -R pi:pi /home/pi/coso-server/data/wav
chmod 755 /home/pi/coso-server/data/wav
```

## 12. Risoluzione dei problemi

### Il servizio non parte

```bash
sudo systemctl status coso-server --no-pager
journalctl -u coso-server -n 100 --no-pager
```

Controlla che tutti i percorsi nel file `coso-server.service` corrispondano all'utente e alla directory effettivi.

### `/hello` restituisce errore 500

Verifica `espeak-ng` e i permessi:

```bash
which espeak-ng
espeak-ng -v it "Prova audio" -w /home/pi/coso-server/data/wav/prova.wav
ls -lh /home/pi/coso-server/data/wav/prova.wav
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

Gli endpoint `/hello` e `/wav/...` sono pubblici per permettere all'ESP di usarli. L'endpoint `/api/v1/ask` è protetto da `COSO_API_KEY` quando questa variabile è valorizzata.

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

Per provare realmente `/hello`, installa anche `espeak-ng` sul computer di sviluppo.

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
