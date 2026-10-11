"""Download Whistle weights and the native runtime before starting the offline server."""

import argparse
import os
from pathlib import Path


def download_whistle(directory: Path | None = None) -> tuple[Path, Path]:
    os.environ["NEEDLE_TELEMETRY"] = "0"
    os.environ["DO_NOT_TRACK"] = "1"
    from needle.agent import fetch

    weights_dir = directory or Path(fetch.cache_dir(fetch.WHISTLE))
    library_dir = directory or Path(fetch.cache_dir(3))
    weights = weights_dir / fetch.base_weights(fetch.WHISTLE)
    library = library_dir / fetch.lib_name(3)
    if not weights.is_file() or weights.stat().st_size == 0:
        weights = Path(fetch.fetch_weights(fetch.WHISTLE, str(weights_dir)))
    if not library.is_file() or library.stat().st_size == 0:
        library = Path(fetch.fetch_library(dest_dir=str(library_dir), generation=3))
    for path in (weights, library):
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"File Whistle mancante o vuoto: {path}")
    print(f"Pesi Whistle: {weights}")
    print(f"Runtime locale: {library}")
    return weights, library


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        help="Directory di destinazione; predefinita: cache Needle dell'utente")
    args = parser.parse_args()
    download_whistle(args.output_dir)


if __name__ == "__main__":
    main()
