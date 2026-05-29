from __future__ import annotations

from pathlib import Path
from typing import Any

from analysis.simple_yaml import load_yaml


ROOT = Path(__file__).resolve().parents[1]


def audit_prompts(
    prompt_dir: Path = ROOT / "config" / "prompts",
    boundary_path: Path = ROOT / "config" / "prompt_boundaries.yaml",
) -> dict[str, Any]:
    boundary = load_yaml(boundary_path)
    prompt_reports = []
    for path in sorted(prompt_dir.glob("*.md")):
        prompt_reports.append(_audit_prompt(path, boundary))
    issues = [issue for report in prompt_reports for issue in report["issues"]]
    if any(issue["severity"] == "FAIL" for issue in issues):
        status = "FAIL"
    elif issues:
        status = "WARN"
    else:
        status = "PASS"
    return {
        "version": boundary["version"],
        "status": status,
        "prompt_count": len(prompt_reports),
        "prompts": prompt_reports,
        "issue_count": len(issues),
        "private_api": "not_used",
    }


def _audit_prompt(path: Path, boundary: dict[str, Any]) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8").lower()
    issues = []
    for group, terms in boundary["required_terms"].items():
        if not any(str(term).lower() in text for term in terms):
            issues.append(
                {
                    "severity": "FAIL",
                    "code": f"REQUIRED_{group.upper()}_MISSING",
                    "message": f"{path.name} does not state the {group} boundary.",
                }
            )
    for term in boundary["forbidden_terms"]:
        if str(term).lower() in text:
            issues.append(
                {
                    "severity": "FAIL",
                    "code": "FORBIDDEN_TERM_PRESENT",
                    "message": f"{path.name} contains forbidden term: {term}",
                }
            )
    return {
        "path": str(path),
        "status": "FAIL" if any(issue["severity"] == "FAIL" for issue in issues) else "PASS",
        "issues": issues,
    }
