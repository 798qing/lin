from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis import db
from analysis.calibration import summarize_triggers
from analysis.costing import estimate_ticket_cost
from analysis.data_quality import audit_rows
from analysis.factors import rolling_factor_stats, simple_regime
from analysis.indicators import enrich_rows
from analysis.risk_validator import RiskInput, validate_risk
from analysis.schema_validation import validate_ticket
from analysis.simple_yaml import load_yaml
from collectors.manifest import default_manifest_path, file_sha256, load_manifest_for_csv
from watchdog.event_builder import build_event


DEFAULT_SAMPLE = ROOT / "data" / "sample_ohlcv.csv"
DEFAULT_REPORT = ROOT / "reports" / "phase_minus_1_report.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay public historical rows through v0.2 Watchdog rules.")
    parser.add_argument("--csv", default=str(DEFAULT_SAMPLE), help="CSV with timestamp,symbol,timeframe,open,high,low,close,volume,funding,oi")
    parser.add_argument("--db", default=str(db.DEFAULT_DB), help="SQLite DB path")
    parser.add_argument("--report", default=str(DEFAULT_REPORT), help="JSON report path")
    args = parser.parse_args()

    db_path = Path(args.db)
    db.migrate(db_path)
    thresholds = load_yaml(ROOT / "config" / "thresholds.yaml")
    rows = _load_rows(Path(args.csv))
    if not rows:
        raise SystemExit(f"No rows found in {args.csv}")

    rows = enrich_rows(
        rows,
        atr_period=int(thresholds["rolling_windows"]["atr_periods"]),
        ema_fast=int(thresholds["regime_policy"]["trend"]["ema_fast"]),
        ema_slow=int(thresholds["regime_policy"]["trend"]["ema_slow"]),
    )
    report = replay(rows, thresholds, db_path, source_path=Path(args.csv))
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))


def replay(rows: List[dict[str, Any]], thresholds: dict[str, Any], db_path: Path, source_path: Path) -> dict[str, Any]:
    db.migrate(db_path)
    if rows and "atr" not in rows[0]:
        rows = enrich_rows(
            rows,
            atr_period=int(thresholds.get("rolling_windows", {}).get("atr_periods", 14)),
            ema_fast=int(thresholds.get("regime_policy", {}).get("trend", {}).get("ema_fast", 20)),
            ema_slow=int(thresholds.get("regime_policy", {}).get("trend", {}).get("ema_slow", 50)),
    )
    data_quality = audit_rows(rows)
    raw_refs = _raw_refs(source_path)
    raw_manifest = load_manifest_for_csv(source_path)
    raw_integrity = _raw_integrity(raw_refs, raw_manifest)
    histories: Dict[str, Dict[str, Deque[float]]] = defaultdict(lambda: defaultdict(lambda: deque(maxlen=240)))
    trigger_counts: Dict[str, int] = defaultdict(int)
    trigger_records: List[dict[str, Any]] = []
    inserted_events = 0
    duplicate_events = 0
    tickets = 0

    with db.connect(db_path) as conn:
        for row in rows:
            key = f"{row['symbol']}|{row['timeframe']}"
            hist = histories[key]
            atr_stats = rolling_factor_stats(row["atr"], hist["atr"], min_periods=5)
            funding_stats = rolling_factor_stats(row["funding"], hist["funding"], min_periods=5)
            oi_change = _pct_change(row["oi"], hist["oi"][-24] if len(hist["oi"]) >= 24 else None)
            oi_stats = rolling_factor_stats(oi_change or 0.0, hist["oi_change"], min_periods=5)
            regime = simple_regime(
                row.get("ema_fast"),
                row.get("ema_slow"),
                row["atr"],
                atr_stats.percentile,
                high_volatility_threshold=float(thresholds["regime_policy"]["high_volatility"]["atr_percentile_min"]),
                range_spread_atr=float(thresholds["regime_policy"]["range"]["ema_spread_max_atr"]),
            )
            trigger_evidence = _trigger_evidence(row, thresholds, funding_stats, oi_stats, atr_stats)
            for evidence in trigger_evidence:
                trigger_type = evidence["trigger_type"]
                trigger_counts[trigger_type] += 1
                trigger_records.append(
                    {
                        "symbol": row["symbol"],
                        "timeframe": row["timeframe"],
                        "close_ts": row["close_ts"],
                        "close": row["close"],
                        "trigger_type": trigger_type,
                        "regime": regime,
                        "evidence": evidence,
                    }
                )
                snapshot = {
                    "symbol": row["symbol"],
                    "timeframe": row["timeframe"],
                    "close_ts": row["close_ts"],
                    "features": {
                        "close": row["close"],
                        "ema_fast": row.get("ema_fast"),
                        "ema_slow": row.get("ema_slow"),
                        "atr": row["atr"],
                        "true_range": row.get("true_range"),
                        "atr_percentile": atr_stats.percentile,
                        "funding": row["funding"],
                        "funding_zscore": funding_stats.zscore,
                        "funding_robust_zscore": funding_stats.robust_zscore,
                        "funding_percentile": funding_stats.percentile,
                        "oi": row["oi"],
                        "oi_24h_pct": oi_change,
                        "oi_24h_percentile": oi_stats.percentile,
                    },
                    "trigger_evidence": evidence,
                    "regime": regime,
                    "data_fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "notes": ["Phase -1 replay; no LLM and no private API."],
                }
                event = build_event(
                    symbol=row["symbol"],
                    timeframe=row["timeframe"],
                    trigger_type=trigger_type,
                    close_ts=row["close_ts"],
                    market_snapshot=snapshot,
                    raw_refs=raw_refs,
                    thresholds_version=thresholds["version"],
                )
                if db.insert_event(conn, event):
                    inserted_events += 1
                else:
                    duplicate_events += 1
                    continue
                risk_output = validate_risk(
                    RiskInput(
                        symbol=row["symbol"],
                        action="WATCH",
                        entry_price=row["close"],
                        atr=row["atr"],
                        trend_confirmed=regime == "trend",
                    ),
                    ROOT / "config" / "risk_rules.yaml",
                    ROOT / "config" / "contract_specs.yaml",
                )
                run_id = db.insert_analysis_run(
                    conn=conn,
                    event_id=event["event_id"],
                    prompt_version="phase_minus_1_no_prompt",
                    agent_versions={"watchdog": "v0.2", "risk_validator": "v0.2"},
                    thresholds_version=thresholds["version"],
                    risk_rules_version=risk_output["risk_rules_version"],
                    status="RISK_VALIDATED",
                )
                db.insert_risk_check(conn, run_id, risk_output)
                ticket = _watch_ticket(event, risk_output, run_id)
                validate_ticket(ticket)
                db.insert_ticket(conn, run_id, ticket)
                db.finish_analysis_run(conn, run_id, "COMPLETED", cost_usd=0.0)
                tickets += 1

            hist["close"].append(row["close"])
            hist["atr"].append(row["atr"])
            hist["funding"].append(row["funding"])
            hist["oi"].append(row["oi"])
            if oi_change is not None:
                hist["oi_change"].append(oi_change)
    return {
        "thresholds_version": thresholds["version"],
        "rows": len(rows),
        "inserted_events": inserted_events,
        "duplicate_events": duplicate_events,
        "tickets": tickets,
        "trigger_counts": dict(sorted(trigger_counts.items())),
        "trigger_evidence_summary": _trigger_evidence_summary(trigger_records),
        "data_quality": data_quality,
        "raw_refs": raw_refs,
        "raw_manifest": raw_manifest,
        "raw_integrity": raw_integrity,
        "ticket_cost_estimate": estimate_ticket_cost(),
        "outcome_summary": summarize_triggers(rows, trigger_records),
        "private_api": "not_used",
    }


def _raw_refs(source_path: Path) -> dict[str, str]:
    refs = {"csv": str(source_path)}
    if source_path.exists():
        refs["csv_sha256"] = file_sha256(source_path)
    manifest_path = default_manifest_path(source_path)
    if manifest_path.exists():
        refs["manifest"] = str(manifest_path)
        refs["manifest_sha256"] = file_sha256(manifest_path)
    return refs


def _raw_integrity(raw_refs: dict[str, str], raw_manifest: Optional[dict[str, Any]]) -> dict[str, Any]:
    issues: List[dict[str, str]] = []
    actual_csv_hash = raw_refs.get("csv_sha256")
    if not actual_csv_hash:
        issues.append({"severity": "FAIL", "code": "CSV_MISSING", "message": "CSV file is missing or cannot be hashed."})
    if raw_manifest is None:
        issues.append({"severity": "WARN", "code": "MANIFEST_MISSING", "message": "Raw data manifest is missing."})
    else:
        expected_csv_hash = raw_manifest.get("csv_sha256")
        if not expected_csv_hash:
            issues.append(
                {
                    "severity": "WARN",
                    "code": "MANIFEST_CSV_HASH_MISSING",
                    "message": "Raw data manifest does not include csv_sha256.",
                }
            )
        elif actual_csv_hash and expected_csv_hash != actual_csv_hash:
            issues.append(
                {
                    "severity": "FAIL",
                    "code": "CSV_SHA256_MISMATCH",
                    "message": "CSV sha256 does not match the raw data manifest.",
                }
            )
    if any(issue["severity"] == "FAIL" for issue in issues):
        status = "FAIL"
    elif issues:
        status = "WARN"
    else:
        status = "PASS"
    return {"status": status, "issues": issues}


def _load_rows(path: Path) -> List[dict[str, Any]]:
    if not path.exists():
        _write_sample(path)
    rows: List[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for item in csv.DictReader(handle):
            rows.append(
                {
                    "close_ts": int(item["timestamp"]),
                    "symbol": item["symbol"],
                    "timeframe": item["timeframe"],
                    "open": float(item["open"]),
                    "high": float(item["high"]),
                    "low": float(item["low"]),
                    "close": float(item["close"]),
                    "volume": float(item["volume"]),
                    "funding": float(item["funding"]),
                    "oi": float(item["oi"]),
                }
            )
    return rows


def _write_sample(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    base_ts = 1761955200
    for index in range(36):
        close = 100.0 + index * 0.6
        funding = 0.0001 + (0.0015 if index == 18 else 0.0)
        oi = 100000 + index * 500 + (12000 if index == 24 else 0)
        high = close + 1.0 + (4.0 if index == 30 else 0.0)
        low = close - 1.0 - (4.0 if index == 30 else 0.0)
        rows.append(
            {
                "timestamp": base_ts + index * 3600,
                "symbol": "SOL-USDT-SWAP",
                "timeframe": "1H",
                "open": close - 0.3,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1000 + index * 10,
                "funding": funding,
                "oi": oi,
                "ema_fast": close - 0.5,
                "ema_slow": close - 1.5,
            }
        )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _trigger_evidence(row: dict[str, Any], thresholds: dict[str, Any], funding_stats: Any, oi_stats: Any, atr_stats: Any) -> List[dict[str, Any]]:
    triggers: List[dict[str, Any]] = []
    enabled_timeframes = thresholds.get("triggers", {}).get("candle_close", {}).get("enabled_timeframes", [row["timeframe"]])
    if row["timeframe"] == "1H":
        triggers.append(
            {
                "trigger_type": "CANDLE_CLOSE_1H",
                "reason": "Scheduled 1H candle close.",
                "metrics": {"timeframe": row["timeframe"], "close_ts": row["close_ts"]},
                "thresholds": {"enabled_timeframes": enabled_timeframes},
                "distribution_position": "scheduled",
            }
        )
    if row["timeframe"] == "4H":
        triggers.append(
            {
                "trigger_type": "CANDLE_CLOSE_4H",
                "reason": "Scheduled 4H candle close.",
                "metrics": {"timeframe": row["timeframe"], "close_ts": row["close_ts"]},
                "thresholds": {"enabled_timeframes": enabled_timeframes},
                "distribution_position": "scheduled",
            }
        )
    funding_cfg = thresholds["triggers"]["funding_spike"]
    funding_conditions = {
        "zscore": _condition(funding_stats.zscore, funding_cfg["zscore_min"]),
        "robust_zscore": _condition(funding_stats.robust_zscore, funding_cfg["robust_zscore_min"]),
        "percentile": _condition(funding_stats.percentile, funding_cfg["percentile_min"]),
    }
    if any(item["passed"] for item in funding_conditions.values()):
        triggers.append(
            {
                "trigger_type": "FUNDING_SPIKE",
                "reason": "Funding is elevated versus rolling history.",
                "metrics": {
                    "funding": row["funding"],
                    "funding_zscore": funding_stats.zscore,
                    "funding_robust_zscore": funding_stats.robust_zscore,
                    "funding_percentile": funding_stats.percentile,
                    "window_size": funding_stats.window_size,
                },
                "thresholds": {
                    "zscore_min": float(funding_cfg["zscore_min"]),
                    "robust_zscore_min": float(funding_cfg["robust_zscore_min"]),
                    "percentile_min": float(funding_cfg["percentile_min"]),
                },
                "conditions": funding_conditions,
                "distribution_position": _distribution_position(funding_stats.percentile),
            }
        )
    oi_cfg = thresholds["triggers"]["oi_pulse"]
    oi_condition = _condition(oi_stats.percentile, oi_cfg["percentile_min"])
    if oi_condition["passed"]:
        triggers.append(
            {
                "trigger_type": "OI_PULSE",
                "reason": "Open interest change is elevated versus rolling history.",
                "metrics": {
                    "oi": row["oi"],
                    "oi_24h_pct": oi_stats.value,
                    "oi_24h_percentile": oi_stats.percentile,
                    "window_size": oi_stats.window_size,
                },
                "thresholds": {
                    "pct_change_window_hours": int(oi_cfg["pct_change_window_hours"]),
                    "percentile_min": float(oi_cfg["percentile_min"]),
                },
                "conditions": {"percentile": oi_condition},
                "distribution_position": _distribution_position(oi_stats.percentile),
            }
        )
    volatility_cfg = thresholds["triggers"]["volatility_breakout"]
    atr_condition = _condition(atr_stats.percentile, volatility_cfg["atr_percentile_min"])
    if atr_condition["passed"]:
        triggers.append(
            {
                "trigger_type": "VOLATILITY_BREAKOUT",
                "reason": "ATR is elevated versus rolling history.",
                "metrics": {
                    "atr": row["atr"],
                    "atr_percentile": atr_stats.percentile,
                    "window_size": atr_stats.window_size,
                },
                "thresholds": {"atr_percentile_min": float(volatility_cfg["atr_percentile_min"])},
                "conditions": {"percentile": atr_condition},
                "distribution_position": _distribution_position(atr_stats.percentile),
            }
        )
    return triggers


def _condition(value: Any, threshold: Any) -> dict[str, Any]:
    threshold_value = float(threshold)
    passed = value is not None and float(value) >= threshold_value
    return {"value": value, "threshold": threshold_value, "operator": ">=", "passed": passed}


def _distribution_position(percentile: Any) -> str:
    if percentile is None:
        return "insufficient_history"
    value = float(percentile)
    if value >= 0.95:
        return "top_5pct"
    if value >= 0.90:
        return "top_10pct"
    if value >= 0.75:
        return "top_quartile"
    if value <= 0.25:
        return "bottom_quartile"
    return "middle_range"


def _trigger_evidence_summary(trigger_records: List[dict[str, Any]]) -> dict[str, Any]:
    summary: Dict[str, dict[str, Any]] = {}
    for record in trigger_records:
        trigger_type = record["trigger_type"]
        evidence = record.get("evidence", {})
        bucket = summary.setdefault(
            trigger_type,
            {
                "records": 0,
                "distribution_positions": defaultdict(int),
                "condition_pass_counts": defaultdict(int),
            },
        )
        bucket["records"] += 1
        bucket["distribution_positions"][evidence.get("distribution_position", "unknown")] += 1
        for name, condition in evidence.get("conditions", {}).items():
            if condition.get("passed"):
                bucket["condition_pass_counts"][name] += 1
    return {
        trigger_type: {
            "records": bucket["records"],
            "distribution_positions": dict(sorted(bucket["distribution_positions"].items())),
            "condition_pass_counts": dict(sorted(bucket["condition_pass_counts"].items())),
        }
        for trigger_type, bucket in sorted(summary.items())
    }


def _pct_change(current: float, previous: Any) -> Any:
    if previous in (None, 0):
        return None
    return (current - float(previous)) / float(previous)


def _watch_ticket(event: dict[str, Any], risk_output: dict[str, Any], run_id: int) -> dict[str, Any]:
    symbol_slug = event["symbol"].split("-")[0].lower()
    timeframe_slug = event["timeframe"].lower()
    created = datetime.utcfromtimestamp(event["close_ts"]).strftime("%Y_%m_%d")
    return {
        "ticket_id": f"tkt_{created}_{symbol_slug}_{timeframe_slug}_{run_id:03d}",
        "event_id": event["event_id"],
        "event_schema_version": event["event_schema_version"],
        "snapshot_hash": event["snapshot_hash"],
        "status": risk_output["verdict"],
        "action": "WATCH",
        "symbol": event["symbol"],
        "timeframe": event["timeframe"],
        "self_reported_confidence": "LOW",
        "prompt_version": "phase_minus_1_no_prompt",
        "thresholds_version": event["thresholds_version"],
        "risk_rules_version": risk_output["risk_rules_version"],
        "agent_versions": {"watchdog": "v0.2", "risk_validator": "v0.2"},
        "entry_zone": "Phase -1 replay only; no entry proposal.",
        "invalid_condition": "No trade setup; replay event for threshold calibration.",
        "risk": {
            "max_loss_pct": risk_output["max_loss_pct"],
            "max_loss_amount": risk_output["max_loss_amount"],
            "suggested_leverage_cap": risk_output["leverage_cap"],
            "stop_distance_atr": 1.5,
            "suggested_position_size": risk_output["suggested_position_size"],
            "liq_safety_margin": risk_output["liq_safety_margin"]["status"],
            "position_size_comment": "Replay only; actual order is manual and out of scope.",
        },
        "bull_case": [],
        "bear_case": [],
        "why_not": risk_output["reasons"] or ["Phase -1 does not produce directional advice."],
        "human_checklist": ["Confirm this replay row is public historical data.", "Do not treat replay tickets as trade signals."],
    }


if __name__ == "__main__":
    main()
