from __future__ import annotations

import argparse

from scripts.init_db import main as init_db
from scripts.replay_phase_minus_1 import main as replay


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenClaw Perp Analyst v0.2 foundation")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db")
    subparsers.add_parser("replay")
    args, unknown = parser.parse_known_args()
    if args.command == "init-db":
        init_db()
    elif args.command == "replay":
        import sys

        sys.argv = [sys.argv[0]] + unknown
        replay()


if __name__ == "__main__":
    main()
