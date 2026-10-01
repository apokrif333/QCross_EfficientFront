from datetime import UTC, datetime

from bs4 import BeautifulSoup

from app.ingestion.lazyportfolio.client import FetchedPage


class FixtureClient:
    def __init__(self, html: str) -> None:
        self.html = html

    def fetch_universe(self) -> FetchedPage:
        return FetchedPage(
            url="https://www.lazyportfolioetf.com/portfolio-backtest-and-simulation/",
            html=self.html,
            content=self.html.encode(),
            encoding="utf-8",
            fetched_at=datetime.now(UTC),
        )


def remove_symbols(html: str, symbols: set[str]) -> str:
    """Simulate a complete source removal across all redundant representations."""
    soup = BeautifulSoup(html, "lxml")
    for option in soup.select("select.asset-dropdown option"):
        if option.get("value") in symbols:
            option.decompose()
    for entry in soup.select(".asset-li-list"):
        badge = entry.select_one(".mini-badge")
        if badge and badge.get_text(strip=True) in symbols:
            entry.decompose()
    return str(soup)
