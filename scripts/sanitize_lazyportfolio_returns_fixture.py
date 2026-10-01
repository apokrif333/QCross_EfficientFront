"""Keep public numerical observations and methodology; remove tokens, ads and live data."""

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from bs4 import BeautifulSoup

from app.config import get_settings
from app.storage.snapshots import read_snapshot


def sanitize(html: str) -> str:
    scripts = BeautifulSoup(html, "lxml").find_all("script")
    matches = [re.search(r"\bconst\s+_d\s*=\s*", script.get_text()) for script in scripts]
    pairs = [
        (script.get_text(), match)
        for script, match in zip(scripts, matches, strict=True)
        if match is not None
    ]
    if len(pairs) != 1:
        raise ValueError("Expected one original Alpine detail-page state")
    text, match = pairs[0]
    state, _ = json.JSONDecoder().raw_decode(text[match.end() :])
    portfolios = {}
    for key, original in state["portfolios"].items():
        retained = {
            field: original[field]
            for field in (
                "pID",
                "isEtf",
                "components",
                "filter",
                "withTaxPaid",
                "withRebalancing",
                "periodoMin",
                "periodoMax",
            )
        }
        for field in ("isUserSimulation", "capitaleIniziale", "etfSwap"):
            if field in original:
                retained[field] = original[field]
        retained["rend"] = {"sliceArray": {}}
        for period in ("MAX", "1M"):
            retained["rend"]["sliceArray"][period] = {
                field: original["rend"]["sliceArray"][period][field]
                for field in (
                    "periodoMin",
                    "periodoMax",
                    "periodoStart",
                    "monthDiff",
                    "capitalBase",
                    "capitaleInvestito",
                    "rendList",
                )
            }
        retained["kpiData"] = {"MAX": {"base": original["kpiData"]["MAX"]["base"]}}
        portfolios[key] = retained
    state = {"settings": state["settings"], "portfolios": portfolios}
    page_text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    methodology = re.search(
        r"Returns,\s*up to\s+[A-Za-z]+\s+\d{4},\s*have been derived[^.]+\.", page_text
    )
    reinvestment = re.search(
        r"dividend reinvestment\s*\(without dividend taxation\),?\s*when applicable",
        page_text,
        re.IGNORECASE,
    )
    if reinvestment is None:
        raise ValueError("Missing published reinvestment convention")
    return (
        '<!doctype html><html><head><meta charset="utf-8"></head><body><script>'
        "const _d = "
        + json.dumps(state, separators=(",", ":"))
        + ";</script><div>"
        + reinvestment.group(0)
        + "</div><p>"
        + (methodology.group(0) if methodology else "No published reconstruction cutoff")
        + "</p></body></html>"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument(
        "--output", type=Path, default=Path("tests/fixtures/lazyportfolio_vti_returns.html")
    )
    parser.add_argument("--source-url", required=True)
    args = parser.parse_args()
    content = read_snapshot(args.snapshot, get_settings().archive_dir)
    fixture = sanitize(content.decode("utf-8-sig"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(fixture, encoding="utf-8")
    args.output.with_suffix(".metadata.json").write_text(
        json.dumps(
            {
                "source_url": args.source_url,
                "captured_at": datetime.now(UTC).isoformat(),
                "source_response_sha256": hashlib.sha256(content).hexdigest(),
                "fixture_sha256": hashlib.sha256(fixture.encode()).hexdigest(),
                "sanitization": (
                    "Public MAX/1M observations, capital, KPIs, settings and methodology; "
                    "no USID/nonces, tracking or live daily observations"
                ),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
