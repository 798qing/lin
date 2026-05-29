from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis import db
from analysis.calibration import summarize_triggers
from analysis.data_quality import audit_rows
from analysis.factors import rolling_factor_stats, simple_regime
from analysis.indicators import enrich_rows
from analysis.risk_validator import RiskInput, validate_risk
from analysis.schema_validation import validate_ticket
from analysis.simple_yaml import load_yaml
from collectors.manifest import default_manifest_path, load_manifest_for_csv
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
            triggers = _triggers(row, thresholds, funding_stats, oi_stats, atr_stats)
            for trigger_type in triggers:
                trigger_counts[trigger_type] += 1
                trigger_records.append(
                    {
                        "symbol": row["symbol"],
                        "timeframe": row["timeframe"],
                        "close_ts": row["close_ts"],
                        "close": row["close"],
                        "trigger_type": trigger_type,
                        "regime": regime,
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
        "data_quality": data_quality,
        "raw_refs": raw_refs,
        "raw_manifest": load_manifest_for_csv(source_path),
        "outcome_summary": summarize_triggers(rows, trigger_records),
        "private_api": "not_used",
    }


def _raw_refs(source_path: Path) -> dict[str, str]:
    refs = {"csv": str(source_path)}
    manifest_path = default_manifest_path(source_path)
    if manifest_path.exists():
        refs["manifest"] = str(manifest_path)
    return refs


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


def _triggers(row: dict[str, Any], thresholds: dict[str, Any], funding_stats: Any, oi_stats: Any, atr_stats: Any) -> List[str]:
    triggers: List[str] = []
    if row["timeframe"] == "1H":
        triggers.append("CANDLE_CLOSE_1H")
    if row["timeframe"] == "4H":
        triggers.append("CANDLE_CLOSE_4H")
    funding_cfg = thresholds["triggers"]["funding_spike"]
    if (
        (funding_stats.zscore is not None and funding_stats.zscore >= float(funding_cfg["zscore_min"]))
        or (funding_stats.robust_zscore is not None and funding_stats.robust_zscore >= float(funding_cfg["robust_zscore_min"]))
        or (funding_stats.percentile is not None and funding_stats.percentile >= float(funding_cfg["percentile_min"]))
    ):
        triggers.append("FUNDING_SPIKE")
    if oi_stats.percentile is not None and oi_stats.percentile >= float(thresholds["triggers"]["oi_pulse"]["percentile_min"]):
        triggers.append("OI_PULSE")
    if atr_stats.percentile is not None and atr_stats.percentile >= float(thresholds["triggers"]["volatility_breakout"]["atr_percentile_min"]):
        triggers.append("VOLATILITY_BREAKOUT")
    return triggers


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
