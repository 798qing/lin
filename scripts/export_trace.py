from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.db import DEFAULT_DB
from analysis.schema_validation import validate_event, validate_ticket


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a read-only audit trace for one stored event.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--event-id", default=None, help="Event id to export. Defaults to latest event.")
    parser.add_argument("--out", default="reports/latest_trace.json")
    args = parser.parse_args()

    trace = export_trace(Path(args.db), event_id=args.event_id)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(trace, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(args.out)


def export_trace(db_path: Path, event_id: Optional[str] = None) -> dict[str, Any]:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        event_row = _fetch_event(conn, event_id)
        if event_row is None:
            raise SystemExit("No events found. Run scripts/replay_phase_minus_1.py first.")
        event = _event_from_row(event_row)
        validate_event(event)
        run_rows = conn.execute(
            """
            SELECT *
            FROM analysis_runs
            WHERE event_id = ?
            ORDER BY started_at DESC, id DESC
            """,
            (event["event_id"],),
        ).fetchall()
        runs = [_run_trace(conn, row) for row in run_rows]
    return {
        "schema_version": "trace_v0.1",
        "event": event,
        "analysis_runs": runs,
        "traceability": _traceability(event, runs),
        "private_api": "not_used",
    }


def _fetch_event(conn: sqlite3.Connection, event_id: Optional[str]) -> Optional[sqlite3.Row]:
    if event_id:
        return conn.execute(
            """
            SELECT event_id, event_schema_version, created_at, symbol, timeframe,
                   trigger_type, close_ts, thresholds_version, snapshot_hash,
                   market_snapshot_json, raw_refs_json
            FROM events
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()
    return conn.execute(
        """
        SELECT event_id, event_schema_version, created_at, symbol, timeframe,
               trigger_type, close_ts, thresholds_version, snapshot_hash,
               market_snapshot_json, raw_refs_json
        FROM events
        ORDER BY created_at DESC, close_ts DESC
        LIMIT 1
        """
    ).fetchone()


def _event_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "event_id": row["event_id"],
        "event_schema_version": row["event_schema_version"],
        "created_at": _iso(row["created_at"]),
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "trigger_type": row["trigger_type"],
        "close_ts": row["close_ts"],
        "thresholds_version": row["thresholds_version"],
        "snapshot_hash": row["snapshot_hash"],
        "market_snapshot": json.loads(row["market_snapshot_json"]),
        "raw_refs": json.loads(row["raw_refs_json"]),
    }


def _run_trace(conn: sqlite3.Connection, run_row: sqlite3.Row) -> dict[str, Any]:
    risk_rows = conn.execute(
        """
        SELECT *
        FROM risk_checks
        WHERE analysis_run_id = ?
        ORDER BY id
        """,
        (run_row["id"],),
    ).fetchall()
    ticket_rows = conn.execute(
        """
        SELECT *
        FROM tickets
        WHERE analysis_run_id = ?
        ORDER BY created_at, id
        """,
        (run_row["id"],),
    ).fetchall()
    tickets = [_ticket_from_row(row) for row in ticket_rows]
    for ticket in tickets:
        validate_ticket(ticket)
    return {
        "id": run_row["id"],
        "event_id": run_row["event_id"],
        "rerun_of": run_row["rerun_of"],
        "started_at": _iso(run_row["started_at"]),
        "finished_at": _iso(run_row["finished_at"]) if run_row["finished_at"] is not None else None,
        "prompt_version": run_row["prompt_version"],
        "agent_versions": json.loads(run_row["agent_versions_json"]),
        "thresholds_version": run_row["thresholds_version"],
        "risk_rules_version": run_row["risk_rules_version"],
        "status": run_row["status"],
        "error_code": run_row["error_code"],
        "error_message": run_row["error_message"],
        "cost_usd": run_row["cost_usd"],
        "risk_checks": [_risk_check_from_row(row) for row in risk_rows],
        "tickets": tickets,
    }


def _risk_check_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "risk_rules_version": row["risk_rules_version"],
        "account_equity": row["account_equity"],
        "max_loss_pct": row["max_loss_pct"],
        "max_loss_amount": row["max_loss_amount"],
        "leverage_cap": row["leverage_cap"],
        "stop_distance": row["stop_distance"],
        "suggested_position_size": row["suggested_position_size"],
        "margin_mode": row["margin_mode"],
        "liq_safety_margin": row["liq_safety_margin"],
        "daily_loss_state": json.loads(row["daily_loss_state"]),
        "consecutive_loss_state": json.loads(row["consecutive_loss_state"]),
        "input": json.loads(row["input_json"]),
        "output": json.loads(row["output_json"]),
        "verdict": row["verdict"],
    }


def _ticket_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return json.loads(row["payload_json"])


def _traceability(event: dict[str, Any], runs: list[dict[str, Any]]) -> dict[str, Any]:
    issues = []
    if not event["raw_refs"]:
        issues.append({"severity": "FAIL", "code": "RAW_REFS_MISSING", "message": "Event has no raw_refs."})
    for key in ("csv", "csv_sha256"):
        if key not in event["raw_refs"]:
            issues.append({"severity": "WARN", "code": f"RAW_REF_{key.upper()}_MISSING", "message": f"raw_refs.{key} is missing."})
    if not runs:
        issues.append({"severity": "FAIL", "code": "ANALYSIS_RUN_MISSING", "message": "Event has no analysis run."})
    for run in runs:
        if run["thresholds_version"] != event["thresholds_version"]:
            issues.append(
                {
                    "severity": "FAIL",
                    "code": "THRESHOLDS_VERSION_MISMATCH",
                    "message": f"Run {run['id']} thresholds_version does not match event.",
                }
            )
        if not run["risk_checks"]:
            issues.append({"severity": "FAIL", "code": "RISK_CHECK_MISSING", "message": f"Run {run['id']} has no risk check."})
        if not run["tickets"]:
            issues.append({"severity": "FAIL", "code": "TICKET_MISSING", "message": f"Run {run['id']} has no ticket."})
        for ticket in run["tickets"]:
            if ticket["event_id"] != event["event_id"]:
                issues.append({"severity": "FAIL", "code": "TICKET_EVENT_MISMATCH", "message": f"Ticket {ticket['ticket_id']} points to another event."})
            if ticket["snapshot_hash"] != event["snapshot_hash"]:
                issues.append({"severity": "FAIL", "code": "TICKET_SNAPSHOT_HASH_MISMATCH", "message": f"Ticket {ticket['ticket_id']} snapshot hash differs."})
    if any(issue["severity"] == "FAIL" for issue in issues):
        status = "FAIL"
    elif issues:
        status = "WARN"
    else:
        status = "PASS"
    return {"status": status, "issues": issues}


def _iso(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    main()
