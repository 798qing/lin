from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple


EXPECTED_INTERVAL_SECONDS = {
    "15m": 15 * 60,
    "1H": 60 * 60,
    "4H": 4 * 60 * 60,
}


def audit_rows(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(rows)
    issues: List[dict[str, Any]] = []
    by_group: Dict[Tuple[str, str], List[dict[str, Any]]] = defaultdict(list)
    for row in items:
        by_group[(row.get("symbol", "UNKNOWN"), row.get("timeframe", "UNKNOWN"))].append(row)
        issues.extend(_row_issues(row))

    groups: dict[str, Any] = {}
    for key, group_rows in sorted(by_group.items()):
        label = "|".join(key)
        group_issues = _group_issues(key[0], key[1], group_rows)
        issues.extend(group_issues)
        groups[label] = {
            "rows": len(group_rows),
            "start_ts": min(int(row["close_ts"]) for row in group_rows if "close_ts" in row),
            "end_ts": max(int(row["close_ts"]) for row in group_rows if "close_ts" in row),
            "issues": group_issues,
        }

    severity_counts = Counter(issue["severity"] for issue in issues)
    return {
        "row_count": len(items),
        "group_count": len(groups),
        "status": _status(severity_counts),
        "severity_counts": dict(sorted(severity_counts.items())),
        "issue_count": len(issues),
        "issues": issues[:100],
        "issues_truncated": len(issues) > 100,
        "groups": groups,
    }


def _row_issues(row: dict[str, Any]) -> List[dict[str, Any]]:
    issues: List[dict[str, Any]] = []
    required = ["close_ts", "symbol", "timeframe", "open", "high", "low", "close", "volume", "funding", "oi"]
    for field in required:
        if field not in row or row[field] is None:
            issues.append(_issue("ERROR", "MISSING_FIELD", row, f"Missing {field}"))
    if issues:
        return issues
    if not (float(row["low"]) <= float(row["open"]) <= float(row["high"])):
        issues.append(_issue("ERROR", "OHLC_OPEN_OUT_OF_RANGE", row, "open is outside low/high"))
    if not (float(row["low"]) <= float(row["close"]) <= float(row["high"])):
        issues.append(_issue("ERROR", "OHLC_CLOSE_OUT_OF_RANGE", row, "close is outside low/high"))
    for field in ["open", "high", "low", "close"]:
        if float(row[field]) <= 0:
            issues.append(_issue("ERROR", "NON_POSITIVE_PRICE", row, f"{field} is non-positive"))
    for field in ["volume", "oi"]:
        if float(row[field]) < 0:
            issues.append(_issue("ERROR", "NEGATIVE_VALUE", row, f"{field} is negative"))
    return issues


def _group_issues(symbol: str, timeframe: str, rows: List[dict[str, Any]]) -> List[dict[str, Any]]:
    issues: List[dict[str, Any]] = []
    ordered = sorted(rows, key=lambda item: int(item["close_ts"]))
    counts = Counter(int(row["close_ts"]) for row in ordered)
    for close_ts, count in sorted(counts.items()):
        if count > 1:
            issues.append(
                {
                    "severity": "ERROR",
                    "code": "DUPLICATE_CLOSE_TS",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "close_ts": close_ts,
                    "message": f"Duplicate close_ts appears {count} times",
                }
            )
    expected = EXPECTED_INTERVAL_SECONDS.get(timeframe)
    if expected is None:
        issues.append(
            {
                "severity": "WARN",
                "code": "UNKNOWN_TIMEFRAME_INTERVAL",
                "symbol": symbol,
                "timeframe": timeframe,
                "close_ts": None,
                "message": "Cannot audit interval for unknown timeframe",
            }
        )
        return issues
    previous: Optional[int] = None
    for row in ordered:
        close_ts = int(row["close_ts"])
        if previous is not None:
            delta = close_ts - previous
            if delta <= 0:
                issues.append(_interval_issue("ERROR", "NON_INCREASING_CLOSE_TS", symbol, timeframe, close_ts, delta, expected))
            elif delta != expected:
                severity = "WARN" if delta > 0 else "ERROR"
                issues.append(_interval_issue(severity, "INTERVAL_GAP", symbol, timeframe, close_ts, delta, expected))
        previous = close_ts
    return issues


def _interval_issue(severity: str, code: str, symbol: str, timeframe: str, close_ts: int, delta: int, expected: int) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "symbol": symbol,
        "timeframe": timeframe,
        "close_ts": close_ts,
        "message": f"delta={delta}, expected={expected}",
    }


def _issue(severity: str, code: str, row: dict[str, Any], message: str) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "symbol": row.get("symbol"),
        "timeframe": row.get("timeframe"),
        "close_ts": row.get("close_ts"),
        "message": message,
    }


def _status(severity_counts: Counter) -> str:
    if severity_counts.get("ERROR", 0):
        return "FAIL"
    if severity_counts.get("WARN", 0):
        return "WARN"
    return "PASS"
