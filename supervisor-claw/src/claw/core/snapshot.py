"""Compressed HTML snapshot store: data/snapshots/<YYYYMMDD>/<source>/<sha>.html.gz."""

from __future__ import annotations

import gzip
import hashlib
from datetime import date
from pathlib import Path

from ..config import get_settings


def write_snapshot(source: str, url: str, content: bytes) -> tuple[str, Path]:
    """Returns (sha256_hex, file_path)."""
    sha = hashlib.sha256(content).hexdigest()
    root = get_settings().claw_snapshot_dir / date.today().strftime("%Y%m%d") / source
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{sha}.html.gz"
    if not path.exists():
        with gzip.open(path, "wb") as f:
            f.write(content)
        # write a sibling .url file so we can map sha -> original URL without DB
        (path.with_suffix(".url")).write_text(url, encoding="utf-8")
    return sha, path
