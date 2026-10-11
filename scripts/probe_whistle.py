"""Probe the server's Whistle adapter using a 16 kHz mono PCM16 WAV, without AI calls."""

import argparse
import json
import sys
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.transcription_service import WhistleTranscriptionService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs deve essere positivo")
    audio = args.wav.read_bytes()
    service = WhistleTranscriptionService()
    for run in range(1, args.runs + 1):
        started = perf_counter()
        text = service.transcribe(audio)
        print(json.dumps({"run": run, "seconds": round(perf_counter() - started, 3),
                          "text": text}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
