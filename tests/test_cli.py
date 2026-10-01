import pytest

from app.cli.lazyportfolio import main
from app.config import Settings
from app.ingestion.lazyportfolio.service import LazyPortfolioDiscoveryService
from tests.ingestion.lazyportfolio.helpers import FixtureClient


@pytest.mark.parametrize("prefix", [[], ["lazyportfolio"]])
def test_cli_entry_point_exports(
    source_html: str,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    prefix: list[str],
) -> None:
    service = LazyPortfolioDiscoveryService(settings, client=FixtureClient(source_html))
    monkeypatch.setattr("app.cli.lazyportfolio.get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.cli.lazyportfolio.LazyPortfolioDiscoveryService", lambda *_, **__: service
    )
    assert main([*prefix, "discover", "--no-db", "--save-raw", "--export"]) == 0
    output = capsys.readouterr().out
    assert "Total instruments: 263" in output
    assert "Validation: PASS" in output
    assert "BNDX--CAD" in output
    assert "no stored historical baseline" in output
    assert (settings.export_dir / "lazyportfolio_instruments.csv").exists()


def test_cli_failed_validation_returns_nonzero(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = LazyPortfolioDiscoveryService(settings, client=FixtureClient("<html>"))
    monkeypatch.setattr("app.cli.lazyportfolio.get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.cli.lazyportfolio.LazyPortfolioDiscoveryService", lambda *_, **__: service
    )
    assert main(["discover", "--no-db"]) == 1
    assert "Validation: FAIL" in capsys.readouterr().out
