from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256_" + digest.hexdigest()


def default_manifest_path(csv_path: Path) -> Path:
    return csv_path.with_suffix(csv_path.suffix + ".manifest.json")


def build_public_manifest(
    csv_path: Path,
    source: str,
    symbols: List[str],
    timeframe: str,
    limit: int,
    pages: int,
    row_count: int,
    extra: Optional[Dict[str, Any]] = None,
) -> dict[str, Any]:
    manifest = {
        "schema_version": "raw_manifest_v0.1",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": source,
        "csv_path": str(csv_path),
        "csv_sha256": file_sha256(csv_path),
        "symbols": symbols,
        "timeframe": timeframe,
        "limit": limit,
        "pages": pages,
        "row_count": row_count,
        "private_api": "not_used",
    }
    if extra:
        manifest["extra"] = extra
    return manifest


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def load_manifest_for_csv(csv_path: Path) -> Optional[dict[str, Any]]:
    path = default_manifest_path(csv_path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
