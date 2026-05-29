from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.costing import estimate_ticket_cost


DEFAULT_OUT = ROOT / "reports" / "ticket_cost_estimate.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Estimate review-only per-ticket LLM cost from local prompt budgets.")
    parser.add_argument("--config", default=str(ROOT / "config" / "cost_budget.yaml"))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    estimate = estimate_ticket_cost(Path(args.config))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(estimate, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(f"Wrote ticket cost estimate to {out}")
    print(f"status={estimate['status']}")
    print(f"total_cost_usd_estimate={estimate['total_cost_usd_estimate']}")
    print("private_api=not_used")


if __name__ == "__main__":
    main()
