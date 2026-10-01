import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from zipfile import ZipFile

import pytest

from app.config import Settings
from app.storage.snapshots import read_snapshot, save_snapshot
from scripts.compact_data import compact, plan, prune


def test_optional_snapshot_is_compressed_identifiable_and_exact(tmp_path):
    raw = b"<html>numerical source " * 100
    path = save_snapshot(
        tmp_path,
        raw,
        symbol="VTI",
        currency="JPY",
        method="backtester",
        captured_at=datetime.now(UTC),
    )
    assert path.name.startswith("VTI_JPY_backtester_") and path.name.endswith(".html.gz")
    assert path.stat().st_size < len(raw)
    assert read_snapshot(path, tmp_path / "archive") == raw
    assert not Settings(_env_file=None).returns_save_raw_snapshots


@pytest.mark.parametrize("symbol", ["^BTC", "VUN.TO", "BNDX--CAD", "BTC"])
def test_snapshot_name_preserves_unusual_identity_safely(tmp_path, symbol):
    path = save_snapshot(
        tmp_path,
        b"source",
        symbol=symbol,
        currency="CAD",
        method="individual_page",
        captured_at=datetime.now(UTC),
    )
    assert path.parent == tmp_path and path.is_file()
    assert symbol.replace("^", "~5E") in path.name


def test_cleanup_archives_before_removing_sources_and_preserves_database(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "data"
    raw = root / "raw/lazyportfolio/backtester"
    raw.mkdir(parents=True)
    snapshot = raw / "unreadable_hash.html"
    snapshot.write_bytes(b"original source" * 1000)
    (raw / "offered_pairs.json").write_text('{"instruments": {}}')
    exports = root / "exports"
    exports.mkdir()
    (exports / "lazyportfolio_instruments.csv").write_text("catalog")
    (exports / "returns_validation_123_USD.json").write_text('{"passed": true}')
    (exports / "backtester_pilot_validation.json").write_text('{"passed": true}')
    (exports / "backtester_batch.log").write_text("old log")
    (root / "qcross.db").write_bytes(b"do not modify database")
    original_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    before = (root / "qcross.db").read_bytes()
    preview = compact(root)
    assert preview["archived_files"] == 2 and snapshot.exists()
    report = compact(root, apply=True)
    assert not snapshot.exists() and not (root / "raw").exists()
    assert (root / "qcross.db").read_bytes() == before
    assert report["database_sha256_before"] == report["database_sha256_after"]
    assert (root / "exports/lazyportfolio_instruments.csv").read_text() == "catalog"
    assert (root / "reports/backtester_pilot_validation.json").exists()
    assert (root / "cache/lazyportfolio/backtester_pairs.json").exists()
    assert (root / "logs/history/backtester_batch.log").exists()
    restored = read_snapshot(
        "data/raw/lazyportfolio/backtester/unreadable_hash.html", root / "archive"
    )
    assert hashlib.sha256(restored).hexdigest() == original_hash
    assert read_snapshot(snapshot, root / "archive") == restored
    assert compact(root, apply=True)["archived_files"] == 0


def test_archive_corruption_is_not_silently_accepted(tmp_path):
    with ZipFile(tmp_path / "legacy.zip", "w") as archive:
        archive.writestr("source.html", b"altered data")
        archive.writestr(
            "snapshot_index.json",
            json.dumps({"old.html": {"member": "source.html", "sha256": "wrong"}}),
        )
    with pytest.raises(ValueError, match="checksum"):
        read_snapshot("old.html", tmp_path)


def test_cleanup_collision_aborts_before_archiving_or_moving(tmp_path):
    root = tmp_path / "data"
    for folder in ("exports", "reports", "raw"):
        (root / folder).mkdir(parents=True)
    (root / "exports/backtester_pilot_validation.json").write_text("original")
    (root / "reports/backtester_pilot_validation.json").write_text("existing")
    source = root / "raw/source.html"
    source.write_text("keep")
    with pytest.raises(FileExistsError):
        compact(root, apply=True)
    assert source.exists() and not (root / "archive").exists()


def test_cleanup_rejects_symlinks_outside_data(tmp_path):
    outside = tmp_path / "outside.html"
    outside.write_text("keep")
    raw = tmp_path / "data/raw"
    raw.mkdir(parents=True)
    try:
        (raw / "escape.html").symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation is unavailable")
    with pytest.raises(ValueError, match="escapes"):
        plan(tmp_path / "data")
    assert outside.read_text() == "keep"


def pruning_fixture(tmp_path, *, checksum_valid=True):
    root = tmp_path / "data"
    for folder in ("archive", "logs/history", "exports/pairs", "reports", "cache/lazyportfolio"):
        (root / folder).mkdir(parents=True)
    original = root / "raw/lazyportfolio/old_hash.html"
    content = b"accepted numerical source"
    checksum = hashlib.sha256(content).hexdigest()
    meta = {
        "snapshot_path": str(original),
        "snapshot_sha256": checksum if checksum_valid else "wrong",
        "source_url": "https://www.lazyportfolioetf.com/etf/accepted-vti/",
        "validation": {"passed": True},
    }
    with ZipFile(root / "archive/legacy_ingestion_old.zip", "w") as archive:
        archive.writestr("accepted.html", content)
        archive.writestr("failed_attempt.html", b"discard failed attempt")
        archive.writestr("backup.db", b"discard old backup")
        archive.writestr(
            "snapshot_index.json",
            json.dumps(
                {str(original).replace("\\", "/"): {"member": "accepted.html", "sha256": checksum}}
            ),
        )
    with sqlite3.connect(root / "qcross.db") as connection:
        connection.execute(
            "CREATE TABLE instruments (id INTEGER, source_symbol TEXT, is_active INTEGER)"
        )
        connection.execute(
            "CREATE TABLE return_series (id INTEGER, instrument_id INTEGER, currency TEXT, "
            "extraction_method TEXT, metadata TEXT, validation_status TEXT, source TEXT)"
        )
        connection.execute("INSERT INTO instruments VALUES (1, 'VTI', 1)")
        connection.execute(
            "INSERT INTO return_series VALUES (1,1,'USD','individual_page',?,'validated',?)",
            (json.dumps(meta), "lazyportfolioetf"),
        )
    for path in (
        "logs/history/old.log",
        "exports/pairs/VTI_USD.csv",
        "reports/storage_cleanup.json",
    ):
        (root / path).write_text("obsolete")
    (root / "reports/backtester_pilot_validation.json").write_text("operational pilot")
    (root / "exports/lazyportfolio_monthly_returns.csv").write_text("working export")
    (root / "cache/lazyportfolio/individual_pages.json").write_text('{"urls":{"stale":"bad"}}')
    return root, original, content


def test_pruning_retains_only_committed_sources_and_operational_outputs(tmp_path):
    root, original, content = pruning_fixture(tmp_path)
    before = (root / "qcross.db").read_bytes()
    preview = prune(root)
    assert preview["obsolete_files"] == 4 and preview["referenced_sources"] == 1
    result = prune(root, apply=True)
    assert result["database_unchanged"] and (root / "qcross.db").read_bytes() == before
    assert not (root / "logs/history").exists()
    assert not (root / "exports/pairs").exists()
    assert not (root / "reports/storage_cleanup.json").exists()
    assert (root / "reports/backtester_pilot_validation.json").read_text() == "operational pilot"
    assert (root / "exports/lazyportfolio_monthly_returns.csv").read_text() == "working export"
    archive = root / "archive/validated_sources.zip"
    assert list((root / "archive").glob("*.zip")) == [archive]
    with ZipFile(archive) as file:
        assert set(file.namelist()) == {
            "sources/VTI_USD_individual_page.html",
            "snapshot_index.json",
        }
    assert read_snapshot(original, root / "archive") == content
    cache = json.loads((root / "cache/lazyportfolio/individual_pages.json").read_text())
    assert cache == {"urls": {"VTI": "https://www.lazyportfolioetf.com/etf/accepted-vti/"}}
    assert prune(root, apply=True)["obsolete_files"] == 0


def test_pruning_aborts_without_deleting_artifacts_on_source_mismatch(tmp_path):
    root, _, _ = pruning_fixture(tmp_path, checksum_valid=False)
    original_archive = (root / "archive/legacy_ingestion_old.zip").read_bytes()
    with pytest.raises(ValueError, match="Stored source checksum"):
        prune(root, apply=True)
    assert (root / "logs/history/old.log").exists()
    assert (root / "exports/pairs/VTI_USD.csv").exists()
    assert (root / "archive/legacy_ingestion_old.zip").read_bytes() == original_archive
    assert not (root / "archive/validated_sources.zip").exists()
    assert not (root / "archive/.validated_sources.zip.tmp").exists()
