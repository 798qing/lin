from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from statistics import mean, median, pstdev
from typing import Iterable, List, Optional, Union


Number = Union[float, int]


@dataclass(frozen=True)
class FactorStats:
    value: float
    window_size: int
    zscore: Optional[float]
    robust_zscore: Optional[float]
    percentile: Optional[float]


def clean_numbers(values: Iterable[Optional[Number]]) -> List[float]:
    return [float(value) for value in values if value is not None and isfinite(float(value))]


def zscore(value: Number, history: Iterable[Optional[Number]], min_periods: int = 5) -> Optional[float]:
    series = clean_numbers(history)
    if len(series) < min_periods:
        return None
    sigma = pstdev(series)
    if sigma == 0:
        return 0.0
    return (float(value) - mean(series)) / sigma


def robust_zscore(value: Number, history: Iterable[Optional[Number]], min_periods: int = 5) -> Optional[float]:
    series = clean_numbers(history)
    if len(series) < min_periods:
        return None
    med = median(series)
    absolute_deviations = [abs(item - med) for item in series]
    mad = median(absolute_deviations)
    if mad == 0:
        return 0.0
    return 0.6745 * (float(value) - med) / mad


def percentile_rank(value: Number, history: Iterable[Optional[Number]], min_periods: int = 5) -> Optional[float]:
    series = clean_numbers(history)
    if len(series) < min_periods:
        return None
    below = sum(1 for item in series if item < float(value))
    equal = sum(1 for item in series if item == float(value))
    return (below + 0.5 * equal) / len(series)


def rolling_factor_stats(value: Number, history: Iterable[Optional[Number]], min_periods: int = 5) -> FactorStats:
    series = clean_numbers(history)
    return FactorStats(
        value=float(value),
        window_size=len(series),
        zscore=zscore(value, series, min_periods=min_periods),
        robust_zscore=robust_zscore(value, series, min_periods=min_periods),
        percentile=percentile_rank(value, series, min_periods=min_periods),
    )


def simple_regime(
    ema_fast: Optional[Number],
    ema_slow: Optional[Number],
    atr: Optional[Number],
    atr_percentile: Optional[Number],
    high_volatility_threshold: float = 0.85,
    range_spread_atr: float = 0.5,
) -> str:
    if atr_percentile is not None and float(atr_percentile) >= high_volatility_threshold:
        return "high_volatility"
    if ema_fast is None or ema_slow is None or atr in (None, 0):
        return "unknown"
    spread = abs(float(ema_fast) - float(ema_slow)) / float(atr)
    if spread <= range_spread_atr:
        return "range"
    return "trend"
