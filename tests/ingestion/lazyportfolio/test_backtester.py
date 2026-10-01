import csv
import io
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.db.models import Instrument, MonthlyReturn, ReturnSeries
from app.exports.returns import export_returns
from app.ingestion.lazyportfolio.backtester import (
    BacktesterAdapter,
    BacktesterRestricted,
    PairNotAvailable,
    form_fields,
    monthly_csv,
    offered_pairs,
)
from app.ingestion.lazyportfolio.backtester_pilot import compare
from app.ingestion.lazyportfolio.return_pairs import ReturnPairService
from app.ingestion.lazyportfolio.returns_models import ReturnExtractionError
from app.ingestion.lazyportfolio.returns_parser import _parse_returns, parse_returns
from app.ingestion.lazyportfolio.returns_validation import validate_returns
from app.repositories.returns import ReturnRepository


@pytest.fixture
def form_html():
    return Path("tests/fixtures/lazyportfolio_backtester_form.html").read_text(encoding="utf-8")


@pytest.fixture
def backtest_html():
    return Path("tests/fixtures/lazyportfolio_backtester_VTI.html").read_text(encoding="utf-8")


@pytest.fixture
def data(backtest_html):
    return _parse_returns(
        backtest_html,
        symbol="VTI",
        currency="USD",
        source_url="https://www.lazyportfolioetf.com/portfolio-backtest-and-simulation/",
        extracted_at=datetime.now(UTC),
        backtester=True,
    )


def add_instrument(factory, symbol="VTI", currencies=None):
    with factory.begin() as s:
        row = Instrument(
            source="lazyportfolioetf",
            source_symbol=symbol,
            ticker=symbol,
            currency="USD",
            available_currencies=currencies or ["USD"],
        )
        s.add(row)
    return row


def routing_service(settings, repository, instrument, data, monkeypatch, *, known_url=True):
    """Exercise the real coordinator/repository with deterministic source adapters."""
    calls = []
    adapter = Mock()
    adapter.last_request = None
    adapter.discover_pairs.return_value = {
        instrument.source_symbol: {"currencies": instrument.available_currencies}
    }

    def extract(symbol, currency, **kwargs):
        calls.append("backtester")
        return data.model_copy(
            update={
                "source_symbol": symbol,
                "currency": currency,
                "extracted_at": datetime.now(UTC),
                "extraction_method": "backtester",
                "currency_kind": "native" if currency == instrument.currency else "converted",
            }
        )

    adapter.extract.side_effect = extract
    individual = Mock()
    individual.last_request = None
    individual.public_page_index.return_value = (
        {instrument.source_symbol: "https://www.lazyportfolioetf.com/etf/known/"}
        if known_url
        else {}
    )

    def import_one(row, url, *, accept_revisions, save_raw):
        calls.append("individual_page")
        result = repository.synchronize(
            row.id,
            data.model_copy(
                update={"extraction_method": "individual_page", "extracted_at": datetime.now(UTC)}
            ),
            accept_revisions=accept_revisions,
        )
        status = "successful" if result["accepted"] else "partial"
        repository.checkpoint(row.id, row.currency, status, {"sync": result})
        return status

    individual.import_one.side_effect = import_one
    service = ReturnPairService(settings, repository, adapter=adapter, individual=individual)
    monkeypatch.setattr(service, "_pilot_valid", lambda: True)
    return service, calls


@pytest.mark.parametrize("method", ["individual_page", "backtester"])
def test_refresh_reuses_validated_method_despite_failed_last_attempt(
    settings, session_factory, data, monkeypatch, method
):
    row = add_instrument(session_factory)
    repo = ReturnRepository(session_factory, source="lazyportfolioetf")
    repo.synchronize(row.id, data.model_copy(update={"extraction_method": method}))
    # A failed refresh is not a successful alternative extraction method.
    repo.checkpoint(row.id, "USD", "failed", {"extraction_method": "other_method"})
    service, calls = routing_service(settings, repo, row, data, monkeypatch)
    report = service.run(all_active=True, update=True)
    assert calls == [method]
    assert report["completed_pairs"] == 1
    assert repo.series(row.id, "USD").extraction_method == method
    attempts = repo.status(row.id, "USD").details["method_attempts"]
    assert len(attempts) == 1 and attempts[0]["status"] == "completed"
    assert attempts[0]["elapsed_seconds"] >= 0


@pytest.mark.parametrize("preferred", ["individual_page", "backtester"])
def test_fallback_success_becomes_preferred_on_next_refresh(
    settings, session_factory, data, monkeypatch, preferred
):
    row = add_instrument(session_factory)
    repo = ReturnRepository(session_factory, source="lazyportfolioetf")
    repo.synchronize(row.id, data.model_copy(update={"extraction_method": preferred}))
    original_id = repo.series(row.id, "USD").id
    service, calls = routing_service(settings, repo, row, data, monkeypatch)
    failed = service.adapter.extract if preferred == "backtester" else service.individual.import_one
    original = failed.side_effect

    def fail(*args, **kwargs):
        calls.append(preferred)
        raise ReturnExtractionError("Source markup is incomplete")

    failed.side_effect = fail
    service.run(all_active=True, update=True)
    fallback = "individual_page" if preferred == "backtester" else "backtester"
    assert calls == [preferred, fallback]
    assert repo.series(row.id, "USD").id == original_id
    assert repo.series(row.id, "USD").extraction_method == fallback
    attempts = repo.status(row.id, "USD").details["method_attempts"]
    assert [a["status"] for a in attempts] == ["failed", "completed"]
    assert attempts[0]["reason"] == "Source markup is incomplete"
    failed.side_effect = original
    calls.clear()
    service.run(all_active=True, update=True)
    assert calls == [fallback]
    calls.clear()
    service.run(all_active=True)
    assert calls == []
    with session_factory() as session:
        assert len(list(session.scalars(select(MonthlyReturn)))) == len(data.observations)


@pytest.mark.parametrize("preferred", ["individual_page", "backtester"])
@pytest.mark.parametrize("error", ["HTTP 403", "HTTP 429", "Retry-After too long"])
def test_preferred_route_restriction_stops_without_alternate_requests(
    settings, session_factory, data, monkeypatch, preferred, error
):
    row = add_instrument(session_factory)
    repo = ReturnRepository(session_factory, source="lazyportfolioetf")
    repo.synchronize(row.id, data.model_copy(update={"extraction_method": preferred}))
    service, calls = routing_service(settings, repo, row, data, monkeypatch)

    def deny(*args, **kwargs):
        calls.append(preferred)
        raise ReturnExtractionError(error)

    source = service.adapter.extract if preferred == "backtester" else service.individual.import_one
    source.side_effect = deny
    report = service.run(all_active=True, update=True)
    assert calls == [preferred]
    assert report["batch_stopped"] == error
    assert repo.status(row.id, "USD").status == "restricted"
    assert repo.series(row.id, "USD").extraction_method == preferred


@pytest.mark.parametrize(
    ("method", "known_url", "validated", "expected"),
    [
        (None, True, True, "individual_page"),
        (None, False, True, "backtester"),
        ("individual_page", False, True, "backtester"),
        ("backtester", True, False, "individual_page"),
    ],
)
def test_route_defaults_and_unavailable_or_unvalidated_previous_method(
    settings, session_factory, data, monkeypatch, method, known_url, validated, expected
):
    row = add_instrument(session_factory)
    repo = ReturnRepository(session_factory, source="lazyportfolioetf")
    if method:
        repo.synchronize(row.id, data.model_copy(update={"extraction_method": method}))
        if not validated:
            with session_factory.begin() as session:
                series = session.get(ReturnSeries, repo.series(row.id, "USD").id)
                series.validation_status = "not_validated"
    service, calls = routing_service(settings, repo, row, data, monkeypatch, known_url=known_url)
    service.run(all_active=True, update=True)
    assert calls == [expected]


def test_routing_is_per_currency_and_never_uses_native_page_for_converted_pair(
    settings, session_factory, data, monkeypatch
):
    row = add_instrument(session_factory, currencies=["USD", "JPY"])
    repo = ReturnRepository(session_factory, source="lazyportfolioetf")
    repo.synchronize(row.id, data.model_copy(update={"extraction_method": "individual_page"}))
    repo.synchronize(
        row.id,
        data.model_copy(update={"currency": "JPY", "currency_kind": "converted"}),
    )
    service, calls = routing_service(settings, repo, row, data, monkeypatch)
    service.run(all_active=True, update=True)
    assert calls == ["backtester", "individual_page"]
    assert service.adapter.extract.call_args.args == ("VTI", "JPY")
    assert repo.series(row.id, "USD").extraction_method == "individual_page"
    assert repo.series(row.id, "JPY").extraction_method == "backtester"
    assert repo.series(row.id, "JPY").currency_kind == "converted"


def test_pending_revisions_do_not_trigger_alternate_download_or_change_preference(
    settings, session_factory, data, monkeypatch
):
    row = add_instrument(session_factory)
    repo = ReturnRepository(session_factory, source="lazyportfolioetf")
    repo.synchronize(row.id, data)
    service, calls = routing_service(settings, repo, row, data, monkeypatch)
    # The source is valid; synchronization waits for explicit revision acceptance.
    monkeypatch.setattr(
        repo, "synchronize", lambda *args, **kwargs: {"accepted": False, "revisions": 1}
    )
    service.run(all_active=True, update=True)
    assert calls == ["backtester"]
    assert repo.status(row.id, "USD").status == "partial"
    assert repo.series(row.id, "USD").extraction_method == "backtester"


def test_both_routes_failing_preserve_validated_data_and_record_attempts(
    settings, session_factory, data, monkeypatch
):
    row = add_instrument(session_factory)
    repo = ReturnRepository(session_factory, source="lazyportfolioetf")
    repo.synchronize(row.id, data)
    series = repo.series(row.id, "USD")
    service, calls = routing_service(settings, repo, row, data, monkeypatch)

    def fail_backtester(*args, **kwargs):
        calls.append("backtester")
        raise ReturnExtractionError("Incomplete backtester response")

    def fail_individual(*args, **kwargs):
        calls.append("individual_page")
        raise ReturnExtractionError("Incomplete individual page")

    service.adapter.extract.side_effect = fail_backtester
    service.individual.import_one.side_effect = fail_individual
    service.run(all_active=True, update=True)
    assert calls == ["backtester", "individual_page"]
    checkpoint = repo.status(row.id, "USD")
    assert checkpoint.status == "failed"
    assert len(checkpoint.details["method_attempts"]) == 2
    assert repo.series(row.id, "USD").last_updated_at == series.last_updated_at
    with session_factory() as session:
        observations = list(session.scalars(select(MonthlyReturn).order_by(MonthlyReturn.date)))
    assert [o.return_value for o in observations] == [o.return_value for o in data.observations]


@pytest.mark.parametrize("error", [OSError("Disk full"), SQLAlchemyError("Write failed")])
def test_storage_failure_does_not_download_an_alternate_source(
    settings, session_factory, data, monkeypatch, error
):
    row = add_instrument(session_factory)
    repo = ReturnRepository(session_factory, source="lazyportfolioetf")
    repo.synchronize(row.id, data)
    original = repo.series(row.id, "USD")
    service, calls = routing_service(settings, repo, row, data, monkeypatch)

    def fail_write(*args, **kwargs):
        raise error

    monkeypatch.setattr(repo, "synchronize", fail_write)
    service.run(all_active=True, update=True)
    assert calls == ["backtester"]
    assert repo.series(row.id, "USD").last_updated_at == original.last_updated_at
    checkpoint = repo.status(row.id, "USD")
    assert checkpoint.status == "failed"
    assert checkpoint.details["method_attempts"][0]["reason"] == str(error)


def test_real_backtester_fixture_and_original_csv(data, backtest_html):
    assert validate_returns(data).passed
    assert len(data.observations) == 2805
    assert str(data.start_date) == "1793-01-31"
    assert str(data.end_date) == "2026-09-30"
    assert data.percent_decimal_places == 4
    assert data.extraction_method == "backtester"
    rows = list(csv.DictReader(io.StringIO(monthly_csv(data).lstrip("\ufeff")), delimiter=";"))
    assert len(rows) == 2805 and rows[0]["Period"] == "1793-01"
    assert Decimal(rows[0]["Return"]) / 100 == data.observations[0].return_value
    assert "USID" not in backtest_html and "_wpnonce" not in backtest_html
    with pytest.raises(ReturnExtractionError):
        parse_returns(
            backtest_html,
            symbol="VTI",
            currency="USD",
            source_url="test",
            extracted_at=datetime.now(UTC),
        )


def test_vtv_real_fixture():
    html = Path("tests/fixtures/lazyportfolio_backtester_VTV.html").read_text(encoding="utf-8")
    data = _parse_returns(
        html,
        symbol="VTV",
        currency="USD",
        source_url="test",
        extracted_at=datetime.now(UTC),
        backtester=True,
    )
    assert validate_returns(data).passed and len(data.observations) == 1197


@pytest.mark.parametrize("change", ["flow", "weight", "tax", "capital", "currency"])
def test_rejects_simulation_features(backtest_html, change):
    from app.ingestion.lazyportfolio.returns_parser import embedded_state

    state = embedded_state(backtest_html)
    p = next(iter(state["portfolios"].values()))
    if change == "flow":
        state["settings"]["withCashflow"] = True
    elif change == "weight":
        p["components"]["weights"] = {"VTI": 99, "SPY": 1}
    elif change == "tax":
        p["withTaxPaid"] = True
    elif change == "capital":
        p["capitaleIniziale"] = 2
    else:
        state["settings"]["currency"] = "JPY"
    # Decimal textual JSON number serialization is needed only for this mutation.
    html = "<script>const _d = " + json.dumps(state, default=float) + ";</script>"
    with pytest.raises(ReturnExtractionError):
        _parse_returns(
            html,
            symbol="VTI",
            currency="USD",
            source_url="test",
            extracted_at=datetime.now(UTC),
            backtester=True,
        )


def test_form_protocol_exact_symbols_and_currency_eligibility(form_html):
    offered = offered_pairs(form_html)
    assert len(offered) == 263 and "^BTC" in offered and "VUN.TO" in offered
    _, fields = form_fields(form_html, "VTI", "USD")
    assert fields["a1"] == "VTI" and fields["p1_1"] == "100"
    assert fields["iA"] == "1" and fields["cA"] == "0"
    assert "h1" not in fields
    state = json.loads(fields["bktState"])
    assert state["ft1"] == "NONE" and not state["txA1"]
    assert fields["sec_checksum"] != "SANITIZED"
    with pytest.raises(PairNotAvailable):
        form_fields(form_html, "VTI", "EUR")


def test_http_submission_and_retry_after(settings, form_html, backtest_html):
    calls = []
    sleeps = []

    def handler(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, text=form_html, headers={"content-type": "text/html"})
        if len([r for r in calls if r.method == "POST"]) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        fields = parse_qs(request.content.decode())
        assert fields["a1"] == ["VTI"] and fields["aC"] == ["USD"]
        return httpx.Response(200, text=backtest_html, headers={"content-type": "text/html"})

    adapter = BacktesterAdapter(
        settings, transport=httpx.MockTransport(handler), sleep=sleeps.append
    )
    data = adapter.extract("VTI", "USD", save_raw=False)
    assert validate_returns(data).passed
    assert [r.method for r in calls] == ["GET", "POST", "POST"]
    assert 7 in sleeps


def test_access_denial_stops_route_without_retry(settings):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(403)

    adapter = BacktesterAdapter(settings, transport=httpx.MockTransport(handler))
    for _ in range(2):
        with pytest.raises(BacktesterRestricted):
            adapter.extract("VTI", "USD")
    assert len(calls) == 1


def test_pilot_detects_difference_without_database_writes(settings, session_factory, data):
    row = add_instrument(session_factory)
    repo = ReturnRepository(session_factory, source="lazyportfolioetf")
    old = data.model_copy(update={"extraction_method": "individual_page"})
    repo.synchronize(row.id, old)
    assert compare(repo, data, row)["passed"]
    data.observations[10].return_value += Decimal("0.001")
    result = compare(repo, data, row)
    assert not result["passed"] and len(result["monthly_differences"]) == 1
    with session_factory() as s:
        assert (
            s.scalar(
                select(MonthlyReturn).where(MonthlyReturn.date == data.observations[10].date)
            ).return_value
            != data.observations[10].return_value
        )


def test_resume_skips_successes_and_records_unavailable_and_revisions(
    settings, session_factory, data
):
    row = add_instrument(session_factory, currencies=["USD", "EUR"])
    repo = ReturnRepository(session_factory, source="lazyportfolioetf")

    class Adapter:
        calls = 0

        def discover_pairs(self):
            return {"VTI": {"currencies": ["USD"]}}

        def extract(self, *args, **kwargs):
            self.calls += 1
            return data

    class Individual:
        def public_page_index(self):
            return {}

    adapter = Adapter()
    service = ReturnPairService(settings, repo, adapter=adapter, individual=Individual())
    service._pilot_valid = lambda: True
    first = service.run(all_active=True)
    assert first["completed_pairs"] == 1
    assert repo.status(row.id, "EUR").status == "not_available_in_backtester"
    assert repo.status(row.id, "USD").status == "completed"
    service.run(all_active=True)
    assert adapter.calls == 1
    revised = data.model_copy(deep=True)
    revised.observations[10].return_value += Decimal("0.1")
    # This intentionally inconsistent source must not alter the completed series.
    data = revised
    result = service.run(all_active=True, update=True)
    assert result["failed_validation_pairs"] == 1
    assert repo.series(row.id, "USD").observation_count == 2805
    path = export_returns(session_factory, settings.export_dir / "method.csv", format="csv")
    assert list(csv.DictReader(path.open()))[0]["extraction_method"] == "backtester"


def test_failed_pilot_blocks_collection(settings, session_factory, monkeypatch):
    repo = ReturnRepository(session_factory, source="lazyportfolioetf")
    service = ReturnPairService(settings, repo)
    monkeypatch.setattr(
        "app.ingestion.lazyportfolio.return_pairs.run_pilot", lambda *args: {"passed": False}
    )
    with pytest.raises(ValueError, match="pilot failed"):
        service.run(all_active=True)


def test_interrupted_pair_resumes_after_committed_pair(settings, session_factory, data):
    first = add_instrument(session_factory)
    second = add_instrument(session_factory, symbol="SPY")
    repo = ReturnRepository(session_factory, source="lazyportfolioetf")

    class Adapter:
        calls = []
        interrupted = False

        def discover_pairs(self):
            return {s: {"currencies": ["USD"]} for s in ("VTI", "SPY")}

        def extract(self, symbol, *args, **kwargs):
            self.calls.append(symbol)
            if symbol == "SPY" and not self.interrupted:
                self.interrupted = True
                raise KeyboardInterrupt
            return data.model_copy(update={"source_symbol": symbol})

    class Individual:
        def public_page_index(self):
            return {}

    adapter = Adapter()
    service = ReturnPairService(settings, repo, adapter=adapter, individual=Individual())
    service._pilot_valid = lambda: True
    with pytest.raises(KeyboardInterrupt):
        service.run(all_active=True)
    assert repo.status(first.id, "USD").status == "completed"
    assert repo.status(second.id, "USD").status == "partial"
    result = service.run(all_active=True)
    assert result["completed_pairs"] == 2
    assert adapter.calls == ["VTI", "SPY", "SPY"]


def test_html_access_restriction_stops_route(settings):
    adapter = BacktesterAdapter(
        settings,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, text="Access denied", headers={"content-type": "text/html"}
            )
        ),
    )
    with pytest.raises(BacktesterRestricted):
        adapter.discover_pairs()


def test_truncated_form_is_rejected(form_html):
    with pytest.raises(ReturnExtractionError, match="Incomplete"):
        offered_pairs(form_html[: len(form_html) // 2])


@pytest.mark.parametrize("symbol", ["BNDX--CAD", "EMB--CAD"])
def test_source_forced_conversion_preserves_catalog_identity(
    settings, session_factory, form_html, symbol, monkeypatch
):
    html = Path(f"tests/fixtures/lazyportfolio_backtester_{symbol}.html").read_text(
        encoding="utf-8"
    )

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, text=form_html, headers={"content-type": "text/html"})
        assert parse_qs(request.content.decode())["a1"] == [symbol]
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    adapter = BacktesterAdapter(
        settings, transport=httpx.MockTransport(handler), sleep=lambda _: None
    )
    data = adapter.extract(symbol, "CAD", save_raw=True)
    assert data.source_symbol == symbol
    assert data.source_component_symbol == symbol.removesuffix("--CAD")
    assert data.currency_kind == "converted"
    assert validate_returns(data).passed
    with session_factory.begin() as session:
        row = Instrument(
            source="lazyportfolioetf",
            source_symbol=symbol,
            ticker=symbol,
            currency="CAD",
            available_currencies=["CAD"],
        )
        session.add(row)
    repo = ReturnRepository(session_factory, source="lazyportfolioetf")
    repo.synchronize(row.id, data)
    assert repo.series(row.id, "CAD").currency_kind == "converted"
    from app.cli.returns import main

    monkeypatch.setattr("app.cli.returns.get_settings", lambda: settings)
    assert main(["validate", "--symbol", symbol, "--currency", "CAD"]) == 0
    # Alias allowance is gated by the actual returned currency-swap flag.
    with pytest.raises(ReturnExtractionError):
        _parse_returns(
            html.replace('"etfSwap":true', '"etfSwap":false'),
            symbol=symbol,
            currency="CAD",
            source_url="test",
            extracted_at=datetime.now(UTC),
            backtester=True,
            component_symbol=symbol.removesuffix("--CAD"),
        )


def test_pair_export_without_currency_selects_native_pair_only(
    settings, session_factory, data, monkeypatch
):
    from app.cli.returns import main

    row = add_instrument(session_factory, currencies=["USD", "JPY"])
    repo = ReturnRepository(session_factory, source="lazyportfolioetf")
    repo.synchronize(row.id, data)
    repo.synchronize(
        row.id, data.model_copy(update={"currency": "JPY", "currency_kind": "converted"})
    )
    monkeypatch.setattr("app.cli.returns.get_settings", lambda: settings)
    assert main(["export", "--symbol", "VTI", "--format", "csv"]) == 0
    path = settings.export_dir / "pairs/VTI_USD_monthly_returns.csv"
    with path.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == len(data.observations)
    assert {r["currency"] for r in rows} == {"USD"}
    assert main(["export", "--symbol", "VTI", "--currency", "JPY", "--format", "csv"]) == 0
    with (settings.export_dir / "pairs/VTI_JPY_monthly_returns.csv").open(
        newline="", encoding="utf-8"
    ) as file:
        assert {r["currency"] for r in csv.DictReader(file)} == {"JPY"}
