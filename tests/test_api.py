from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.ingestion.base import InstrumentRecord
from app.ingestion.lazyportfolio.service import LazyPortfolioDiscoveryService
from app.main import create_app
from app.repositories.instruments import InstrumentRepository
from tests.ingestion.lazyportfolio.helpers import FixtureClient, remove_symbols


def test_api_filters_detail_and_pagination(
    source_html: str,
    settings: Settings,
    repository: InstrumentRepository,
    session_factory: sessionmaker[Session],
) -> None:
    LazyPortfolioDiscoveryService(settings, repository, FixtureClient(source_html)).discover()
    LazyPortfolioDiscoveryService(
        settings, repository, FixtureClient(remove_symbols(source_html, {"QQQ"}))
    ).discover()
    application = create_app(settings)
    application.state.session_factory = session_factory
    with TestClient(application) as client:
        assert client.get("/health").json() == {"status": "ok"}
        active = client.get(
            "/api/v1/instruments", params={"source": "lazyportfolioetf", "active": True}
        )
        assert active.status_code == 200 and len(active.json()) == 262
        assert len(client.get("/api/v1/instruments", params={"category": "US Stocks"}).json()) == 30
        inactive = client.get("/api/v1/instruments", params={"active": False}).json()
        assert [i["source_symbol"] for i in inactive] == ["QQQ"]
        matches = client.get("/api/v1/instruments", params={"search": "^btc"}).json()
        assert len(matches) == 4
        assert client.get("/api/v1/instruments", params={"search": "%"}).json() == []
        assert client.get("/api/v1/instruments", params={"source": "absent"}).json() == []
        page = client.get("/api/v1/instruments", params={"limit": 2, "offset": 1}).json()
        assert len(page) == 2
        detail = client.get(f"/api/v1/instruments/{matches[0]['id']}")
        assert detail.status_code == 200 and detail.json() == matches[0]
        assert detail.json()["last_seen_at"].endswith("Z")
        assert client.get("/api/v1/instruments/999999").status_code == 404
        assert client.get("/api/v1/instruments", params={"limit": 1001}).status_code == 422
        assert client.post("/api/v1/instruments/discover").status_code == 405


def test_currency_filters_match_source_availability(
    source_html: str,
    settings: Settings,
    repository: InstrumentRepository,
    session_factory: sessionmaker[Session],
) -> None:
    LazyPortfolioDiscoveryService(settings, repository, FixtureClient(source_html)).discover()
    repository.synchronize(
        "manual",
        [InstrumentRecord(source_symbol="UNKNOWN", ticker="UNKNOWN")],
        datetime.now(UTC),
        lambda _: None,
    )
    application = create_app(settings)
    application.state.session_factory = session_factory
    with TestClient(application) as client:

        def results(**filters: str) -> list[dict]:
            response = client.get("/api/v1/instruments", params=filters)
            assert response.status_code == 200
            return response.json()

        usd = results(source="lazyportfolioetf", currency="usd")
        assert len(usd) == 74 and all(i["currency"] == "USD" for i in usd)
        for code, symbol in [
            ("USD", "VTI"),
            ("CAD", "VUN.TO"),
            ("EUR", "XD9U.DE"),
            ("GBP", "XDUS.L"),
        ]:
            instruments = results(currency=code, search="US Total Stock Market")
            assert [i["source_symbol"] for i in instruments] == [symbol]
        gbp = results(available_currency="gbp")
        assert len(gbp) == 68 and all("GBP" in i["available_currencies"] for i in gbp)
        cad = results(available_currency="CAD")
        assert len(cad) == 79
        assert any(i["source_symbol"] == "VTV" and i["currency"] == "USD" for i in cad)
        jpy = results(available_currency="JPY")
        assert len(jpy) == 70
        assert next(i for i in jpy if i["source_symbol"] == "VTI")["currency"] == "USD"
        assert results(currency="JPY") == []
        assert results(available_currency="ZZZ") == []
        assert results(currency="GBP", available_currency="USD") == []
        assert client.get("/api/v1/instruments", params={"currency": "US"}).status_code == 422
        assert (
            client.get("/api/v1/instruments", params={"available_currency": "US%"}).status_code
            == 422
        )
