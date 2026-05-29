from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.db import DEFAULT_DB


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the latest stored event JSON from SQLite.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--out", default="data/latest_event.json")
    args = parser.parse_args()

    with sqlite3.connect(args.db) as conn:
        row = conn.execute(
            """
            SELECT event_id, event_schema_version, created_at, symbol, timeframe,
                   trigger_type, close_ts, thresholds_version, snapshot_hash,
                   market_snapshot_json, raw_refs_json
            FROM events
            ORDER BY created_at DESC, close_ts DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        raise SystemExit("No events found. Run scripts/replay_phase_minus_1.py first.")
    event = {
        "event_id": row[0],
        "event_schema_version": row[1],
        "created_at": _iso(row[2]),
        "symbol": row[3],
        "timeframe": row[4],
        "trigger_type": row[5],
        "close_ts": row[6],
        "thresholds_version": row[7],
        "snapshot_hash": row[8],
        "market_snapshot": json.loads(row[9]),
        "raw_refs": json.loads(row[10]),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(event, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(args.out)


def _iso(timestamp: int) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    main()
