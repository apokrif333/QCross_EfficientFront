"""Compact legacy ingestion artifacts without changing the database or observations.

Default: show a plan. --apply archives and verifies source bytes before removing
loose copies, and moves current outputs to their dedicated directories.
"""

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def digest(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def within(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"File operation escapes the data directory: {path}")
    if path.is_symlink():
        raise ValueError(f"Refusing a symlink: {path}")
    return resolved


def plan(data_dir: Path) -> tuple[list[tuple[Path, Path]], list[Path]]:
    root = data_dir.resolve()
    moves = []
    caches = [
        (
            "raw/lazyportfolio/backtester/offered_pairs.json",
            "cache/lazyportfolio/backtester_pairs.json",
        ),
        (
            "raw/lazyportfolio/returns/public_etf_page_index.json",
            "cache/lazyportfolio/individual_pages.json",
        ),
    ]
    for source, target in caches:
        if (root / source).is_file():
            moves.append((root / source, root / target))
    reports = {
        "lazyportfolio_returns_coverage.csv",
        "lazyportfolio_returns_coverage.json",
        "lazyportfolio_returns_unresolved.csv",
        "backtester_pilot_validation.json",
        "backtester_preservation_audit.json",
        "lazyportfolio_discovery_report.json",
    }
    keep = {
        ".gitkeep",
        "lazyportfolio_instruments.csv",
        "lazyportfolio_instruments.json",
        "lazyportfolio_monthly_returns.csv",
        "lazyportfolio_monthly_returns.csv.metadata.json",
        "lazyportfolio_monthly_returns.parquet",
        "lazyportfolio_monthly_returns.parquet.metadata.json",
        "lazyportfolio_monthly_returns_wide.parquet",
        "lazyportfolio_monthly_returns_wide.parquet.metadata.json",
    }
    archive = []
    for source in sorted((root / "exports").glob("*")):
        if not source.is_file() or source.name in keep:
            continue
        if source.suffix == ".log":
            moves.append((source, root / "logs" / "history" / source.name))
        elif source.name in reports:
            moves.append((source, root / "reports" / source.name))
        elif source.name.startswith(("VTI_USD_monthly_returns.", "VTV_USD_monthly_returns.")):
            moves.append((source, root / "exports" / "pairs" / source.name))
        else:
            archive.append(source)
    moved_sources = {s for s, _ in moves}
    archive.extend(
        p for p in sorted((root / "raw").rglob("*")) if p.is_file() and p not in moved_sources
    )
    for source, target in moves:
        within(root, source)
        within(root, target)
        if target.exists():
            raise FileExistsError(f"Will not overwrite an existing output: {target}")
    for source in archive:
        within(root, source)
    return moves, archive


def compact(data_dir: Path, *, apply: bool = False) -> dict:
    root = data_dir.resolve()
    moves, sources = plan(root)
    result = {
        "apply": apply,
        "archived_files": len(sources),
        "archived_original_bytes": sum(p.stat().st_size for p in sources),
        "moved_files": len(moves),
        "moves": [
            {"from": str(s.relative_to(root)), "to": str(t.relative_to(root))} for s, t in moves
        ],
    }
    if not apply or not (sources or moves):
        return result
    database = root / "qcross.db"
    before = digest(database) if database.is_file() else None
    if sources:
        folder = within(root, root / "archive")
        folder.mkdir(parents=True, exist_ok=True)
        path = within(
            root, folder / f"legacy_ingestion_{datetime.now(UTC):%Y-%m-%dT%H%M%S_%fZ}.zip"
        )
        temporary = path.with_suffix(".zip.tmp")
        index = {}
        with ZipFile(temporary, "x", compression=ZIP_DEFLATED, compresslevel=6) as archive:
            for source in sources:
                member = "data/" + source.relative_to(root).as_posix()
                checksum = digest(source)
                archive.write(source, member)
                entry = {"member": member, "sha256": checksum, "bytes": source.stat().st_size}
                index[str(source).replace("\\", "/")] = entry
                index[member] = entry
            archive.writestr("snapshot_index.json", json.dumps(index, indent=2))
        # Verify every archived byte before unlinking any source, including backup DBs.
        with ZipFile(temporary) as archive:
            for source in sources:
                entry = index[str(source).replace("\\", "/")]
                checksum = hashlib.sha256(archive.read(entry["member"])).hexdigest()
                if checksum != entry["sha256"] or digest(source) != checksum:
                    raise ValueError(f"Archive/source checksum mismatch: {source}")
        temporary.replace(path)
        result["archive"] = str(path.relative_to(root))
        result["archive_bytes"] = path.stat().st_size
        for source in sources:
            within(root, source).unlink()
    for source, target in moves:
        target = within(root, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Both endpoints were checked; refuse collisions again before moving.
        if target.exists():
            raise FileExistsError(target)
        within(root, source).rename(target)
    raw = root / "raw"
    for directory in sorted(raw.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if directory.is_dir() and not any(directory.iterdir()):
            within(root, directory).rmdir()
    if raw.is_dir() and not any(raw.iterdir()):
        within(root, raw).rmdir()
    after = digest(database) if database.is_file() else None
    if before != after:
        raise ValueError("Database changed during cleanup; investigate a concurrent writer")
    result["database_sha256_before"] = before
    result["database_sha256_after"] = after
    reports = within(root, root / "reports")
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "storage_cleanup.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def prune(data_dir: Path, *, apply: bool = False) -> dict:
    """Keep operational files and only the sources referenced by stored series."""
    from app.storage.snapshots import read_snapshot

    root = data_dir.resolve()
    database = within(root, root / "qcross.db")
    before = digest(database)
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        rows = connection.execute(
            "SELECT s.id, i.source_symbol, s.currency, s.extraction_method, s.metadata, "
            "s.validation_status, i.is_active FROM return_series s "
            "JOIN instruments i ON i.id=s.instrument_id WHERE s.source='lazyportfolioetf'"
        ).fetchall()
    records = [(row, json.loads(row[4])) for row in rows]
    references = [(row, meta) for row, meta in records if meta.get("snapshot_path")]
    archive_dir = within(root, root / "archive")
    target = within(root, archive_dir / "validated_sources.zip")
    obsolete = []
    for folder in ("logs/history", "exports/pairs", "reports/pilots"):
        directory = within(root, root / folder)
        obsolete.extend(p for p in directory.rglob("*") if p.is_file())
    for name in (
        "backtester_preservation_audit.json",
        "storage_cleanup.json",
        "storage_preservation.json",
    ):
        path = root / "reports" / name
        if path.is_file():
            obsolete.append(path)
    obsolete.extend(archive_dir.glob("legacy_ingestion_*.zip"))
    for path in obsolete:
        within(root, path)
    urls = {
        row[1]: meta["source_url"]
        for row, meta in records
        if row[3] == "individual_page"
        and row[5] == "validated"
        and row[6]
        and meta.get("validation", {}).get("passed")
        and meta.get("source_url")
    }
    result = {
        "apply": apply,
        "obsolete_files": len(obsolete),
        "referenced_sources": len(references),
        "working_individual_urls": len(urls),
    }
    if not apply:
        return result
    index = {}
    if references:
        archive_dir.mkdir(parents=True, exist_ok=True)
        temporary = within(root, archive_dir / ".validated_sources.zip.tmp")
        if temporary.exists():
            raise FileExistsError(temporary)
        try:
            with ZipFile(temporary, "x", compression=ZIP_DEFLATED, compresslevel=6) as archive:
                for row, meta in references:
                    original = Path(meta["snapshot_path"])
                    within(root, original)
                    content = read_snapshot(original, archive_dir)
                    checksum = hashlib.sha256(content).hexdigest()
                    if checksum != meta.get("snapshot_sha256"):
                        raise ValueError(f"Stored source checksum mismatch for {row[1]} {row[2]}")
                    label = "_".join(str(value) for value in (row[1], row[2], row[3]))
                    label = re.sub(r"[^A-Za-z0-9_.-]", lambda m: f"~{ord(m[0]):02X}", label)
                    member = f"sources/{label}.html"
                    if member in archive.namelist():
                        raise ValueError(f"Ambiguous source identity: {member}")
                    archive.writestr(member, content)
                    entry = {"member": member, "sha256": checksum, "bytes": len(content)}
                    for key in (str(original), str(original.resolve())):
                        index[key.replace("\\", "/")] = entry
                archive.writestr("snapshot_index.json", json.dumps(index, indent=2))
            with ZipFile(temporary) as archive:
                for member, checksum in {(r["member"], r["sha256"]) for r in index.values()}:
                    if hashlib.sha256(archive.read(member)).hexdigest() != checksum:
                        raise ValueError(f"Archive checksum mismatch: {member}")
            if digest(database) != before:
                raise ValueError("Database changed during pruning; no obsolete files removed")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        result["source_archive_bytes"] = target.stat().st_size
    elif any(p.suffix == ".zip" for p in obsolete):
        raise ValueError("No referenced sources found; refusing to discard source archives")
    if digest(database) != before:
        raise ValueError("Database changed during pruning; no obsolete files removed")
    cache = within(root, root / "cache/lazyportfolio/individual_pages.json")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps({"urls": dict(sorted(urls.items()))}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    for path in obsolete:
        within(root, path).unlink()
    for folder in ("logs/history", "exports/pairs", "reports/pilots"):
        directory = within(root, root / folder)
        for child in sorted(directory.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if child.is_dir() and not any(child.iterdir()):
                within(root, child).rmdir()
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    result["database_unchanged"] = digest(database) == before
    if not result["database_unchanged"]:
        raise ValueError("Database changed during pruning; investigate a concurrent writer")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--prune", action="store_true", help="Remove obsolete investigation artifacts"
    )
    args = parser.parse_args()
    operation = prune if args.prune else compact
    print(json.dumps(operation(args.data_dir, apply=args.apply), indent=2))


if __name__ == "__main__":
    main()
