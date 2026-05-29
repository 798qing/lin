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
python3 -m gateway.openclaw_bridge data/latest_event.json --out reports/latest_event.md
```

Fetch public OKX rows, then replay them:

```bash
python3 scripts/fetch_okx_public.py --symbol BTC-USDT-SWAP --symbol ETH-USDT-SWAP --symbol SOL-USDT-SWAP --bar 1H --limit 100 --out data/okx_public_ohlcv.csv
python3 main.py replay --csv data/okx_public_ohlcv.csv
```

The OKX collector uses public market endpoints only. It has no API key,
secret, passphrase, signing code, account endpoint, position endpoint, order
endpoint, or execution path.
