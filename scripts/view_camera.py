"""Open the authenticated LAN camera in a browser via a loopback-only proxy.

Uses only Python's standard library; run on the PC where you want to watch.
The API key stays in this process, outside browser storage and URLs.
"""

import argparse
import getpass
import os
import sys
import webbrowser
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from camera_relay import local_opener


PAGE = b"""<!doctype html><html lang="it"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gli occhi di Buddy</title>
<style>body{background:#18212a;color:#fff;font:18px system-ui;text-align:center}
img{max-width:95vw;max-height:80vh}p{min-height:1.5em}</style>
<h1>Gli occhi di Buddy</h1><p id="status">Collegamento...</p><img id="video">
<script>
let previous;
async function update(){
  try {
    const response=await fetch('/snapshot',{cache:'no-store',signal:AbortSignal.timeout(2000)});
    if(!response.ok) throw new Error('Telecamera non disponibile');
    const next=URL.createObjectURL(await response.blob());
    document.getElementById('video').src=next;
    if(previous) URL.revokeObjectURL(previous);
    previous=next;
    document.getElementById('status').textContent='In diretta';
  } catch(error){
    document.getElementById('video').removeAttribute('src');
    document.getElementById('status').textContent='Telecamera non disponibile';
  }
  setTimeout(update,200);
}
update();
</script></html>"""


class Viewer(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(3)

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.headers.get('Host') != f"127.0.0.1:{self.server.server_port}":
            self.send_error(403)
            return
        if self.path == '/':
            status, mime, data = 200, 'text/html; charset=utf-8', PAGE
        elif self.path == '/snapshot':
            try:
                req = Request(self.server.relay_url + '/snapshot', headers={'X-API-Key': self.server.api_key})
                with local_opener().open(req, timeout=1) as response:
                    if not 200 <= response.status < 300 or response.headers.get_content_type() != 'image/jpeg':
                        raise ValueError('Risposta non valida')
                    data = response.read(524289)
                    if len(data) > 524288:
                        raise ValueError('Immagine troppo grande')
                status, mime = 200, 'image/jpeg'
            except (OSError, ValueError, HTTPException):
                status, mime, data = 503, 'text/plain', b'Camera non disponibile'
        else:
            self.send_error(404)
            return
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://192.168.1.104:8001', help='URL del relay sul Raspberry')
    args = parser.parse_args()
    parsed = urlsplit(args.url)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
        parser.error('URL HTTP/HTTPS non valido')
    key = os.getenv('COSO_API_KEY') or getpass.getpass('COSO_API_KEY: ')
    if not key.strip():
        parser.error('Chiave obbligatoria')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Viewer)
    server.relay_url = args.url.rstrip('/')
    server.api_key = key
    url = f'http://127.0.0.1:{server.server_port}/'
    print(f'Viewer: {url}\nCtrl+C per chiudere.')
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
