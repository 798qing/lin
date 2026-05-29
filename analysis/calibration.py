from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any, Dict, Iterable, List, Tuple


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
