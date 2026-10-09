"""Download the Paola voice and matching config without requiring Piper installed."""

import argparse
import json
import shutil
from pathlib import Path
from urllib.request import urlopen
from uuid import uuid4

VOICE = "it_IT-paola-medium"
REVISION = "375a0fe641dea077c2a47b4e9a056d6da521eed3"
BASE_URL = (
    f"https://huggingface.co/rhasspy/piper-voices/resolve/{REVISION}"
    "/it/it_IT/paola/medium"
)
DEFAULT_DIRECTORY = Path(__file__).resolve().parent.parent / "data" / "voices"


def download_voice(directory: Path, *, force: bool = False) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    model = directory / f"{VOICE}.onnx"
    config = directory / f"{VOICE}.onnx.json"
    if not force and model.is_file() and model.stat().st_size > 0 and config.is_file():
        json.loads(config.read_text(encoding="utf-8"))
        print(f"Voce gia presente: {model}")
        return model

    pending = []
    try:
        for destination in (model, config):
            temporary = directory / f".{uuid4().hex}.download"
            pending.append((temporary, destination))
            url = f"{BASE_URL}/{destination.name}"
            print(f"Scarico {destination.name}...", flush=True)
            with urlopen(url, timeout=120) as response, temporary.open("wb") as output:
                shutil.copyfileobj(response, output)
                expected = response.headers.get("Content-Length")
            if temporary.stat().st_size == 0 or (
                expected and temporary.stat().st_size != int(expected)
            ):
                raise ValueError(f"Download incompleto: {destination.name}")
        json.loads(pending[1][0].read_text(encoding="utf-8"))
        for temporary, destination in pending:
            temporary.replace(destination)
    finally:
        for temporary, _ in pending:
            temporary.unlink(missing_ok=True)
    print(f"Voce pronta: {model}")
    return model


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    download_voice(args.output_dir, force=args.force)


if __name__ == "__main__":
    main()
