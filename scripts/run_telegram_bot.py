from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.db import DEFAULT_DB
from notify.telegram_bot import run_polling


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Telegram Bot polling for read-only v0.2 commands.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--once", action="store_true", help="Poll once for tests or smoke checks.")
    parser.add_argument("--poll-seconds", type=int, default=2)
    args = parser.parse_args()

    run_polling(db_path=Path(args.db), once=args.once, poll_seconds=args.poll_seconds)


if __name__ == "__main__":
    main()
