from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple


def enrich_rows(rows: Iterable[dict[str, Any]], atr_period: int = 14, ema_fast: int = 20, ema_slow: int = 50) -> List[dict[str, Any]]:
    grouped: Dict[Tuple[str, str], List[dict[str, Any]]] = {}
    for row in rows:
        key = (row["symbol"], row["timeframe"])
        grouped.setdefault(key, []).append(dict(row))

    enriched: List[dict[str, Any]] = []
    for key in sorted(grouped):
        items = sorted(grouped[key], key=lambda item: item["close_ts"])
        previous_close: Optional[float] = None
        true_ranges: List[float] = []
        ema_fast_value: Optional[float] = None
        ema_slow_value: Optional[float] = None
        for item in items:
            true_range = _true_range(item["high"], item["low"], previous_close)
            true_ranges.append(true_range)
            item["true_range"] = true_range
            item["atr"] = _rolling_average(true_ranges, atr_period)
            ema_fast_value = _ema_next(item["close"], ema_fast_value, ema_fast)
            ema_slow_value = _ema_next(item["close"], ema_slow_value, ema_slow)
            item["ema_fast"] = ema_fast_value
            item["ema_slow"] = ema_slow_value
            item["ema_fast_period"] = ema_fast
            item["ema_slow_period"] = ema_slow
            item["atr_period"] = atr_period
            previous_close = item["close"]
            enriched.append(item)
    return sorted(enriched, key=lambda item: (item["symbol"], item["timeframe"], item["close_ts"]))


def _true_range(high: float, low: float, previous_close: Optional[float]) -> float:
    high = float(high)
    low = float(low)
    if previous_close is None:
        return max(high - low, 0.0001)
    return max(high - low, abs(high - previous_close), abs(low - previous_close), 0.0001)


def _rolling_average(values: List[float], period: int) -> float:
    if period <= 0:
        raise ValueError("period must be positive")
    window = values[-period:]
    return sum(window) / len(window)


def _ema_next(close: float, previous_ema: Optional[float], period: int) -> float:
    if period <= 0:
        raise ValueError("period must be positive")
    close = float(close)
    if previous_ema is None:
        return close
    alpha = 2.0 / (period + 1.0)
    return close * alpha + previous_ema * (1.0 - alpha)
