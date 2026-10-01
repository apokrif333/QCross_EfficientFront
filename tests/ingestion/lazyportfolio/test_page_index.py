import json
from pathlib import Path

from app.ingestion.lazyportfolio.page_index import published_instrument_links
from app.ingestion.lazyportfolio.returns_service import LazyPortfolioReturnsService
from app.repositories.returns import ReturnRepository


def test_provider_column_layout_and_directory_symbol_identity() -> None:
    html = Path("tests/fixtures/lazyportfolio_instrument_links.html").read_text()
    links = dict(published_instrument_links(html, "https://www.lazyportfolioetf.com"))
    assert links["VTV"].endswith("/etf/vanguard-value-vtv/")
    assert links["VTI"].endswith("/etf/vanguard-total-stock-market-vti/")
    assert "VUN.TO" in links and "^BTC" in links
    assert "EWC" in links
    assert "IUSV" in links
    assert "SPY" not in links
    assert "Large Cap Value" not in links


def test_url_crawl_disabled_and_known_cache_read_without_network(settings, session_factory):
    import pytest

    from app.ingestion.lazyportfolio.returns_models import ReturnExtractionError

    class Client:
        def fetch_page(self, path):
            raise AssertionError("URL discovery must not issue requests")

    service = LazyPortfolioReturnsService(
        settings, ReturnRepository(session_factory, source="lazyportfolioetf"), client=Client()
    )
    service.index_path.parent.mkdir(parents=True)
    service.index_path.write_text(json.dumps({"urls": {"IUSV": "/known"}}))
    assert service.public_page_index()["IUSV"] == "/known"
    with pytest.raises(ReturnExtractionError, match="disabled"):
        service.resolve_public_links()
    with pytest.raises(ReturnExtractionError, match="disabled"):
        service.public_page_index(refresh=True)
