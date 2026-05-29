from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.prompt_audit import audit_prompts


DEFAULT_OUT = ROOT / "reports" / "prompt_boundary_audit.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit Agent prompts for read-only frozen-snapshot boundaries.")
    parser.add_argument("--prompt-dir", default=str(ROOT / "config" / "prompts"))
    parser.add_argument("--boundaries", default=str(ROOT / "config" / "prompt_boundaries.yaml"))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    report = audit_prompts(Path(args.prompt_dir), Path(args.boundaries))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(f"Wrote prompt boundary audit to {out}")
    print(f"status={report['status']}")
    print("private_api=not_used")


if __name__ == "__main__":
    main()
