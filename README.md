# OpenClaw Perp Analyst v0.2 Foundation

This repository implements the v0.2 foundation from `OpenClaw Perp Analyst 合并架构 v0.1`.

Scope:
- freeze event and ticket JSON schemas
- initialize SQLite storage
- keep versioned risk, contract, and threshold config
- calculate z-score, robust z-score, and percentile factors
- replay Phase -1 public historical rows without LLMs
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
