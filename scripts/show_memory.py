"""Mostra i ricordi di Coso Server senza modificare il database."""

import argparse
import json
import os
from pathlib import Path
import shlex
import sqlite3
import sys


ROOT = Path(__file__).resolve().parent.parent


def configured_database() -> Path:
    value = os.environ.get("MEMORY_DB_PATH")
    env_file = ROOT / ".env"
    if value is None and env_file.is_file():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            key, separator, raw = line.strip().removeprefix("export ").partition("=")
            if separator and key.strip() == "MEMORY_DB_PATH":
                lexer = shlex.shlex(raw.strip(), posix=True)
                lexer.whitespace_split = True
                lexer.escape = ""
                value = " ".join(lexer)
    path = Path(value or "data/memory.sqlite3").expanduser()
    return path if path.is_absolute() else ROOT / path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, help="Percorso alternativo del database")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()
    try:
        database = (args.db.expanduser() if args.db else configured_database()).resolve()
        # Read-only mode also prevents creating a new DB for an incorrect path.
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
        try:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT id, summary, category, tags_json, created_at FROM memories "
                "ORDER BY created_at DESC, id DESC"
            ).fetchall()
        finally:
            connection.close()
        memories = [dict(id=row["id"], summary=row["summary"], category=row["category"],
                         tags=json.loads(row["tags_json"]), created_at=row["created_at"])
                    for row in rows]
        if args.json:
            print(json.dumps(memories, ensure_ascii=False, indent=2))
        else:
            print(f"Database: {database}\nRicordi salvati: {len(memories)}")
            for memory in memories:
                print(f"\n#{memory['id']} | {memory['category']} | {memory['created_at']}")
                print(f"Tag: {', '.join(memory['tags']) or '-'}")
                print(memory["summary"])
        return 0
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"Impossibile leggere la memoria: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
