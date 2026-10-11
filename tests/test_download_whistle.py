from pathlib import Path
from unittest.mock import Mock

import pytest
from needle.agent import fetch

from scripts.download_whistle import download_whistle


@pytest.mark.parametrize("custom_directory", [False, True])
def test_prepares_runtime_and_weights_once(tmp_path, monkeypatch, custom_directory):
    monkeypatch.setenv("NEEDLE_TELEMETRY", "0")
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    monkeypatch.setattr(fetch, "cache_dir", lambda generation: str(tmp_path / str(generation)))
    directory = tmp_path / "custom" if custom_directory else None

    def weights_download(generation, destination):
        path = Path(destination) / fetch.base_weights(generation)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"model")
        return str(path)

    def library_download(*, dest_dir, generation):
        path = Path(dest_dir) / fetch.lib_name(generation)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"runtime")
        return str(path)

    weights_fetch = Mock(side_effect=weights_download)
    library_fetch = Mock(side_effect=library_download)
    monkeypatch.setattr(fetch, "fetch_weights", weights_fetch)
    monkeypatch.setattr(fetch, "fetch_library", library_fetch)
    weights, library = download_whistle(directory)
    assert weights.read_bytes() == b"model"
    assert library.read_bytes() == b"runtime"
    assert download_whistle(directory) == (weights, library)
    weights_fetch.assert_called_once_with(fetch.WHISTLE, str(directory or tmp_path / fetch.WHISTLE))
    library_fetch.assert_called_once_with(dest_dir=str(directory or tmp_path / "3"), generation=3)
