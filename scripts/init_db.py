from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.db import DEFAULT_DB, migrate


def main() -> None:
    migrate(DEFAULT_DB)
    print(f"SQLite schema ready: {Path(DEFAULT_DB)}")


if __name__ == "__main__":
    main()
