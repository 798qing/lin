from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


OKX_BASE_URL = "https://www.okx.com"
USER_AGENT = "openclaw-perp-analyst/0.2"


class OkxPublicError(RuntimeError):
    pass


@dataclass(frozen=True)
class Candle:
    timestamp: int
    symbol: str
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class FundingRate:
    timestamp: int
    symbol: str
    funding: float


@dataclass(frozen=True)
class OpenInterest:
    timestamp: int
    symbol: str
    oi: float


class OkxPublicClient:
    """Small OKX public REST client.

    This client intentionally has no API key, secret, passphrase, signing, or
    account endpoints. It only reads public market data.
    """

    def __init__(self, base_url: str = OKX_BASE_URL, timeout: int = 20, sleep_seconds: float = 0.12) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.sleep_seconds = sleep_seconds

    def get_json(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        query = urllib.parse.urlencode({key: value for key, value in params.items() if value is not None})
        url = f"{self.base_url}{path}?{query}" if query else f"{self.base_url}{path}"
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("code") != "0":
            raise OkxPublicError(f"OKX public API error {payload.get('code')}: {payload.get('msg')}")
        time.sleep(self.sleep_seconds)
        return payload

    def history_candles(
        self,
        inst_id: str,
        bar: str = "1H",
        limit: int = 100,
        before: Optional[int] = None,
        after: Optional[int] = None,
    ) -> List[Candle]:
        payload = self.get_json(
            "/api/v5/market/history-candles",
            {"instId": inst_id, "bar": bar, "limit": limit, "before": before, "after": after},
        )
        candles = [_parse_candle(item, inst_id, bar) for item in payload.get("data", [])]
        return sorted(candles, key=lambda item: item.timestamp)

    def funding_rate_history(
        self,
        inst_id: str,
        limit: int = 100,
        before: Optional[int] = None,
        after: Optional[int] = None,
    ) -> List[FundingRate]:
        payload = self.get_json(
            "/api/v5/public/funding-rate-history",
            {"instId": inst_id, "limit": limit, "before": before, "after": after},
        )
        rates = [
            FundingRate(timestamp=int(item["fundingTime"]) // 1000, symbol=inst_id, funding=float(item["realizedRate"]))
            for item in payload.get("data", [])
        ]
        return sorted(rates, key=lambda item: item.timestamp)

    def open_interest(self, inst_id: str) -> OpenInterest:
        payload = self.get_json("/api/v5/public/open-interest", {"instType": "SWAP", "instId": inst_id})
        data = payload.get("data") or []
        if not data:
            raise OkxPublicError(f"No open-interest data for {inst_id}")
        item = data[0]
        return OpenInterest(timestamp=int(item.get("ts", "0")) // 1000, symbol=inst_id, oi=float(item["oi"]))


def merge_rows(
    candles: Iterable[Candle],
    funding_rates: Iterable[FundingRate],
    open_interest: OpenInterest,
) -> List[dict[str, Any]]:
    funding_by_ts = sorted(funding_rates, key=lambda item: item.timestamp)
    rows: List[dict[str, Any]] = []
    last_funding = 0.0
    funding_index = 0
    for candle in sorted(candles, key=lambda item: item.timestamp):
        while funding_index < len(funding_by_ts) and funding_by_ts[funding_index].timestamp <= candle.timestamp:
            last_funding = funding_by_ts[funding_index].funding
            funding_index += 1
        rows.append(
            {
                "timestamp": candle.timestamp,
                "symbol": candle.symbol,
                "timeframe": candle.timeframe,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
                "funding": last_funding,
                "oi": open_interest.oi,
            }
        )
    return rows


def _parse_candle(item: List[str], inst_id: str, bar: str) -> Candle:
    return Candle(
        timestamp=int(item[0]) // 1000,
        symbol=inst_id,
        timeframe=bar,
        open=float(item[1]),
        high=float(item[2]),
        low=float(item[3]),
        close=float(item[4]),
        volume=float(item[5]),
    )
