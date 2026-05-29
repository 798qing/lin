from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.calibration import dump_thresholds_yaml, suggest_thresholds
from analysis.data_quality import audit_rows
from analysis.indicators import enrich_rows
from analysis.simple_yaml import load_yaml
from scripts.replay_phase_minus_1 import DEFAULT_SAMPLE, _load_rows


DEFAULT_OUT = ROOT / "reports" / "thresholds_candidate.yaml"
DEFAULT_JSON = ROOT / "reports" / "thresholds_candidate_summary.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a Phase -1 candidate thresholds YAML from public replay CSV.")
    parser.add_argument("--csv", default=str(DEFAULT_SAMPLE))
    parser.add_argument("--base", default=str(ROOT / "config" / "thresholds.yaml"))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--json-summary", default=str(DEFAULT_JSON))
    args = parser.parse_args()

    base = load_yaml(args.base)
    rows = enrich_rows(
        _load_rows(Path(args.csv)),
        atr_period=int(base["rolling_windows"]["atr_periods"]),
        ema_fast=int(base["regime_policy"]["trend"]["ema_fast"]),
        ema_slow=int(base["regime_policy"]["trend"]["ema_slow"]),
    )
    candidate = suggest_thresholds(rows, base)
    candidate["data_quality"] = audit_rows(rows)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dump_thresholds_yaml(candidate), encoding="utf-8")

    json_summary = Path(args.json_summary)
    json_summary.parent.mkdir(parents=True, exist_ok=True)
    json_summary.write_text(json.dumps(candidate, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(f"Wrote candidate thresholds to {out}")
    print(f"Wrote candidate summary to {json_summary}")
    print("review_required=true")
    print("private_api=not_used")


if __name__ == "__main__":
    main()
