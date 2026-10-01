import json
import math
import statistics
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.models import Instrument, MonthlyReturn, ReturnRevision
from app.exports.returns import export_returns
from app.ingestion.lazyportfolio.client import FetchedPage, FetchError
from app.ingestion.lazyportfolio.returns_models import (
    ExtractedReturns,
    ReturnExtractionError,
    ReturnObservation,
    ReturnValidationError,
)
from app.ingestion.lazyportfolio.returns_parser import (
    embedded_state,
    month_end,
    parse_returns,
    shift_month,
)
from app.ingestion.lazyportfolio.returns_service import LazyPortfolioReturnsService
from app.ingestion.lazyportfolio.returns_validation import validate_returns
from app.main import create_app
from app.repositories.returns import ReturnRepository

URL = "https://www.lazyportfolioetf.com/etf/vanguard-total-stock-market-vti/"


@pytest.fixture
def returns_html() -> str:
    return Path("tests/fixtures/lazyportfolio_vti_returns.html").read_text(encoding="utf-8")


@pytest.fixture
def vti(returns_html: str) -> ExtractedReturns:
    return parse_returns(
        returns_html, symbol="VTI", currency="USD", source_url=URL, extracted_at=datetime.now(UTC)
    )


def instrument(factory, symbol="TEST", currency="USD") -> Instrument:
    with factory.begin() as session:
        row = Instrument(
            source="lazyportfolioetf",
            source_symbol=symbol,
            ticker=symbol,
            currency=currency,
            raw_metadata={},
        )
        session.add(row)
    return row


def synthetic(values=None, *, symbol="TEST", start=date(2020, 1, 31)) -> ExtractedReturns:
    values = values or [Decimal("0.01"), Decimal("-0.02"), Decimal("0.003456")] * 12
    data = ExtractedReturns(
        source_symbol=symbol,
        currency="USD",
        source_url=URL,
        extracted_at=datetime.now(UTC),
        start_date=start,
        end_date=shift_month(start, len(values) - 1),
        expected_count=len(values),
        observations=[
            ReturnObservation(
                date=shift_month(start, i),
                return_value=value,
                quality_flag="rounded_4dp_percent|unknown",
            )
            for i, value in enumerate(values)
        ],
        percent_decimal_places=4,
        capital=[],
        published_statistics={},
        published_latest_return=values[-1],
    )
    data.capital = [Decimal(1)]
    peak = Decimal(1)
    dd = Decimal(0)
    for r in values:
        data.capital.append(data.capital[-1] * (1 + r))
        peak = max(peak, data.capital[-1])
        dd = min(dd, data.capital[-1] / peak - 1)
    data.published_statistics = {
        "final_capital": data.capital[-1],
        "annualized_return": Decimal(str(float(data.capital[-1]) ** (12 / len(values)) - 1)),
        "annualized_volatility": Decimal(
            str(statistics.pstdev(map(float, values)) * math.sqrt(12))
        ),
        "maximum_drawdown": dd,
    }
    return data


def test_vti_complete_precision_and_provenance(vti):
    assert len(vti.observations) == 2804
    assert vti.start_date == date(1793, 1, 31)
    assert vti.end_date == date(2026, 8, 31)
    assert vti.observations[0].return_value == Decimal("-0.004688")
    assert vti.observations[-1].return_value == Decimal("0.026996")
    assert vti.percent_decimal_places == 4
    assert vti.proxy_cutoff == date(2001, 12, 31)
    flags = {o.date: o.source_metadata["provenance"] for o in vti.observations}
    assert flags[date(2001, 12, 31)] == "historical_proxy"
    assert flags[date(2002, 1, 31)] == "reported_etf_era"


def test_missing_methodology_is_unknown(returns_html):
    html = returns_html[: returns_html.index("<p>")] + "</body></html>"
    data = parse_returns(
        html, symbol="VTI", currency="USD", source_url=URL, extracted_at=datetime.now(UTC)
    )
    assert data.proxy_cutoff is None
    assert all(o.source_metadata["provenance"] == "unknown" for o in data.observations)


@pytest.mark.parametrize("symbol,currency", [("SPY", "USD"), ("VTI", "GBP")])
def test_identity_currency_rejected(returns_html, symbol, currency):
    with pytest.raises(ReturnExtractionError):
        parse_returns(
            returns_html,
            symbol=symbol,
            currency=currency,
            source_url=URL,
            extracted_at=datetime.now(UTC),
        )


def test_truncated_json_rejected(returns_html):
    with pytest.raises(ReturnExtractionError):
        parse_returns(
            returns_html[:4000] + "</script>",
            symbol="VTI",
            currency="USD",
            source_url=URL,
            extracted_at=datetime.now(UTC),
        )


@pytest.mark.parametrize(
    "flag", ["withCashflow", "withRebalancing", "withTaxPaid", "isUserSimulation"]
)
def test_simulation_features_rejected(returns_html, flag):
    html = returns_html.replace(f'"{flag}":false', f'"{flag}":true')
    with pytest.raises(ReturnExtractionError):
        parse_returns(
            html, symbol="VTI", currency="USD", source_url=URL, extracted_at=datetime.now(UTC)
        )


def test_actual_vti_statistics_and_entire_path(vti):
    report = validate_returns(vti, today=date(2026, 10, 1))
    assert report.passed, report.errors
    assert report.checks["capital_path"]["points_checked"] == 2804
    assert report.checks["capital_path"]["failed_months"] == []
    assert len(report.checks["annual_returns"]) == 234
    assert all(y["passed"] for y in report.checks["annual_returns"])
    assert all(v["passed"] for v in report.checks["statistics"].values())
    # Preserving original rounded numbers yields a small honest mismatch, not a forced exact fit.
    assert Decimal(report.checks["capital_path"]["final_relative_error"]) != 0


@pytest.mark.parametrize(
    "mutation", ["duplicate", "missing", "unordered", "month_start", "truncated"]
)
def test_calendar_errors(vti, mutation):
    if mutation == "duplicate":
        vti.observations[1].date = vti.observations[0].date
    elif mutation == "missing":
        del vti.observations[10]
    elif mutation == "unordered":
        vti.observations[0], vti.observations[1] = vti.observations[1], vti.observations[0]
    elif mutation == "month_start":
        vti.observations[0].date = date(1793, 1, 1)
    else:
        vti.observations = vti.observations[:100]
    assert not validate_returns(vti).passed


@pytest.mark.parametrize("invalid", [Decimal("-1.01"), Decimal("NaN"), Decimal("Infinity")])
def test_invalid_returns_rejected(vti, invalid):
    vti.observations[1].return_value = invalid
    assert not validate_returns(vti).passed


def test_row_count_alone_does_not_validate(vti):
    vti.observations[1000].return_value += Decimal("0.01")
    report = validate_returns(vti)
    assert not report.passed
    assert report.checks["capital_path"]["failed_months"]


@pytest.mark.parametrize(
    "metric", ["annualized_return", "annualized_volatility", "maximum_drawdown"]
)
def test_published_statistics_mismatch_rejected(vti, metric):
    vti.published_statistics[metric] += Decimal("0.05")
    assert not validate_returns(vti).passed


def test_current_month_rejected(vti):
    assert not validate_returns(vti, today=date(2026, 8, 15)).passed


def test_month_end_leap_year():
    assert month_end(2000, 2) == date(2000, 2, 29)
    assert month_end(1900, 2) == date(1900, 2, 28)
    assert shift_month(date(1999, 12, 31), 2) == date(2000, 2, 29)


def test_outliers_retained():
    data = synthetic([Decimal("0.75"), Decimal("-0.6"), Decimal("0.01")])
    report = validate_returns(data)
    assert report.passed
    assert len(report.checks["reviewable_outliers"]) == 2
    assert len(data.observations) == 3


def test_upsert_decimal_idempotence_and_invalid_rollback(session_factory):
    row = instrument(session_factory)
    repo = ReturnRepository(session_factory, source="lazyportfolioetf")
    data = synthetic()
    assert repo.synchronize(row.id, data)["inserted"] == 36
    assert repo.synchronize(row.id, data)["inserted"] == 0
    data.observations.pop()
    with pytest.raises(ReturnValidationError):
        repo.synchronize(row.id, data)
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(MonthlyReturn)) == 36
        value = session.scalar(
            select(MonthlyReturn.return_value).order_by(MonthlyReturn.date).offset(2)
        )
        assert value == Decimal("0.003456")


def test_catalog_statuses_report_downloaded_observations(session_factory):
    row = instrument(session_factory, symbol="TEST")
    repo = ReturnRepository(session_factory, source="lazyportfolioetf")
    assert repo.catalog_statuses()[("TEST", "USD")] == {
        "returns_status": "not_attempted",
        "returns_downloaded": False,
        "return_observation_count": 0,
    }

    repo.synchronize(row.id, synthetic())
    repo.checkpoint(row.id, "USD", "successful", {})
    assert repo.catalog_statuses()[("TEST", "USD")] == {
        "returns_status": "successful",
        "returns_downloaded": True,
        "return_observation_count": 36,
    }


def test_revisions_audited_and_explicitly_applied(session_factory):
    row = instrument(session_factory)
    repo = ReturnRepository(session_factory, source="lazyportfolioetf")
    data = synthetic()
    repo.synchronize(row.id, data)
    values = [o.return_value for o in data.observations]
    values[5] = Decimal("0.003457")
    revised = synthetic(values)
    result = repo.synchronize(row.id, revised)
    assert not result["accepted"] and result["revisions"] == 1
    repo.synchronize(row.id, revised)
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ReturnRevision)) == 1
        assert session.scalar(
            select(MonthlyReturn.return_value).where(
                MonthlyReturn.date == revised.observations[5].date
            )
        ) == values[5] - Decimal("0.000001")
        assert session.scalar(select(ReturnRevision.applied_at)) is None
    assert repo.synchronize(row.id, revised, accept_revisions=True)["updated"] == 1
    with session_factory() as session:
        assert session.scalar(select(ReturnRevision.applied_at)) is not None
        assert (
            session.scalar(
                select(MonthlyReturn.return_value).where(
                    MonthlyReturn.date == revised.observations[5].date
                )
            )
            == values[5]
        )


def test_incremental_extension_and_no_shortening(session_factory):
    row = instrument(session_factory)
    repo = ReturnRepository(session_factory, source="lazyportfolioetf")
    data = synthetic()
    repo.synchronize(row.id, data)
    extended = synthetic([o.return_value for o in data.observations] + [Decimal("0.015")])
    assert repo.synchronize(row.id, extended)["inserted"] == 1
    # A freshly fetched but shorter source must never remove already imported history.
    shortened = synthetic()
    with pytest.raises(ValueError, match="shorten"):
        repo.synchronize(row.id, shortened)
    assert repo.series(row.id, "USD").observation_count == 37


class FakeClient:
    def __init__(self, html):
        self.html = html
        self.calls = 0
        self.interrupt = False

    def fetch_page(self, path):
        self.calls += 1
        if self.interrupt:
            raise KeyboardInterrupt
        return FetchedPage(
            url=URL,
            html=self.html,
            content=self.html.encode(),
            encoding="utf-8",
            fetched_at=datetime.now(UTC),
        )


def test_interrupted_resume_and_skip_success(settings, session_factory, returns_html):
    row = instrument(session_factory, "VTI")
    repo = ReturnRepository(session_factory, source="lazyportfolioetf")
    client = FakeClient(returns_html)
    service = LazyPortfolioReturnsService(settings, repo, client=client, sleep=lambda _: None)
    client.interrupt = True
    with pytest.raises(KeyboardInterrupt):
        service.run(symbol="VTI")
    assert repo.status(row.id, "USD").status == "partial"
    assert repo.series(row.id, "USD") is None
    client.interrupt = False
    service.run(symbol="VTI")
    assert repo.status(row.id, "USD").status == "successful"
    assert repo.series(row.id, "USD").observation_count == 2804
    service.run(symbol="VTI")
    assert client.calls == 2
    assert repo.series(row.id, "USD").observation_count == 2804


def test_failed_validation_preserves_import(settings, session_factory, returns_html):
    row = instrument(session_factory, "VTI")
    repo = ReturnRepository(session_factory, source="lazyportfolioetf")
    client = FakeClient(returns_html)
    service = LazyPortfolioReturnsService(settings, repo, client=client, sleep=lambda _: None)
    service.run(symbol="VTI")
    client.html = returns_html.replace('"monthDiff":2804', '"monthDiff":40')
    service.run(symbol="VTI", update=True)
    assert repo.status(row.id, "USD").status == "partial"
    assert repo.series(row.id, "USD").observation_count == 2804


def test_bulk_requires_vti_validation(settings, session_factory):
    instrument(session_factory, "SPY")
    client = FakeClient("")
    service = LazyPortfolioReturnsService(
        settings, ReturnRepository(session_factory, source="lazyportfolioetf"), client=client
    )
    with pytest.raises(ReturnExtractionError, match="pilot"):
        service.run(all_active=True)
    assert client.calls == 0


def test_csv_parquet_and_currency_wide_missing_values(settings, session_factory):
    import pyarrow.parquet as pq

    a = instrument(session_factory)
    b = instrument(session_factory, "SPY")
    repo = ReturnRepository(session_factory, source="lazyportfolioetf")
    repo.synchronize(a.id, synthetic())
    repo.synchronize(b.id, synthetic(symbol="SPY", start=date(2020, 2, 29)))
    with pytest.raises(ValueError, match="currency"):
        export_returns(session_factory, settings.export_dir / "bad.parquet", wide=True)
    path = export_returns(
        session_factory, settings.export_dir / "wide.parquet", wide=True, currency="USD"
    )
    table = pq.read_table(path)
    assert table.num_rows == 37
    assert table.column("SPY")[0].as_py() is None
    assert table.column("TEST")[-1].as_py() is None
    assert table.column("TEST").type == __import__("pyarrow").float64()
    path = export_returns(session_factory, settings.export_dir / "long.csv", format="csv")
    assert "0.003456" in path.read_text()
    assert json.loads(path.with_suffix(".csv.metadata.json").read_text())["rows"] == 72


def test_return_api_date_filters_and_no_ingestion(settings, session_factory):
    row = instrument(session_factory)
    ReturnRepository(session_factory, source="lazyportfolioetf").synchronize(row.id, synthetic())
    engine = session_factory.kw["bind"]
    with TestClient(create_app(settings, engine=engine)) as client:
        data = client.get("/api/v1/return-series?currency=usd").json()
        assert len(data) == 1
        assert data[0]["instrument_id"] == row.id
        assert data[0]["last_updated_at"].endswith("Z")
        assert data[0]["metadata"]["precision_percent_decimals"] == 4
        response = client.get(
            f"/api/v1/return-series/{data[0]['id']}/observations",
            params={"start_date": "2020-02-01", "end_date": "2020-03-31"},
        )
        assert response.status_code == 200
        assert [o["date"] for o in response.json()] == ["2020-02-29", "2020-03-31"]
        assert Decimal(response.json()[1]["return_value"]) == Decimal("0.003456")
        assert response.json()[0]["created_at"].endswith("Z")
        assert client.get("/api/v1/return-series/999/observations").status_code == 404
        assert client.post("/api/v1/return-series/discover").status_code == 405


def test_fixture_has_no_live_data_or_tokens(returns_html):
    assert "USID" not in returns_html and "wpParams" not in returns_html
    assert "jsAsyncLive" not in returns_html
    assert embedded_state(returns_html)["settings"]["withCashflow"] is False


def test_sanitizer_preserves_all_original_observations(returns_html):
    from scripts.sanitize_lazyportfolio_returns_fixture import sanitize

    original = embedded_state(returns_html)
    regenerated = embedded_state(sanitize(returns_html))
    a = original["portfolios"]["VTI_1"]["rend"]["sliceArray"]["MAX"]
    b = regenerated["portfolios"]["VTI_1"]["rend"]["sliceArray"]["MAX"]
    assert a["rendList"] == b["rendList"]
    assert a["capitalBase"] == b["capitalBase"]


@pytest.mark.parametrize("access_denied", [False, True])
def test_batch_failure_isolated_or_access_denial_stops(
    settings,
    session_factory,
    returns_html,
    access_denied,
):
    pilot = instrument(session_factory, "VTI")
    repo = ReturnRepository(session_factory, source="lazyportfolioetf")
    data = parse_returns(
        returns_html, symbol="VTI", currency="USD", source_url=URL, extracted_at=datetime.now(UTC)
    )
    repo.synchronize(pilot.id, data)
    repo.checkpoint(pilot.id, "USD", "successful", {})
    failed = instrument(session_factory, "SPY")
    later = instrument(session_factory, "TLT")

    class BatchClient(FakeClient):
        def fetch_page(self, path):
            self.calls += 1
            if path == "/bad":
                raise FetchError("HTTP 403" if access_denied else "Transient HTTP 500")
            html = self.html.replace('"VTI"', '"TLT"')
            return FetchedPage(
                url=URL,
                html=html,
                content=html.encode(),
                encoding="utf-8",
                fetched_at=datetime.now(UTC),
            )

    client = BatchClient(returns_html)
    service = LazyPortfolioReturnsService(settings, repo, client=client, sleep=lambda _: None)
    service.public_page_index = lambda **_: {"SPY": "/bad", "TLT": "/good"}
    report = service.run(all_active=True)
    assert repo.status(failed.id, "USD").status == "failed"
    assert repo.series(pilot.id, "USD").observation_count == 2804
    if access_denied:
        assert report["batch_stopped"] is not None
        assert client.calls == 1
        assert repo.series(later.id, "USD") is None
    else:
        assert report["batch_stopped"] is None
        assert repo.status(later.id, "USD").status == "successful"
        assert repo.series(later.id, "USD").observation_count == 2804


def test_latest_slice_mismatch_and_unconfirmed_dividends(vti, returns_html):
    vti.published_latest_return += Decimal("0.000001")
    assert not validate_returns(vti).passed
    with pytest.raises(ReturnExtractionError, match="dividend"):
        parse_returns(
            returns_html.replace("dividend reinvestment", "unconfirmed income"),
            symbol="VTI",
            currency="USD",
            source_url=URL,
            extracted_at=datetime.now(UTC),
        )
