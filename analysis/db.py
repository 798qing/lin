from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "trading.db"
DEFAULT_MIGRATION = ROOT / "migrations" / "001_init.sql"


def connect(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate(db_path: Path = DEFAULT_DB, migration_path: Path = DEFAULT_MIGRATION) -> None:
    with connect(db_path) as conn:
        conn.executescript(migration_path.read_text(encoding="utf-8"))


def insert_event(conn: sqlite3.Connection, event: dict[str, Any]) -> bool:
    created_at = _to_unix(event["created_at"])
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO events (
            event_id, event_schema_version, created_at, symbol, timeframe,
            trigger_type, close_ts, thresholds_version, snapshot_hash,
            market_snapshot_json, raw_refs_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event["event_id"],
            event["event_schema_version"],
            created_at,
            event["symbol"],
            event["timeframe"],
            event["trigger_type"],
            event["close_ts"],
            event["thresholds_version"],
            event["snapshot_hash"],
            json.dumps(event["market_snapshot"], ensure_ascii=False, sort_keys=True),
            json.dumps(event["raw_refs"], ensure_ascii=False, sort_keys=True),
        ),
    )
    return cursor.rowcount == 1


def insert_analysis_run(
    conn: sqlite3.Connection,
    event_id: str,
    prompt_version: str,
    agent_versions: dict[str, str],
    thresholds_version: str,
    risk_rules_version: str,
    status: str = "STARTED",
    rerun_of: Optional[int] = None,
) -> int:
    now = int(datetime.utcnow().timestamp())
    cursor = conn.execute(
        """
        INSERT INTO analysis_runs (
            event_id, rerun_of, started_at, prompt_version, agent_versions_json,
            thresholds_version, risk_rules_version, status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            rerun_of,
            now,
            prompt_version,
            json.dumps(agent_versions, sort_keys=True),
            thresholds_version,
            risk_rules_version,
            status,
        ),
    )
    return int(cursor.lastrowid)


def finish_analysis_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
    cost_usd: Optional[float] = None,
) -> None:
    conn.execute(
        """
        UPDATE analysis_runs
        SET finished_at = ?, status = ?, error_code = ?, error_message = ?, cost_usd = ?
        WHERE id = ?
        """,
        (int(datetime.utcnow().timestamp()), status, error_code, error_message, cost_usd, run_id),
    )


def insert_risk_check(conn: sqlite3.Connection, run_id: int, risk_output: dict[str, Any]) -> int:
    cursor = conn.execute(
        """
        INSERT INTO risk_checks (
            analysis_run_id, risk_rules_version, account_equity, max_loss_pct,
            max_loss_amount, leverage_cap, stop_distance, suggested_position_size,
            margin_mode, liq_safety_margin, daily_loss_state, consecutive_loss_state,
            input_json, output_json, verdict
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            risk_output["risk_rules_version"],
            risk_output["account_equity"],
            risk_output["max_loss_pct"],
            risk_output["max_loss_amount"],
            risk_output["leverage_cap"],
            risk_output["stop_distance"],
            risk_output["suggested_position_size"],
            risk_output["margin_mode"],
            risk_output["liq_safety_margin"].get("gap"),
            json.dumps(risk_output["daily_loss_state"], sort_keys=True),
            json.dumps(risk_output["consecutive_loss_state"], sort_keys=True),
            json.dumps(risk_output["input_json"], sort_keys=True),
            json.dumps(risk_output, sort_keys=True),
            risk_output["verdict"],
        ),
    )
    return int(cursor.lastrowid)


def insert_ticket(conn: sqlite3.Connection, run_id: int, ticket: dict[str, Any]) -> int:
    cursor = conn.execute(
        """
        INSERT INTO tickets (
            analysis_run_id, ticket_id, status, action,
            self_reported_confidence, payload_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            ticket["ticket_id"],
            ticket["status"],
            ticket["action"],
            ticket["self_reported_confidence"],
            json.dumps(ticket, ensure_ascii=False, sort_keys=True),
            int(datetime.utcnow().timestamp()),
        ),
    )
    return int(cursor.lastrowid)


def _to_unix(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
