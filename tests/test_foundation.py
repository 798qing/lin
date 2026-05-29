import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from analysis.calibration import dump_thresholds_yaml, summarize_triggers, suggest_thresholds
from analysis.data_quality import audit_rows
from analysis.factors import percentile_rank, robust_zscore, simple_regime, zscore
from analysis.indicators import enrich_rows
from analysis.risk_validator import APPROVED, REJECTED, WATCH_ONLY, RiskInput, validate_risk
from analysis.schema_validation import validate_event
from analysis.simple_yaml import load_yaml
from collectors.manifest import build_public_manifest, default_manifest_path, file_sha256, write_manifest
from collectors.okx_public import Candle, FundingRate, OkxPublicClient, OpenInterest, merge_rows
from scripts.export_trace import export_trace
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
        self.assertIn("outcome_summary", report)
        self.assertIn("data_quality", report)

    def test_export_trace_reconstructs_event_chain(self):
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
            db_path = Path(tmpdir) / "test.db"
            replay(rows, thresholds, db_path, DEFAULT_SAMPLE)
            trace = export_trace(db_path)
        self.assertEqual(trace["private_api"], "not_used")
        self.assertEqual(trace["traceability"]["status"], "PASS")
        self.assertEqual(trace["event"]["thresholds_version"], "thresholds_v0.1")
        self.assertTrue(trace["analysis_runs"])
        self.assertTrue(trace["analysis_runs"][0]["risk_checks"])
        self.assertTrue(trace["analysis_runs"][0]["tickets"])

    def test_replay_includes_manifest_refs(self):
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
            csv_path = Path(tmpdir) / "sample.csv"
            csv_path.write_text(DEFAULT_SAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
            manifest = build_public_manifest(csv_path, "unit-test", ["SOL-USDT-SWAP"], "1H", 8, 1, len(rows))
            write_manifest(default_manifest_path(csv_path), manifest)
            report = replay(rows, thresholds, Path(tmpdir) / "test.db", csv_path)
        self.assertIn("manifest", report["raw_refs"])
        self.assertTrue(report["raw_refs"]["csv_sha256"].startswith("sha256_"))
        self.assertTrue(report["raw_refs"]["manifest_sha256"].startswith("sha256_"))
        self.assertEqual(report["raw_manifest"]["csv_sha256"], report["raw_refs"]["csv_sha256"])
        self.assertEqual(report["raw_integrity"]["status"], "PASS")
        self.assertEqual(report["raw_manifest"]["private_api"], "not_used")

    def test_replay_flags_raw_hash_mismatch(self):
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
            csv_path = Path(tmpdir) / "sample.csv"
            csv_path.write_text(DEFAULT_SAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
            manifest = build_public_manifest(csv_path, "unit-test", ["SOL-USDT-SWAP"], "1H", 8, 1, len(rows))
            write_manifest(default_manifest_path(csv_path), manifest)
            csv_path.write_text(DEFAULT_SAMPLE.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            report = replay(rows, thresholds, Path(tmpdir) / "test.db", csv_path)
        codes = {issue["code"] for issue in report["raw_integrity"]["issues"]}
        self.assertEqual(report["raw_integrity"]["status"], "FAIL")
        self.assertIn("CSV_SHA256_MISMATCH", codes)

    def test_file_sha256_has_stable_prefix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "raw.csv"
            path.write_text("timestamp,symbol\n1,SOL-USDT-SWAP\n", encoding="utf-8")
            digest = file_sha256(path)
        self.assertEqual(len(digest), len("sha256_") + 64)
        self.assertTrue(digest.startswith("sha256_"))

    def test_data_quality_audit(self):
        rows = [
            {
                "close_ts": 1000,
                "symbol": "SOL-USDT-SWAP",
                "timeframe": "1H",
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "volume": 100.0,
                "funding": 0.0,
                "oi": 1000.0,
            },
            {
                "close_ts": 1000,
                "symbol": "SOL-USDT-SWAP",
                "timeframe": "1H",
                "open": 12.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "volume": 100.0,
                "funding": 0.0,
                "oi": 1000.0,
            },
            {
                "close_ts": 8200,
                "symbol": "SOL-USDT-SWAP",
                "timeframe": "1H",
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "volume": 100.0,
                "funding": 0.0,
                "oi": 1000.0,
            },
        ]
        report = audit_rows(rows)
        codes = {issue["code"] for issue in report["issues"]}
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("DUPLICATE_CLOSE_TS", codes)
        self.assertIn("OHLC_OPEN_OUT_OF_RANGE", codes)
        self.assertIn("INTERVAL_GAP", codes)

    def test_calibration_outcome_summary(self):
        rows = [
            {
                "close_ts": index,
                "symbol": "SOL-USDT-SWAP",
                "timeframe": "1H",
                "open": 100 + index,
                "high": 101 + index,
                "low": 99 + index,
                "close": 100 + index,
                "volume": 1000,
                "funding": 0.0,
                "oi": 1000.0,
            }
            for index in range(1, 8)
        ]
        triggers = [
            {"symbol": "SOL-USDT-SWAP", "timeframe": "1H", "close_ts": 1, "trigger_type": "CANDLE_CLOSE_1H", "regime": "trend"},
            {"symbol": "SOL-USDT-SWAP", "timeframe": "1H", "close_ts": 6, "trigger_type": "CANDLE_CLOSE_1H", "regime": "range"},
        ]
        report = summarize_triggers(rows, triggers, horizons=(1, 4), train_fraction=0.5)
        trigger_summary = report["by_trigger"]["CANDLE_CLOSE_1H"]["horizons"]
        self.assertEqual(trigger_summary["1"]["events"], 2)
        self.assertEqual(trigger_summary["1"]["complete"], 2)
        self.assertEqual(trigger_summary["4"]["missing_future"], 1)
        self.assertIn("CANDLE_CLOSE_1H|train", report["by_trigger_and_split"])
        self.assertIn("CANDLE_CLOSE_1H|test", report["by_trigger_and_split"])

    def test_threshold_candidate_round_trip(self):
        base = load_yaml(ROOT / "config" / "thresholds.yaml")
        rows = enrich_rows(_load_rows(DEFAULT_SAMPLE), atr_period=14, ema_fast=20, ema_slow=50)
        candidate = suggest_thresholds(rows, base)
        candidate["data_quality"] = audit_rows(rows)
        self.assertTrue(candidate["calibration"]["review_required"])
        self.assertEqual(candidate["status"], "candidate_from_phase_minus_1_replay")
        self.assertIn("feature_distribution", candidate)
        yaml_text = dump_thresholds_yaml(candidate)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "candidate.yaml"
            path.write_text(yaml_text, encoding="utf-8")
            parsed = load_yaml(path)
        self.assertEqual(parsed["version"], "thresholds_v0.1")
        self.assertTrue(parsed["calibration"]["review_required"])
        self.assertIn("data_quality", parsed)

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
