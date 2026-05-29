from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from analysis.db import DEFAULT_DB, migrate


PROMPT_VERSION = "telegram_command_v0.1"
LOCAL_MODEL = "none_v0.2_local"
SYMBOLS = {
    "btc": "BTC-USDT-SWAP",
    "eth": "ETH-USDT-SWAP",
    "sol": "SOL-USDT-SWAP",
    "btcusdtswap": "BTC-USDT-SWAP",
    "ethusdtswap": "ETH-USDT-SWAP",
    "solusdtswap": "SOL-USDT-SWAP",
    "btcusdt": "BTC-USDT-SWAP",
    "ethusdt": "ETH-USDT-SWAP",
    "solusdt": "SOL-USDT-SWAP",
}
FULL_PIPELINE_COMMANDS = {"btcusdt", "ethusdt", "solusdt"}


@dataclass(frozen=True)
class TelegramCommand:
    kind: str
    symbol: Optional[str] = None
    raw: str = ""


def parse_command(text: str) -> TelegramCommand:
    raw = text.strip()
    if not raw.startswith("/"):
        return TelegramCommand("UNKNOWN", raw=raw)
    parts = raw[1:].split()
    command = parts[0].split("@", 1)[0].lower() if parts else ""
    if command in {"start", "help"}:
        return TelegramCommand("HELP", raw=raw)
    if command in {"signal", "signals"}:
        return TelegramCommand("SIGNAL", raw=raw)
    if command == "risk" and len(parts) >= 2:
        return TelegramCommand("RISK", symbol=_symbol(parts[1]), raw=raw)
    if command == "trace" and len(parts) >= 2:
        return TelegramCommand("TRACE", symbol=_symbol(parts[1]), raw=raw)
    if command in FULL_PIPELINE_COMMANDS:
        return TelegramCommand("FULL_PIPELINE", symbol=SYMBOLS[command], raw=raw)
    if command in SYMBOLS:
        return TelegramCommand("QUICK_LOOKUP", symbol=SYMBOLS[command], raw=raw)
    return TelegramCommand("UNKNOWN", raw=raw)


def handle_command(text: str, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    command = parse_command(text)
    if command.kind == "HELP":
        response = _help_text()
    elif command.kind == "QUICK_LOOKUP":
        response = _quick_lookup(db_path, command.symbol)
        _insert_quick_lookup(db_path, command.symbol or "UNKNOWN", response)
    elif command.kind == "SIGNAL":
        response = _latest_signals(db_path)
    elif command.kind == "RISK":
        response = _latest_risk(db_path, command.symbol)
    elif command.kind == "TRACE":
        response = _latest_trace_summary(db_path, command.symbol)
    elif command.kind == "FULL_PIPELINE":
        response = _full_pipeline_stub(command.symbol)
    else:
        response = "Unknown command. Try /help, /sol, /signal, /risk BTC, or /trace SOL."
    return {
        "status": "OK" if command.kind != "UNKNOWN" else "UNKNOWN_COMMAND",
        "command": command.kind,
        "symbol": command.symbol,
        "response": response,
        "private_api": "not_used",
    }


def _symbol(value: str) -> Optional[str]:
    normalized = value.lower().replace("-", "").replace("_", "")
    return SYMBOLS.get(normalized)


def _help_text() -> str:
    return "\n".join(
        [
            "OpenClaw Perp Analyst commands:",
            "/sol /btc /eth - local quick read from latest frozen event",
            "/signal - latest stored tickets",
            "/risk BTC - latest stored risk check",
            "/trace SOL - latest trace summary",
            "/solusdt /btcusdt /ethusdt - full pipeline placeholder, not enabled in v0.2",
            "private_api=not_used",
        ]
    )


def _quick_lookup(db_path: Path, symbol: Optional[str]) -> str:
    if symbol is None:
        return "Unknown symbol. Use BTC, ETH, or SOL."
    row = _latest_event_row(db_path, symbol)
    if row is None:
        return f"No frozen event found for {symbol}. Run public replay first."
    import json

    snapshot = json.loads(row["market_snapshot_json"])
    features = snapshot.get("features", {})
    evidence = snapshot.get("trigger_evidence", {})
    lines = [
        f"{symbol} quick read",
        f"trigger={row['trigger_type']} timeframe={row['timeframe']} close_ts={row['close_ts']}",
        f"regime={snapshot.get('regime', 'unknown')} close={features.get('close')}",
        f"snapshot_hash={row['snapshot_hash']}",
    ]
    if evidence:
        lines.append(f"why={evidence.get('reason')} distribution={evidence.get('distribution_position')}")
    lines.append("This is read-only from frozen SQLite data. private_api=not_used")
    return "\n".join(lines)


def _latest_signals(db_path: Path, limit: int = 5) -> str:
    if not db_path.exists():
        return "No database found. Run public replay first."
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT t.ticket_id, t.status, t.action, t.created_at, e.symbol, e.timeframe, e.trigger_type
            FROM tickets t
            JOIN analysis_runs r ON r.id = t.analysis_run_id
            JOIN events e ON e.event_id = r.event_id
            ORDER BY t.created_at DESC, t.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    if not rows:
        return "No tickets found. Run public replay first."
    lines = ["Latest tickets:"]
    for row in rows:
        lines.append(f"- {row['symbol']} {row['timeframe']} {row['trigger_type']} {row['status']} {row['ticket_id']}")
    lines.append("private_api=not_used")
    return "\n".join(lines)


def _latest_risk(db_path: Path, symbol: Optional[str]) -> str:
    if symbol is None:
        return "Unknown symbol. Use /risk BTC, /risk ETH, or /risk SOL."
    if not db_path.exists():
        return "No database found. Run public replay first."
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT rc.verdict, rc.risk_rules_version, rc.max_loss_amount, rc.leverage_cap,
                   rc.suggested_position_size, e.symbol, e.timeframe, e.trigger_type
            FROM risk_checks rc
            JOIN analysis_runs r ON r.id = rc.analysis_run_id
            JOIN events e ON e.event_id = r.event_id
            WHERE e.symbol = ?
            ORDER BY r.started_at DESC, rc.id DESC
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()
    if row is None:
        return f"No risk check found for {symbol}. Run public replay first."
    return "\n".join(
        [
            f"{symbol} latest risk",
            f"verdict={row['verdict']} trigger={row['trigger_type']} timeframe={row['timeframe']}",
            f"risk_rules={row['risk_rules_version']} max_loss={row['max_loss_amount']} leverage_cap={row['leverage_cap']}",
            f"suggested_position_size={row['suggested_position_size']}",
            "private_api=not_used",
        ]
    )


def _latest_trace_summary(db_path: Path, symbol: Optional[str]) -> str:
    if symbol is None:
        return "Unknown symbol. Use /trace BTC, /trace ETH, or /trace SOL."
    row = _latest_event_row(db_path, symbol)
    if row is None:
        return f"No traceable event found for {symbol}. Run public replay first."
    return "\n".join(
        [
            f"{symbol} trace",
            f"event_id={row['event_id']}",
            f"snapshot_hash={row['snapshot_hash']}",
            f"thresholds_version={row['thresholds_version']}",
            "Use `python3 main.py trace --event-id <event_id>` for the full local JSON trace.",
            "private_api=not_used",
        ]
    )


def _full_pipeline_stub(symbol: Optional[str]) -> str:
    return "\n".join(
        [
            f"{symbol} full pipeline is not enabled in v0.2.",
            "Current TG layer is read-only command routing over frozen events and tickets.",
            "Next step: wire this handler to Telegram Bot polling with token/chat_id from environment variables.",
            "private_api=not_used",
        ]
    )


def _latest_event_row(db_path: Path, symbol: str) -> Optional[sqlite3.Row]:
    if not db_path.exists():
        return None
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            """
            SELECT *
            FROM events
            WHERE symbol = ?
            ORDER BY created_at DESC, close_ts DESC
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()


def _insert_quick_lookup(db_path: Path, symbol: str, response: str) -> None:
    migrate(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO quick_lookups (
                symbol, requested_at, prompt_version, model, response_summary, cost_usd
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (symbol, int(datetime.utcnow().timestamp()), PROMPT_VERSION, LOCAL_MODEL, response[:1000], 0.0),
        )
