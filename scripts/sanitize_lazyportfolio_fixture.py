"""Retain only the instrument catalogue and two verbatim selector copies.

Usage: python scripts/sanitize_lazyportfolio_fixture.py path/to/snapshot.html
Run this explicitly when reviewing a source change; never overwrite the golden
fixture automatically during a discovery or a test run.
"""

import argparse
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from bs4 import BeautifulSoup

from app.config import get_settings
from app.storage.snapshots import read_snapshot


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    raw = read_snapshot(args.snapshot, get_settings().archive_dir)
    source = BeautifulSoup(raw, "lxml")
    selectors = source.select("select.asset-dropdown")
    blocks = source.select(".asset-block")
    if len(selectors) < 2 or not blocks:
        raise SystemExit("Expected multiple selectors and the available-assets panel")
    content = (
        '<!doctype html><html><head><meta charset="utf-8"></head><body>\n'
        "<!-- Sanitized live fixture: complete panel and two verbatim selector copies. -->\n"
        + "\n".join(str(block) for block in blocks)
        + "\n"
        + "\n".join(str(select) for select in selectors[:2])
        + "\n</body></html>\n"
    )
    target = Path("tests/fixtures/lazyportfolio_asset_universe.html")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    symbols = {o["value"] for o in selectors[0].select("option[value]") if o["value"]}
    metadata = {
        "source_url": "https://www.lazyportfolioetf.com/portfolio-backtest-and-simulation/",
        "captured_at": datetime.now(UTC).isoformat(),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "fixture_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "live_selector_count": len(selectors),
        "fixture_selector_count": 2,
        "instrument_count": len(symbols),
        "instrument_currency_counts": dict(
            Counter(
                o.get("data-basecurrency") or "Unknown"
                for o in selectors[0].select("option[value]")
                if o["value"]
            )
        ),
        "preserved_currency_attributes": ["data-basecurrency", "data-currency"],
        "sanitization": "Only complete asset-block elements and first two asset-dropdown "
        "selects retained; no scripts, forms, tokens, ads or tracking.",
    }
    target.with_suffix(".metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Fixture written: {target} ({len(content.encode())} bytes; {len(symbols)} symbols)")


if __name__ == "__main__":
    main()
