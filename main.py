from __future__ import annotations

import argparse

from scripts.init_db import main as init_db
from scripts.replay_phase_minus_1 import main as replay
from scripts.export_trace import main as trace
from scripts.telegram_command_dry_run import main as tg
from scripts.run_telegram_bot import main as tg_bot


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenClaw Perp Analyst v0.2 foundation")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db")
    subparsers.add_parser("replay")
    subparsers.add_parser("trace")
    subparsers.add_parser("tg")
    subparsers.add_parser("tg-bot")
    args, unknown = parser.parse_known_args()
    if args.command == "init-db":
        init_db()
    elif args.command == "replay":
        import sys

        sys.argv = [sys.argv[0]] + unknown
        replay()
    elif args.command == "trace":
        import sys

        sys.argv = [sys.argv[0]] + unknown
        trace()
    elif args.command == "tg":
        import sys

        sys.argv = [sys.argv[0]] + unknown
        tg()
    elif args.command == "tg-bot":
        import sys

        sys.argv = [sys.argv[0]] + unknown
        tg_bot()


if __name__ == "__main__":
    main()
