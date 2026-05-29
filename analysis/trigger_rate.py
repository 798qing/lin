from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


SECONDS_PER_DAY = 24 * 60 * 60


def audit_trigger_rates(
    rows: Iterable[dict[str, Any]],
    trigger_records: Iterable[dict[str, Any]],
    max_triggers_per_symbol_day: float = 12.0,
    exempt_trigger_types: Iterable[str] = ("CANDLE_CLOSE_1H", "CANDLE_CLOSE_4H"),
) -> dict[str, Any]:
    exempt = set(exempt_trigger_types)
    rows_by_group: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        rows_by_group[(row["symbol"], row["timeframe"])].append(row)

    records_by_group: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in trigger_records:
        records_by_group[(record["symbol"], record["timeframe"], record["trigger_type"])].append(record)

    groups = {}
    issues = []
    for key, records in sorted(records_by_group.items()):
        symbol, timeframe, trigger_type = key
        coverage_days = _coverage_days(rows_by_group[(symbol, timeframe)])
        trigger_count = len(records)
        per_day = round(trigger_count / coverage_days, 6) if coverage_days else 0.0
        if trigger_type in exempt:
            status = "EXEMPT_SCHEDULED"
        else:
            status = "PASS" if per_day <= max_triggers_per_symbol_day else "WARN"
        label = "|".join(key)
        groups[label] = {
            "symbol": symbol,
            "timeframe": timeframe,
            "trigger_type": trigger_type,
            "coverage_days": round(coverage_days, 6),
            "trigger_count": trigger_count,
            "triggers_per_symbol_day": per_day,
            "max_triggers_per_symbol_day": max_triggers_per_symbol_day,
            "status": status,
        }
        if status == "WARN":
            issues.append(
                {
                    "severity": "WARN",
                    "code": "TRIGGER_RATE_HIGH",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "trigger_type": trigger_type,
                    "message": f"{per_day} triggers/day exceeds {max_triggers_per_symbol_day}",
                }
            )

    return {
        "status": "WARN" if issues else "PASS",
        "max_triggers_per_symbol_day": max_triggers_per_symbol_day,
        "exempt_trigger_types": sorted(exempt),
        "groups": groups,
        "issues": issues,
    }


def _coverage_days(rows: list[dict[str, Any]]) -> float:
    if not rows:
        return 0.0
    timestamps = [int(row["close_ts"]) for row in rows]
    span_seconds = max(timestamps) - min(timestamps)
    return max(span_seconds / SECONDS_PER_DAY, 1.0)
