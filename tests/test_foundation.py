import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collectors.okx_public import Candle, FundingRate, OkxPublicClient, OpenInterest, merge_rows
from analysis.factors import percentile_rank, robust_zscore, simple_regime, zscore
from analysis.indicators import enrich_rows
from analysis.risk_validator import APPROVED, REJECTED, WATCH_ONLY, RiskInput, validate_risk
from analysis.schema_validation import validate_event
from scripts.replay_phase_minus_1 import DEFAULT_SAMPLE, _load_rows, replay
from watchdog.event_builder import build_event


ROOT = Path(__file__).resolve().parents[1]


class FoundationTests(unittest.TestCase):
    def test_factor_stats(self):
        history = [1, 2, 3, 4, 5]
        self.assertAlmostEqual(zscore(6, history), 2.1213203435596424)
        self.assertAlmostEqual(robust_zscore(6, history), 2.0235)
        self.assertEqual(percentile_rank(5, history), 0.9)
        self.assertEqual(simple_regime(10, 11, 10, 0.9), "high_volatility")

    def test_indicator_enrichment(self):
        rows = [
            {
                "close_ts": 1,
                "symbol": "SOL-USDT-SWAP",
                "timeframe": "1H",
                "open": 10.0,
                "high": 12.0,
                "low": 9.0,
                "close": 11.0,
                "volume": 100.0,
                "funding": 0.0,
                "oi": 1000.0,
            },
            {
                "close_ts": 2,
                "symbol": "SOL-USDT-SWAP",
                "timeframe": "1H",
                "open": 11.0,
                "high": 15.0,
                "low": 10.0,
                "close": 14.0,
                "volume": 100.0,
                "funding": 0.0,
                "oi": 1000.0,
            },
        ]
        enriched = enrich_rows(rows, atr_period=14, ema_fast=2, ema_slow=3)
        self.assertEqual(enriched[0]["true_range"], 3.0)
        self.assertEqual(enriched[1]["true_range"], 5.0)
        self.assertEqual(enriched[1]["atr"], 4.0)
        self.assertGreater(enriched[1]["ema_fast"], enriched[0]["ema_fast"])

    def test_event_builder_validates_schema(self):
        event = build_event(
            symbol="SOL-USDT-SWAP",
            timeframe="1H",
            trigger_type="MANUAL_REVIEW",
            close_ts=1761955200,
            market_snapshot={
                "symbol": "SOL-USDT-SWAP",
                "timeframe": "1H",
                "close_ts": 1761955200,
                "features": {"close": 100.0},
                "regime": "unknown",
            },
            raw_refs={"unit": "test"},
            thresholds_version="thresholds_v0.1",
        )
        validate_event(event)
        self.assertTrue(event["event_id"].startswith("evt_"))
        self.assertTrue(event["snapshot_hash"].startswith("sha256_"))

    def test_risk_validator_degrade_chain(self):
        base = RiskInput(
            symbol="SOL-USDT-SWAP",
            action="CONSIDER_LONG",
            entry_price=100,
            atr=2,
            trend_confirmed=True,
        )
        approved = validate_risk(base, ROOT / "config/risk_rules.yaml", ROOT / "config/contract_specs.yaml")
        self.assertEqual(approved["verdict"], APPROVED)
        technical_failed = validate_risk(
            RiskInput(**{**base.__dict__, "technical_status": "failed"}),
            ROOT / "config/risk_rules.yaml",
            ROOT / "config/contract_specs.yaml",
        )
        self.assertEqual(technical_failed["verdict"], REJECTED)
        derivatives_failed = validate_risk(
            RiskInput(**{**base.__dict__, "derivatives_status": "failed"}),
            ROOT / "config/risk_rules.yaml",
            ROOT / "config/contract_specs.yaml",
        )
        self.assertEqual(derivatives_failed["verdict"], WATCH_ONLY)

    def test_replay_writes_traceable_records(self):
        rows = _load_rows(DEFAULT_SAMPLE)[:8]
        thresholds = {
            "version": "thresholds_v0.1",
            "regime_policy": {
                "high_volatility": {"atr_percentile_min": 0.85},
                "range": {"ema_spread_max_atr": 0.5},
            },
            "triggers": {
                "funding_spike": {"zscore_min": 2.0, "robust_zscore_min": 2.5, "percentile_min": 0.95},
                "oi_pulse": {"percentile_min": 0.95},
                "volatility_breakout": {"atr_percentile_min": 0.9},
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            report = replay(rows, thresholds, Path(tmpdir) / "test.db", DEFAULT_SAMPLE)
        self.assertEqual(report["private_api"], "not_used")
        self.assertGreater(report["inserted_events"], 0)

    def test_okx_public_request_has_no_private_auth_headers(self):
        seen = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"code":"0","data":[]}'

        def fake_urlopen(request, timeout):
            seen["headers"] = dict(request.header_items())
            seen["url"] = request.full_url
            return FakeResponse()

        client = OkxPublicClient(base_url="https://example.test", sleep_seconds=0)
        with patch("urllib.request.urlopen", fake_urlopen):
            client.get_json("/api/v5/market/history-candles", {"instId": "BTC-USDT-SWAP"})
        header_keys = {key.lower() for key in seen["headers"]}
        self.assertIn("user-agent", header_keys)
        self.assertNotIn("ok-access-key", header_keys)
        self.assertNotIn("ok-access-sign", header_keys)
        self.assertNotIn("ok-access-passphrase", header_keys)
        self.assertIn("/api/v5/market/history-candles", seen["url"])

    def test_okx_public_merge_rows(self):
        rows = merge_rows(
            candles=[
                Candle(1000, "BTC-USDT-SWAP", "1H", 1, 2, 0.5, 1.5, 10),
                Candle(2000, "BTC-USDT-SWAP", "1H", 1.5, 2.5, 1, 2, 11),
            ],
            funding_rates=[FundingRate(1500, "BTC-USDT-SWAP", 0.0002)],
            open_interest=OpenInterest(2000, "BTC-USDT-SWAP", 12345),
        )
        self.assertEqual(rows[0]["funding"], 0.0)
        self.assertEqual(rows[1]["funding"], 0.0002)
        self.assertEqual(rows[1]["oi"], 12345)

    def test_okx_public_candle_pagination(self):
        pages = [
            {
                "code": "0",
                "data": [
                    ["3000000", "3", "4", "2", "3.5", "30"],
                    ["2000000", "2", "3", "1", "2.5", "20"],
                ],
            },
            {
                "code": "0",
                "data": [
                    ["2000000", "2", "3", "1", "2.5", "20"],
                    ["1000000", "1", "2", "0.5", "1.5", "10"],
                ],
            },
        ]
        seen_urls = []

        def fake_get_json(path, params):
            seen_urls.append(params)
            return pages.pop(0) if pages else {"code": "0", "data": []}

        client = OkxPublicClient(sleep_seconds=0)
        with patch.object(client, "get_json", fake_get_json):
            candles = client.history_candles_pages("BTC-USDT-SWAP", bar="1H", limit=2, pages=2)
        self.assertEqual([item.timestamp for item in candles], [1000, 2000, 3000])
        self.assertIsNone(seen_urls[0]["after"])
        self.assertEqual(seen_urls[1]["after"], 2000000)


if __name__ == "__main__":
    unittest.main()
