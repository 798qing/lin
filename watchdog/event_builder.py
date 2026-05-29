from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional

from analysis.schema_validation import validate_event


EVENT_SCHEMA_VERSION = "event_v0.1"


def stable_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def snapshot_hash(market_snapshot: dict[str, Any], raw_refs: dict[str, str]) -> str:
    payload = {"market_snapshot": market_snapshot, "raw_refs": raw_refs}
    return "sha256_" + hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()


def event_id(symbol: str, timeframe: str, trigger_type: str, close_ts: int) -> str:
    raw = f"{symbol}|{timeframe}|{trigger_type}|{close_ts}"
    return "evt_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def build_event(
    symbol: str,
    timeframe: str,
    trigger_type: str,
    close_ts: int,
    market_snapshot: dict[str, Any],
    raw_refs: dict[str, str],
    thresholds_version: str,
    created_at: Optional[datetime] = None,
) -> dict[str, Any]:
    created = created_at or datetime.now(timezone.utc)
    event = {
        "event_id": event_id(symbol, timeframe, trigger_type, close_ts),
        "event_schema_version": EVENT_SCHEMA_VERSION,
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "symbol": symbol,
        "timeframe": timeframe,
        "trigger_type": trigger_type,
        "close_ts": int(close_ts),
        "market_snapshot": market_snapshot,
        "snapshot_hash": snapshot_hash(market_snapshot, raw_refs),
        "raw_refs": raw_refs,
        "thresholds_version": thresholds_version,
    }
    validate_event(event)
    return event
