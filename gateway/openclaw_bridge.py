from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Optional

from analysis.schema_validation import validate_event


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OPENCLAW = "/Users/lin/.local/node-v24.16.0-darwin-arm64/bin/openclaw"


def render_single_event_markdown(event: dict[str, Any]) -> str:
    validate_event(event)
    features = event["market_snapshot"].get("features", {})
    lines = [
        f"# Perp Analyst Event Report: {event['symbol']} {event['timeframe']}",
        "",
        f"- event_id: `{event['event_id']}`",
        f"- trigger_type: `{event['trigger_type']}`",
        f"- close_ts: `{event['close_ts']}`",
        f"- thresholds_version: `{event['thresholds_version']}`",
        f"- snapshot_hash: `{event['snapshot_hash']}`",
        "- private_api: `not_used`",
        "",
        "## Frozen Snapshot",
        "",
        f"- regime: `{event['market_snapshot'].get('regime', 'unknown')}`",
    ]
    for key in sorted(features):
        lines.append(f"- {key}: `{features[key]}`")
    lines.extend(
        [
            "",
            "## Boundaries",
            "",
            "- This report is generated from the frozen event snapshot only.",
            "- No account, position, order, or private exchange API is used.",
            "- Directional analysis and full Agent routing are intentionally out of scope for v0.2 foundation.",
        ]
    )
    return "\n".join(lines) + "\n"


def run_openclaw_cli(
    event: dict[str, Any],
    openclaw_bin: str = DEFAULT_OPENCLAW,
    timeout: int = 120,
    dry_run: bool = True,
) -> subprocess.CompletedProcess[str]:
    validate_event(event)
    command = [
        openclaw_bin,
        "agent",
        "--agent",
        "perp_cio",
        "--session-key",
        "agent:perp_cio:perp-watchdog",
        "--message",
        json.dumps(event, ensure_ascii=False, sort_keys=True),
        "--timeout",
        str(timeout),
        "--json",
    ]
    if dry_run:
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"dry_run": True, "command": command}), stderr="")
    return subprocess.run(command, text=True, capture_output=True, timeout=timeout + 5, check=False)


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Render or send one frozen event to OpenClaw CLI.")
    parser.add_argument("event_json", help="Path to event JSON")
    parser.add_argument("--send", action="store_true", help="Actually call OpenClaw CLI. Default renders Markdown only.")
    parser.add_argument("--openclaw-bin", default=DEFAULT_OPENCLAW)
    parser.add_argument("--out", default=None, help="Markdown output path")
    args = parser.parse_args(argv)

    event = json.loads(Path(args.event_json).read_text(encoding="utf-8"))
    if args.send:
        result = run_openclaw_cli(event, openclaw_bin=args.openclaw_bin, dry_run=False)
        print(result.stdout)
        if result.stderr:
            print(result.stderr)
        raise SystemExit(result.returncode)
    markdown = render_single_event_markdown(event)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(markdown, encoding="utf-8")
    print(markdown)


if __name__ == "__main__":
    main()
