from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.db import DEFAULT_DB
from notify.telegram_commands import handle_command


def main() -> None:
    parser = argparse.ArgumentParser(description="Dry-run Telegram command handling without a bot token.")
    parser.add_argument("command", help="Command text, for example '/sol' or '/risk SOL'")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = handle_command(args.command, Path(args.db))
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(result["response"])


if __name__ == "__main__":
    main()
