# Deploy automatico da GitHub su Raspberry Pi

Il servizio indipendente `coso-deploy` espone soltanto `POST /deploy`, sulla
porta locale `9000`. Verifica la firma `X-Hub-Signature-256` sul body originale
con un segreto condiviso con GitHub: chi non conosce il segreto non può creare
una firma valida. Non usa `COSO_API_KEY` e non modifica le API di Coso Server.
Il segreto deve rimanere riservato e diverso dalle altre chiavi.

Accetta solo il repository configurato e i push sul branch configurato;
risponde al `ping` di GitHub e ignora altri eventi, branch e cancellazioni.
Risponde `202` appena il lavoro viene accodato, poi esegue in sequenza:

1. Verifica che il checkout sia sul branch atteso e non abbia modifiche locali.
2. `git pull --ff-only origin main` (oppure il branch configurato).
3. `.venv/bin/python -m pip install -r requirements.txt`.
4. `sudo -n /usr/bin/systemctl restart coso-server.service`.

Un errore interrompe la sequenza e viene registrato nel journal. Non vengono
eseguiti reset, merge automatici o rollback. Il pull e pip modificano la stessa
installazione del servizio: non è un deploy atomico e un errore di pip può
lasciare le dipendenze parzialmente aggiornate. Il servizio non viene riavviato
se un passaggio precedente fallisce. Questo flusso segue l'installazione
nativa di [setup.md](setup.md), non quella Docker.

## Installazione

Gli esempi assumono utente `giovanni`, checkout `/home/giovanni/coso-server`, branch `main`
e servizio esistente `coso-server.service`. Sostituisci utente e percorsi nei
file e nei comandi se la tua installazione è diversa.

Il webhook usa una virtualenv separata in `.venv-deploy`, ignorata da Git, così pip non
modifica le dipendenze del processo che riceve i webhook durante il deploy:

```bash
cd /home/giovanni/coso-server
python3 -m venv /home/giovanni/coso-server/.venv-deploy
/home/giovanni/coso-server/.venv-deploy/bin/python -m pip install -r deploy/requirements.txt
sudo install -o root -g root -m 600 deploy/webhook.env.example /etc/coso-deploy.env
python3 -c "import secrets; print(secrets.token_hex(32))"
sudo nano /etc/coso-deploy.env
```

Incolla il segreto generato in `DEPLOY_WEBHOOK_SECRET` e verifica
`DEPLOY_REPOSITORY`, `DEPLOY_BRANCH` e `DEPLOY_DIRECTORY`. Il file viene letto
da systemd; lo script non carica automaticamente file `.env`.

Git e pip vengono eseguiti come `giovanni`; il checkout e `.venv` devono essere
scrivibili da questo utente. Gli eventuali file locali nella directory devono
essere ignorati da Git: altrimenti il controllo del checkout blocca il deploy.
Per un repository privato configura una deploy key SSH di sola lettura e
`known_hosts` per l'utente del servizio. Verifica il pull senza prompt:

```bash
sudo -u giovanni env GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND='/usr/bin/ssh -oBatchMode=yes' git -C /home/giovanni/coso-server ls-remote origin
```

Concedi soltanto il permesso di riavviare Coso Server, senza password:

```bash
sudo visudo -cf deploy/coso-deploy.sudoers
sudo install -o root -g root -m 440 deploy/coso-deploy.sudoers /etc/sudoers.d/coso-deploy
sudo visudo -c
sudo -u giovanni sudo -n -l /usr/bin/systemctl restart coso-server.service
sudo install -o root -g root -m 644 deploy/coso-deploy.service /etc/systemd/system/coso-deploy.service
sudo systemctl daemon-reload
sudo systemctl enable --now coso-deploy.service
sudo systemctl status coso-deploy.service
```

Mantieni **un solo worker Gunicorn**, senza `--preload` o `--reload`: la coda
è interna al processo. Push simultanei vengono accorpati; se arrivano durante
un deploy, viene effettuato un nuovo passaggio al termine per ottenere anche
l'ultimo aggiornamento. Il checkout viene aggiornato all'ultimo stato remoto,
non necessariamente al singolo commit del payload. Una riconsegna può ripetere
il deploy. La coda non è persistente: se `coso-deploy` viene riavviato o il Pi
si spegne, riconsegna l'evento da GitHub dopo aver verificato i log.

`NoNewPrivileges=true` non va aggiunto a questo servizio perché impedirebbe
il comando sudo autorizzato. `coso-server` può mantenere la sua configurazione.
Chi può scrivere nel repository e nei requirements può eseguire codice come
l'utente del servizio: limita i permessi di push sul branch di produzione.

## URL pubblico e webhook GitHub

GitHub deve raggiungere un URL HTTPS pubblico; `192.168.x.x` non è raggiungibile
da Internet. Configura un reverse proxy HTTPS o un tunnel verso
`http://127.0.0.1:9000/deploy`, lasciando la porta `9000` locale. Il proxy deve
conservare body e header GitHub. Esempio di location in un server Nginx già
configurato con dominio e certificato TLS:

```nginx
location = /deploy {
    client_max_body_size 25m;
    proxy_pass http://127.0.0.1:9000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Su GitHub, repository → **Settings → Webhooks → Add webhook**:

- Payload URL: `https://tuo-dominio/deploy`.
- Content type: `application/json`.
- Secret: lo stesso valore di `DEPLOY_WEBHOOK_SECRET`.
- SSL verification: abilitata.
- Eventi: **Just the push event**.
- Active: abilitato.

Il primo ping deve ricevere `200`. Un push sul branch configurato deve
ricevere `202`; questo significa accettazione, non completamento del deploy.
Controlla l'esito sul Raspberry:

```bash
journalctl -u coso-deploy.service -f
sudo systemctl status coso-server.service
journalctl -u coso-server.service -n 50 --no-pager
```

Per firme mancanti o errate la risposta è `401`; per un repository diverso
è `403`. Gli eventi ignorati ricevono `200`. Dopo un errore correggi la causa
e usa **Recent Deliveries → Redeliver** su GitHub. Dopo un aggiornamento dello
script webhook o della sua configurazione, riavvia manualmente `coso-deploy`;
le sue dipendenze si aggiornano con la virtualenv separata indicata sopra.

Riferimenti: [verifica delle firme GitHub](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)
e [risposte rapide ai webhook](https://docs.github.com/en/webhooks/using-webhooks/best-practices).
