from email.message import Message
import base64
from io import BytesIO
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from camera_relay import CameraReader, FrameBuffer, RelayServer, local_opener, mjpeg_frames
from app.services.camera_service import CameraService, CameraUnavailable, is_visual_question


JPEG = b'\xff\xd8test\xff\xd9'


def multipart(data=JPEG, *, mime='image/jpeg', length=None):
    return (b'\r\n--frame\r\nContent-Type: ' + mime.encode() + b'\r\nContent-Length: '
            + str(len(data) if length is None else length).encode() + b'\r\n\r\n' + data)


def test_mjpeg_parser_reads_multiple_frames_without_decoding():
    source = BytesIO(multipart() + multipart(JPEG + b'\xff\xd9') + b'\r\n--frame--\r\n')
    assert list(mjpeg_frames(source, 'multipart/x-mixed-replace; boundary="frame"', 100)) == [JPEG, JPEG + b'\xff\xd9']


@pytest.mark.parametrize('payload,mime', [
    (multipart(length=101), 'multipart/x-mixed-replace; boundary=frame'),
    (multipart(mime='text/plain'), 'multipart/x-mixed-replace; boundary=frame'),
    (multipart(b'not jpeg'), 'multipart/x-mixed-replace; boundary=frame'),
    (multipart(length=99), 'multipart/x-mixed-replace; boundary=frame'),
    (b'--frame\r\n' + b'x' * 9000, 'multipart/x-mixed-replace; boundary=frame'),
    (multipart(), 'text/html'),
])
def test_parser_rejects_invalid_or_unbounded_stream(payload, mime):
    with pytest.raises((ValueError, EOFError)):
        list(mjpeg_frames(BytesIO(payload), mime, 100))


def test_buffer_is_bounded_and_rejects_stale_frames(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr('camera_relay.time.monotonic', lambda: clock[0])
    buffer = FrameBuffer(max_age=3, max_bytes=20)
    for i in range(9):
        buffer.put(bytes([i]))
    assert len(buffer.frames) == 5
    assert [frame.sequence for frame in buffer.frames] == [5, 6, 7, 8, 9]
    assert buffer.latest().data == b'\x08'
    with pytest.raises(ValueError):
        buffer.put(b'x' * 21)
    clock[0] += 3.01
    assert buffer.latest() is None


@pytest.fixture
def relay():
    buffer = FrameBuffer()
    server = RelayServer(('127.0.0.1', 0), buffer, 'secret', max_viewers=1, fps=30,
                         username='buddy', password='video-secret')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, f'http://127.0.0.1:{server.server_port}'
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def get(url, key='secret'):
    return local_opener().open(Request(url, headers={'X-API-Key': key}), timeout=2)


def basic(credentials='buddy:video-secret'):
    return {'Authorization': 'Basic ' + base64.b64encode(credentials.encode()).decode()}


@pytest.mark.parametrize('path', ['/snapshot', '/stream'])
def test_basic_auth_allows_video_and_challenges_browser(relay, path):
    server, url = relay
    server.buffer.put(JPEG)
    with pytest.raises(HTTPError) as exc:
        local_opener().open(url + path, timeout=2)
    assert exc.value.code == 401
    assert exc.value.headers['WWW-Authenticate'].startswith('Basic ')
    with local_opener().open(Request(url + path, headers=basic()), timeout=2) as response:
        assert response.status == 200
        if path == '/snapshot':
            assert response.read() == JPEG
        else:
            assert response.readline().strip() == b'--cosoframe'
    with server.buffer.condition:
        server.buffer.frames.clear()


@pytest.mark.parametrize('authorization', [
    'Basic not-base64', 'Basic ' + base64.b64encode(b'buddy:wrong').decode(),
    'Basic ' + base64.b64encode(b'wrong:video-secret').decode(),
    'Bearer video-secret', 'Basic ',
])
def test_invalid_basic_auth_is_rejected(relay, authorization):
    _, url = relay
    with pytest.raises(HTTPError) as exc:
        local_opener().open(Request(url + '/snapshot', headers={'Authorization': authorization}), timeout=2)
    assert exc.value.code == 401


@pytest.mark.parametrize('path,method', [('/health', 'GET'), ('/unknown', 'GET'),
                                       ('/snapshot', 'POST'), ('/stream', 'OPTIONS')])
def test_video_credentials_cannot_access_other_routes_or_methods(relay, path, method):
    _, url = relay
    with pytest.raises(HTTPError) as exc:
        local_opener().open(Request(url + path, headers=basic(), method=method), timeout=2)
    assert exc.value.code == 401


def test_basic_auth_is_optional_and_head_works(relay):
    server, url = relay
    server.buffer.put(JPEG)
    with local_opener().open(Request(url + '/snapshot', headers=basic(), method='HEAD'), timeout=2) as response:
        assert response.status == 200
        assert response.read() == b''
    server.video_credentials = None
    with pytest.raises(HTTPError) as exc:
        local_opener().open(Request(url + '/snapshot', headers=basic()), timeout=2)
    assert exc.value.code == 401
    assert 'WWW-Authenticate' not in exc.value.headers
    assert CameraService(url=url, api_key='secret').snapshot().data == JPEG


@pytest.mark.parametrize('username,password', [('buddy', ''), ('', 'secret'),
                                             ('bad:user', 'secret'), (' ', 'secret'), ('buddy', ' ')])
def test_incomplete_basic_configuration_fails(username, password):
    with pytest.raises(ValueError):
        RelayServer(('127.0.0.1', 0), FrameBuffer(), 'secret', username=username, password=password)


@pytest.mark.parametrize('path', ['/health', '/snapshot', '/stream', '/unknown'])
@pytest.mark.parametrize('method', ['GET', 'HEAD', 'OPTIONS', 'POST'])
def test_relay_requires_key_everywhere(relay, path, method):
    _, url = relay
    with pytest.raises(HTTPError) as exc:
        local_opener().open(Request(url + path, headers={'X-API-Key': 'wrong'}, method=method), timeout=2)
    assert exc.value.code == 401


def test_snapshot_and_stream_do_not_block_health_or_voice_snapshot(relay):
    server, url = relay
    with pytest.raises(HTTPError) as exc:
        get(url + '/snapshot')
    assert exc.value.code == 503
    server.buffer.put(JPEG)
    service = CameraService(url=url, api_key='secret')
    assert service.snapshot().data == JPEG
    with get(url + '/stream') as stream:
        assert stream.headers.get_content_type() == 'multipart/x-mixed-replace'
        assert stream.readline().strip() == b'--cosoframe'
        with pytest.raises(HTTPError) as exc:
            get(url + '/stream')
        assert exc.value.code == 503
        with get(url + '/health') as health:
            assert health.status == 200
        assert service.snapshot().data == JPEG
    with server.buffer.condition:
        server.buffer.frames.clear()


class FakeResponse(BytesIO):
    def __init__(self, *, data=JPEG, mime='image/jpeg', age='0', status=200):
        super().__init__(data)
        self.status = status
        self.headers = Message()
        self.headers['Content-Type'] = mime
        self.headers['X-Frame-Age'] = age


@pytest.mark.parametrize('options', [
    {'mime': 'text/html'}, {'age': '4'}, {'age': 'nan'}, {'age': '-1'},
    {'data': b'not jpeg'}, {'data': JPEG * 100}, {'status': 302},
])
def test_snapshot_client_rejects_invalid_response(monkeypatch, options):
    response = FakeResponse(**options)
    class Opener:
        def open(self, req, timeout):
            assert req.get_header('X-api-key') == 'secret'
            assert timeout == 0.75
            return response
    monkeypatch.setattr('app.services.camera_service.local_opener', Opener)
    with pytest.raises(CameraUnavailable):
        CameraService(url='http://relay:8001', api_key='secret', max_bytes=100).snapshot()


def test_disabled_camera_never_opens_network(monkeypatch):
    monkeypatch.setattr('app.services.camera_service.local_opener', lambda: pytest.fail('Network opened'))
    with pytest.raises(CameraUnavailable):
        CameraService(url='', api_key='secret').snapshot()


def test_snapshot_redirect_does_not_forward_credentials():
    requests = []
    class Redirect(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            requests.append(self.path)
            self.send_response(302)
            self.send_header('Location', '/leak')
            self.end_headers()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Redirect)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        service = CameraService(url=f'http://127.0.0.1:{server.server_port}', api_key='secret')
        with pytest.raises(CameraUnavailable):
            service.snapshot()
        assert requests == ['/snapshot']
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_reader_reconnects_after_failure(monkeypatch):
    buffer = FrameBuffer()
    reader = CameraReader('http://camera:81/stream', buffer)
    attempts = []
    waits = []
    class Opener:
        def open(self, url, timeout):
            attempts.append(url)
            if len(attempts) == 1:
                raise TimeoutError('offline')
            return FakeResponse(data=multipart() + b'\r\n--frame--\r\n', mime='multipart/x-mixed-replace; boundary=frame')
    def wait(seconds):
        waits.append(seconds)
        if len(attempts) == 2:
            reader.stop.set()
    monkeypatch.setattr('camera_relay.local_opener', Opener)
    monkeypatch.setattr(reader.stop, 'wait', wait)
    reader.run()
    assert len(attempts) == 2
    assert waits == [1, 1]  # Backoff resets after receipt of a valid frame.
    assert buffer.latest().data == JPEG


def test_reader_drops_excess_frames_and_does_not_send_api_key(monkeypatch):
    buffer = FrameBuffer()
    reader = CameraReader('http://camera:81/stream', buffer, fps=5)
    response = FakeResponse(data=multipart() * 10 + b'\r\n--frame--\r\n', mime='multipart/x-mixed-replace; boundary=frame')
    class Opener:
        def open(self, url, timeout):
            assert url == 'http://camera:81/stream'  # Plain URL, no authentication headers.
            assert timeout == 3
            return response
    monkeypatch.setattr('camera_relay.local_opener', Opener)
    monkeypatch.setattr('camera_relay.time.monotonic', lambda: 100.0)
    monkeypatch.setattr(reader.stop, 'wait', lambda _: reader.stop.set())
    reader.run()
    assert len(buffer.frames) == 1


@pytest.mark.parametrize('text', ['Cosa vedi?', 'Che cosa vedi davanti a te?', 'Che oggetto è questo?',
                                  'Descrivi quello che vedi.', 'Identifica questo oggetto',
                                  'Cosa sto inquadrando?', 'Cosa ho davanti?'])
def test_visual_questions(text):
    assert is_visual_question(text)


@pytest.mark.parametrize('text', ['Perché il cielo è blu?', 'Come vedi il futuro?', "Che cos'è un oggetto?", 'Ciao'])
def test_ordinary_questions_do_not_use_camera(text):
    assert not is_visual_question(text)
