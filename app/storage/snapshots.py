"""Optional compressed source snapshots and lossless legacy archive access."""

import gzip
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile


def save_snapshot(
    directory: Path,
    content: bytes,
    *,
    symbol: str,
    currency: str | None,
    method: str,
    captured_at: datetime,
) -> Path:
    def safe(value: str) -> str:
        # Encode unsafe characters instead of dropping them (e.g. ^BTC versus BTC).
        return re.sub(r"[^A-Za-z0-9_.-]", lambda m: f"~{ord(m[0]):02X}", value)

    label = "_".join(safe(v) for v in (symbol, currency, method) if v)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{label}_{captured_at:%Y-%m-%dT%H%M%S_%fZ}.html.gz"
    path.write_bytes(gzip.compress(content, mtime=0))
    return path


def read_snapshot(path: str | Path, archive_dir: Path) -> bytes:
    """Keep existing DB snapshot paths valid after moving sources into a ZIP."""
    original = Path(path)
    if original.is_file():
        content = original.read_bytes()
        return gzip.decompress(content) if original.suffix == ".gz" else content
    keys = {str(original).replace("\\", "/"), str(original.resolve()).replace("\\", "/")}
    for archive in sorted(archive_dir.glob("*.zip")):
        with ZipFile(archive) as file:
            if "snapshot_index.json" not in file.namelist():
                continue
            index = json.loads(file.read("snapshot_index.json"))
            entry = next((index[k] for k in keys if k in index), None)
            if entry is not None:
                content = file.read(entry["member"])
                if hashlib.sha256(content).hexdigest() != entry["sha256"]:
                    raise ValueError(f"Archived snapshot checksum differs: {original}")
                return content
    raise FileNotFoundError(f"Snapshot is not available locally or in archives: {original}")
