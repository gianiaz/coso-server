import json
from io import BytesIO

import pytest

from scripts.download_piper_voice import VOICE, download_voice


class Download(BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


def test_downloads_matching_model_and_config_once(tmp_path, monkeypatch):
    urls = []

    def download(url, timeout):
        urls.append(url)
        return Download(b'{"audio":{"sample_rate":22050}}' if url.endswith(".json") else b"onnx-model")

    monkeypatch.setattr("scripts.download_piper_voice.urlopen", download)
    model = download_voice(tmp_path)
    assert model.read_bytes() == b"onnx-model"
    config = json.loads((tmp_path / f"{VOICE}.onnx.json").read_text())
    assert config["audio"]["sample_rate"] == 22050
    assert len(urls) == 2
    assert urls[1] == urls[0] + ".json"
    download_voice(tmp_path)
    assert len(urls) == 2


def test_failed_config_download_preserves_existing_voice(tmp_path, monkeypatch):
    model = tmp_path / f"{VOICE}.onnx"
    config = tmp_path / f"{VOICE}.onnx.json"
    model.write_bytes(b"old-model")
    config.write_text("{}")

    def download(url, timeout):
        if url.endswith(".json"):
            raise OSError("network unavailable")
        return Download(b"new-model")

    monkeypatch.setattr("scripts.download_piper_voice.urlopen", download)
    with pytest.raises(OSError):
        download_voice(tmp_path, force=True)
    assert model.read_bytes() == b"old-model"
    assert config.read_text() == "{}"
    assert not list(tmp_path.glob("*.download"))
