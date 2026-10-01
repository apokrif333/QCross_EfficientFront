"""Read published ticker/link pairs without assuming fixed table columns."""

import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

SYMBOL = re.compile(r"[A-Z0-9^][A-Z0-9.^=_-]*")
PREFIX = re.compile(r"^([A-Z0-9^][A-Z0-9.^=_-]*)\s+[-–]\s+")


def published_instrument_links(html: str, base_url: str) -> list[tuple[str, str]]:
    links: set[tuple[str, str]] = set()
    for row in BeautifulSoup(html, "lxml").select("table tr"):
        for anchor in row.select("a[href]"):
            target = urljoin(base_url, anchor["href"]).rstrip("/") + "/"
            parsed = urlsplit(target)
            if parsed.netloc != urlsplit(base_url).netloc or not parsed.path.startswith("/etf/"):
                continue
            label = anchor.get_text(" ", strip=True)
            prefix = PREFIX.match(label)
            symbol = prefix.group(1) if prefix else label if SYMBOL.fullmatch(label) else None
            if symbol is None:
                fragments = [t for t in anchor.stripped_strings if SYMBOL.fullmatch(t)]
                if len(fragments) == 1:
                    symbol = fragments[0]
            if symbol is None:
                badges = [
                    e.get_text(" ", strip=True) for e in row.select(".etf-code, .badge-ticker")
                ]
                if len(badges) == 1 and SYMBOL.fullmatch(badges[0]):
                    symbol = badges[0]
            if symbol is None:
                # Directory/correlation tables put the ticker before the fund-name link.
                cell = anchor.find_parent("td")
                previous = cell.find_previous_sibling("td") if cell else None
                if previous is not None:
                    text = previous.get_text(" ", strip=True)
                    if SYMBOL.fullmatch(text):
                        symbol = text
            if symbol:
                links.add((symbol, target))
    return sorted(links)
