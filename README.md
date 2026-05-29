# OpenClaw Perp Analyst v0.2 Foundation

This repository implements the v0.2 foundation from `OpenClaw Perp Analyst 合并架构 v0.1`.

Scope:
- freeze event and ticket JSON schemas
- initialize SQLite storage
- keep versioned risk, contract, and threshold config
- calculate z-score, robust z-score, and percentile factors
- replay Phase -1 public historical rows without LLMs
- fetch OKX public market rows for Phase -1 CSV input
- run Risk Validator without private account APIs
- render a single-event Markdown report before wiring OpenClaw CLI

Out of scope for v0.2:
- private exchange APIs
- account, position, order, or execution access
- automatic trading
- full multi-agent orchestration

Quick check:

```bash
python3 main.py init-db
python3 main.py replay
python3 scripts/export_latest_event.py --out data/latest_event.json
python3 main.py trace --out reports/latest_trace.json
python3 -m gateway.openclaw_bridge data/latest_event.json --out reports/latest_event.md
```

Fetch public OKX rows, then replay them:

```bash
python3 scripts/fetch_okx_public.py --symbol BTC-USDT-SWAP --symbol ETH-USDT-SWAP --symbol SOL-USDT-SWAP --bar 1H --limit 100 --pages 3 --out data/okx_public_ohlcv.csv
python3 main.py replay --csv data/okx_public_ohlcv.csv
python3 scripts/calibrate_thresholds.py --csv data/okx_public_ohlcv.csv --out reports/thresholds_candidate.yaml
```

The OKX collector uses public market endpoints only. It has no API key,
secret, passphrase, signing code, account endpoint, position endpoint, order
endpoint, or execution path.

`calibrate_thresholds.py` writes a review-only candidate file. It does not
overwrite `config/thresholds.yaml`.

`fetch_okx_public.py` also writes `<csv>.manifest.json` with public data source
metadata and sha256 hashes. Replay reports include that manifest in `raw_refs`
when present and flag raw-data hash mismatches in `raw_integrity`.

`main.py trace` exports a read-only audit trace for the latest event, including
the frozen event input, analysis runs, risk checks, tickets, version fields, and
traceability status.
