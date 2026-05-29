from __future__ import annotations

from collections import defaultdict, deque
from statistics import mean
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

from analysis.factors import rolling_factor_stats


DEFAULT_HORIZONS = (1, 4, 12)


def summarize_triggers(
    rows: Iterable[dict[str, Any]],
    trigger_records: Iterable[dict[str, Any]],
    horizons: Tuple[int, ...] = DEFAULT_HORIZONS,
    train_fraction: float = 0.7,
) -> dict[str, Any]:
    ordered_rows = sorted(rows, key=lambda item: (item["symbol"], item["timeframe"], item["close_ts"]))
    index_by_key = {
        (row["symbol"], row["timeframe"], row["close_ts"]): index for index, row in enumerate(ordered_rows)
    }
    split_ts = _split_timestamp(ordered_rows, train_fraction)
    outcomes: List[dict[str, Any]] = []
    for record in trigger_records:
        key = (record["symbol"], record["timeframe"], record["close_ts"])
        index = index_by_key.get(key)
        if index is None:
            continue
        row = ordered_rows[index]
        for horizon in horizons:
            future_index = index + horizon
            if future_index >= len(ordered_rows):
                outcomes.append(_missing_outcome(record, horizon, row, split_ts))
                continue
            future = ordered_rows[future_index]
            if future["symbol"] != row["symbol"] or future["timeframe"] != row["timeframe"]:
                outcomes.append(_missing_outcome(record, horizon, row, split_ts))
                continue
            outcomes.append(_outcome(record, horizon, row, ordered_rows[index + 1 : future_index + 1], future, split_ts))
    return {
        "horizons": list(horizons),
        "train_fraction": train_fraction,
        "split_close_ts": split_ts,
        "by_trigger": _aggregate(outcomes, ["trigger_type"]),
        "by_trigger_and_split": _aggregate(outcomes, ["trigger_type", "split"]),
        "by_trigger_and_regime": _aggregate(outcomes, ["trigger_type", "regime"]),
    }


def suggest_thresholds(rows: Iterable[dict[str, Any]], base_thresholds: dict[str, Any]) -> dict[str, Any]:
    ordered_rows = sorted(rows, key=lambda item: (item["symbol"], item["timeframe"], item["close_ts"]))
    windows = base_thresholds.get("rolling_windows", {})
    funding_window = max(5, int(windows.get("funding_days", 90)) * 3)
    oi_window = max(5, int(windows.get("oi_days", 30)) * 24)
    funding_zscores: List[float] = []
    funding_robust_zscores: List[float] = []
    funding_percentiles: List[float] = []
    oi_changes: List[float] = []
    oi_percentiles: List[float] = []
    atr_percentiles: List[float] = []
    by_key: Dict[str, Dict[str, Deque[float]]] = defaultdict(lambda: defaultdict(lambda: deque(maxlen=max(funding_window, oi_window, 240))))

    for row in ordered_rows:
        key = f"{row['symbol']}|{row['timeframe']}"
        hist = by_key[key]
        funding_stats = rolling_factor_stats(row["funding"], hist["funding"], min_periods=5)
        atr_stats = rolling_factor_stats(row["atr"], hist["atr"], min_periods=5)
        oi_change = _lookback_pct_change(row["oi"], hist["oi"], 24)
        oi_stats = rolling_factor_stats(oi_change or 0.0, hist["oi_change"], min_periods=5)
        _append_if_number(funding_zscores, funding_stats.zscore)
        _append_if_number(funding_robust_zscores, funding_stats.robust_zscore)
        _append_if_number(funding_percentiles, funding_stats.percentile)
        _append_if_number(oi_changes, oi_change)
        _append_if_number(oi_percentiles, oi_stats.percentile)
        _append_if_number(atr_percentiles, atr_stats.percentile)
        hist["funding"].append(row["funding"])
        hist["atr"].append(row["atr"])
        hist["oi"].append(row["oi"])
        if oi_change is not None:
            hist["oi_change"].append(oi_change)

    return {
        "version": base_thresholds.get("version", "thresholds_v0.1"),
        "status": "candidate_from_phase_minus_1_replay",
        "calibration": {
            "phase": "phase_minus_1_candidate",
            "source": "public_replay_csv",
            "sample_rows": len(ordered_rows),
            "review_required": True,
            "note": "Candidate only; do not auto-promote without manual review and larger samples.",
        },
        "rolling_windows": base_thresholds.get("rolling_windows", {}),
        "triggers": {
            "funding_spike": {
                "zscore_min": _max_or_default(2.0, _quantile(funding_zscores, 0.95)),
                "robust_zscore_min": _max_or_default(2.5, _quantile(funding_robust_zscores, 0.95)),
                "percentile_min": _max_or_default(0.95, _quantile(funding_percentiles, 0.95)),
            },
            "oi_pulse": {
                "pct_change_window_hours": base_thresholds.get("triggers", {}).get("oi_pulse", {}).get("pct_change_window_hours", 24),
                "pct_change_min": _quantile(oi_changes, 0.95),
                "percentile_min": _max_or_default(0.95, _quantile(oi_percentiles, 0.95)),
            },
            "volatility_breakout": {
                "atr_percentile_min": _max_or_default(0.90, _quantile(atr_percentiles, 0.90)),
            },
            "candle_close": base_thresholds.get("triggers", {}).get("candle_close", {}),
        },
        "regime_policy": base_thresholds.get("regime_policy", {}),
        "feature_distribution": {
            "funding_zscore_p95": _quantile(funding_zscores, 0.95),
            "funding_robust_zscore_p95": _quantile(funding_robust_zscores, 0.95),
            "funding_percentile_p95": _quantile(funding_percentiles, 0.95),
            "oi_24h_pct_p95": _quantile(oi_changes, 0.95),
            "oi_24h_percentile_p95": _quantile(oi_percentiles, 0.95),
            "atr_percentile_p90": _quantile(atr_percentiles, 0.90),
        },
    }


def dump_thresholds_yaml(candidate: dict[str, Any]) -> str:
    return _dump_yaml(candidate).rstrip() + "\n"


def _split_timestamp(rows: List[dict[str, Any]], train_fraction: float) -> int:
    if not rows:
        return 0
    bounded = min(max(train_fraction, 0.1), 0.9)
    index = int((len(rows) - 1) * bounded)
    return int(rows[index]["close_ts"])


def _missing_outcome(record: dict[str, Any], horizon: int, row: dict[str, Any], split_ts: int) -> dict[str, Any]:
    return {
        "trigger_type": record["trigger_type"],
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "regime": record.get("regime", "unknown"),
        "split": "train" if row["close_ts"] <= split_ts else "test",
        "horizon": horizon,
        "has_future": False,
        "return_pct": None,
        "max_favorable_pct": None,
        "max_adverse_pct": None,
    }


def _outcome(
    record: dict[str, Any],
    horizon: int,
    row: dict[str, Any],
    future_window: List[dict[str, Any]],
    future: dict[str, Any],
    split_ts: int,
) -> dict[str, Any]:
    entry = float(row["close"])
    highs = [float(item["high"]) for item in future_window]
    lows = [float(item["low"]) for item in future_window]
    close_return = _pct(float(future["close"]), entry)
    return {
        "trigger_type": record["trigger_type"],
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "regime": record.get("regime", "unknown"),
        "split": "train" if row["close_ts"] <= split_ts else "test",
        "horizon": horizon,
        "has_future": True,
        "return_pct": close_return,
        "max_favorable_pct": _pct(max(highs), entry),
        "max_adverse_pct": _pct(min(lows), entry),
    }


def _aggregate(outcomes: List[dict[str, Any]], dimensions: List[str]) -> dict[str, Any]:
    buckets: Dict[Tuple[Any, ...], List[dict[str, Any]]] = defaultdict(list)
    for outcome in outcomes:
        key = tuple(outcome[dimension] for dimension in dimensions)
        buckets[key].append(outcome)
    result: dict[str, Any] = {}
    for key, items in sorted(buckets.items(), key=lambda item: item[0]):
        label = "|".join(str(part) for part in key)
        result[label] = _aggregate_bucket(items)
    return result


def _aggregate_bucket(items: List[dict[str, Any]]) -> dict[str, Any]:
    by_horizon: Dict[int, List[dict[str, Any]]] = defaultdict(list)
    for item in items:
        by_horizon[int(item["horizon"])].append(item)
    horizon_summaries = {str(horizon): _horizon_summary(horizon_items) for horizon, horizon_items in sorted(by_horizon.items())}
    return {
        "trigger_count": max((summary["events"] for summary in horizon_summaries.values()), default=0),
        "horizons": horizon_summaries,
    }


def _horizon_summary(items: List[dict[str, Any]]) -> dict[str, Any]:
    complete = [item for item in items if item["has_future"]]
    returns = [item["return_pct"] for item in complete]
    favorable = [item["max_favorable_pct"] for item in complete]
    adverse = [item["max_adverse_pct"] for item in complete]
    return {
        "events": len(items),
        "complete": len(complete),
        "missing_future": len(items) - len(complete),
        "avg_return_pct": _rounded_mean(returns),
        "avg_max_favorable_pct": _rounded_mean(favorable),
        "avg_max_adverse_pct": _rounded_mean(adverse),
        "positive_rate": _positive_rate(returns),
        "false_signal_rate": _false_signal_rate(favorable, adverse),
    }


def _pct(value: float, base: float) -> float:
    if base == 0:
        return 0.0
    return (value - base) / base * 100.0


def _rounded_mean(values: List[float]) -> Any:
    if not values:
        return None
    return round(mean(values), 6)


def _positive_rate(values: List[float]) -> Any:
    if not values:
        return None
    return round(sum(1 for value in values if value > 0) / len(values), 6)


def _false_signal_rate(favorable: List[float], adverse: List[float]) -> Any:
    if not favorable or not adverse:
        return None
    false_signals = 0
    for fav, adv in zip(favorable, adverse):
        if fav <= abs(adv):
            false_signals += 1
    return round(false_signals / len(favorable), 6)


def _lookback_pct_change(current: float, history: Deque[float], periods: int) -> Optional[float]:
    if len(history) < periods:
        return None
    previous = history[-periods]
    if previous == 0:
        return None
    return (float(current) - float(previous)) / float(previous)


def _append_if_number(values: List[float], value: Optional[float]) -> None:
    if value is not None:
        values.append(float(value))


def _quantile(values: List[float], quantile: float) -> Any:
    if not values:
        return None
    ordered = sorted(values)
    bounded = min(max(quantile, 0.0), 1.0)
    index = int(round((len(ordered) - 1) * bounded))
    return round(ordered[index], 8)


def _max_or_default(default: float, value: Any) -> float:
    if value is None:
        return default
    return max(default, float(value))


def _dump_yaml(value: Any, indent: int = 0) -> str:
    prefix = " " * indent
    if isinstance(value, dict):
        lines: List[str] = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.append(_dump_yaml(item, indent + 2))
            else:
                lines.append(f"{prefix}{key}: {_format_scalar(item)}")
        return "\n".join(lines)
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.append(_dump_yaml(item, indent + 2))
            else:
                lines.append(f"{prefix}- {_format_scalar(item)}")
        return "\n".join(lines)
    return f"{prefix}{_format_scalar(value)}"


def _format_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, float):
        return str(round(value, 8))
    return str(value)
