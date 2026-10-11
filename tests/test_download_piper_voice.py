import json
from io import BytesIO
from unittest.mock import Mock, call

import pytest

from scripts.download_piper_voice import VOICE, download_voice, main


class Download(BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


@pytest.mark.parametrize("voice,base_url", [
    (VOICE, "https://huggingface.co/committa/it_IT-serena-high/resolve/"
     "c704759b2286f5f8c56eb4476169c4e2cda10b02"),
    ("it_IT-serena-medium", "https://huggingface.co/rhasspy/piper-voices/resolve/main/it/it_IT/serena/medium"),
    ("it_IT-riccardo-x_low", "https://huggingface.co/rhasspy/piper-voices/resolve/main/it/it_IT/riccardo/x_low"),
])
def test_downloads_matching_model_and_config_once(tmp_path, monkeypatch, voice, base_url):
    urls = []

    def download(url, timeout):
        urls.append(url)
        return Download(b'{"audio":{"sample_rate":22050}}' if url.endswith(".json") else b"onnx-model")

    monkeypatch.setattr("scripts.download_piper_voice.urlopen", download)
    model = download_voice(tmp_path, voice=voice)
    assert model.read_bytes() == b"onnx-model"
    config = json.loads((tmp_path / f"{voice}.onnx.json").read_text())
    assert config["audio"]["sample_rate"] == 22050
    assert len(urls) == 2
    assert urls[0] == f"{base_url}/{voice}.onnx"
    assert urls[1] == urls[0] + ".json"
    download_voice(tmp_path, voice=voice)
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


@pytest.mark.parametrize("selection,expected", [
    ([], [VOICE]),
    (["--all"], [VOICE, "it_IT-serena-medium", "it_IT-riccardo-x_low"]),
    (["--voice", "it_IT-serena-medium", "--voice", "it_IT-riccardo-x_low"],
     ["it_IT-serena-medium", "it_IT-riccardo-x_low"]),
])
def test_command_selects_requested_voices(tmp_path, monkeypatch, selection, expected):
    download = Mock()
    monkeypatch.setattr("scripts.download_piper_voice.download_voice", download)
    monkeypatch.setattr("sys.argv", ["download_piper_voice.py", "--output-dir", str(tmp_path),
                                    "--force", *selection])
    main()
    assert download.call_args_list == [call(tmp_path, voice=voice, force=True) for voice in expected]
