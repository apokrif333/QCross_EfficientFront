import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from scipy.signal import lfilter
from threadpoolctl import threadpool_limits

from app.analytics.bootstrap_calibration import (
    _aggregate,
    analyze_calibration,
    calibration_detail,
    calibration_indices,
    calibration_preview,
    chronological_folds,
    real_intervals,
)
from app.analytics.calibration_metrics import (
    energy_score,
    ensemble_crps,
    path_metrics,
    standardization,
    variogram_components,
    variogram_scores,
)
from app.analytics.models import AnalyticsError, ReturnPanel
from app.analytics.references import real_returns
from app.main import create_app
from app.schemas.bootstrap_calibration import CalibrationRequest
from tests.analytics.test_api import wait_result
from tests.analytics.test_deterministic import seed_database


def _request(**options):
    return CalibrationRequest(
        **{
            "instrument_ids": [1, 2, 3],
            "train_length": 48,
            "test_length": 48,
            "horizons": [3, 6, 12, 24],
            "generations": 48,
            "min_block_length": 3,
            "max_block_length": 12,
            "block_length_step": 9,
            **options,
        }
    )


def _panel(values):
    months = pd.period_range("1980-01", periods=len(values), freq="M")
    assets = [str(i + 1) for i in range(values.shape[1])]
    return ReturnPanel(pd.DataFrame(values, index=months, columns=assets), assets)


def test_chronological_split_adjacent_tests_and_partial_windows():
    folds = chronological_folds(490, 120, 120)
    assert [(f.train_start, f.test_start, f.test_stop) for f in folds] == [
        (0, 120, 240),
        (120, 240, 360),
        (240, 360, 480),
        (360, 480, 490),
    ]
    for left, right in zip(folds[:-1], folds[1:], strict=True):
        assert left.train_start < left.test_start < left.test_stop <= right.test_start
    with pytest.raises(AnalyticsError, match="Step"):
        chronological_folds(490, 120, 120, 100)

    rows = np.arange(1292)[:, None]
    for horizon in (3, 6, 12, 24, 60, 120, 240):
        windows = real_intervals(rows, horizon)
        assert windows.shape == (1292 // horizon, horizon, 1)
        np.testing.assert_array_equal(windows.ravel(), np.arange(len(windows) * horizon))
    assert real_intervals(rows[:10], 12).shape == (0, 12, 1)


def test_default_preview_and_equal_horizon_weights():
    panel = _panel(np.zeros((1292, 3)))
    preview = calibration_preview(panel, CalibrationRequest(instrument_ids=[1, 2, 3]).settings())
    assert preview["full_calibration_folds"] == 4 and preview["trajectory_count"] == 88000
    assert preview["holdout"]["test"]["months"] == 92
    assert preview["holdout"]["intervals"]["120"] == 0
    assert preview["interval_counts"] == {
        "3": 320,
        "6": 160,
        "12": 80,
        "24": 40,
        "60": 16,
        "120": 8,
        "240": 4,
    }
    short = calibration_preview(
        _panel(np.zeros((479, 3))), CalibrationRequest(instrument_ids=[1, 2, 3]).settings()
    )
    assert not short["ready"] and "full Calibration" in short["blocking_reason"]
    oversized = calibration_preview(
        _panel(np.zeros((8000, 15))),
        CalibrationRequest(instrument_ids=list(range(1, 16)), generations=2000).settings(),
    )
    assert not oversized["ready"] and "exceeds the job limit" in oversized["blocking_reason"]
    assert (
        _aggregate(
            [
                {"crps": 1, "horizon": 3, "intervals": 320},
                {"crps": 3, "horizon": 240, "intervals": 4},
            ]
        )
        == 2
    )


def test_train_isolation_circular_restart_and_joint_inflation_sampling():
    idx = calibration_indices(7, 24, 100, 12, 42, 2)
    assert idx.min() == 0 and idx.max() == 6
    assert np.any((idx[:, :-1] == 6) & (idx[:, 1:] == 0))
    np.testing.assert_array_equal(idx, calibration_indices(7, 24, 100, 12, 42, 2))
    assert not np.array_equal(idx, calibration_indices(7, 24, 100, 3, 42, 2))
    raw = np.arange(30)[:, None] / 100
    assets = np.concatenate((raw + 0.05, raw * 2), axis=1)
    cpi = raw[:, 0] / 10
    train = real_returns(assets[:7], cpi[:7])
    stacked = np.column_stack((assets[:7], cpi[:7]))[idx]
    expected = (1 + stacked[..., :2]) / (1 + stacked[..., 2, None]) - 1
    np.testing.assert_array_equal(train[idx], expected)


def test_crps_direct_formula_and_ties():
    samples = np.array([[-1.0, 3.0], [0.0, 1.0], [0.0, 2.0], [7.0, 2.0]])
    actual = np.array([[0.0, 2.0], [-2.0, 0.0], [10.0, 4.0]])
    direct = abs(samples[None, ...] - actual[:, None, :]).mean(axis=1) - (
        0.5 * abs(samples[:, None, :] - samples[None, :, :]).mean(axis=(0, 1))
    )
    crps, ranks = ensemble_crps(samples, actual)
    np.testing.assert_allclose(crps, direct, atol=1e-14)
    np.testing.assert_array_equal(ranks, [[0.5, 0.5], [0, 0], [1, 1]])
    assert ensemble_crps(np.ones((3, 1)), np.ones((1, 1)))[0][0, 0] == 0


def test_energy_and_variogram_direct_formulas():
    rng = np.random.default_rng(21)
    simulated, actual = rng.normal(size=(9, 4, 3)), rng.normal(size=(2, 4, 3))
    a, b = simulated[:, 0], actual[:, 0]
    expected = [
        np.mean([np.linalg.norm(x - y) for x in a])
        - 0.5 * np.mean([np.linalg.norm(x - other) for x in a for other in a])
        for y in b
    ]
    np.testing.assert_allclose(energy_score(a, b, batch=2), expected, rtol=1e-14)
    train_scale = standardization(simulated.reshape(-1, 3))
    forecast = {key: val.mean(axis=0) for key, val in variogram_components(simulated, train_scale)}
    variograms = variogram_scores(forecast, variogram_components(actual, train_scale))
    center, scale, abs_center, abs_scale = train_scale
    for name, transform in (
        ("temporal_returns", lambda x: (x - center) / scale),
        ("temporal_absolute", lambda x: (abs(x) - abs_center) / abs_scale),
        ("cross_asset", lambda x: (x - center) / scale),
    ):
        x, y = transform(simulated), transform(actual)
        terms, weights = [], []
        for month in range(4):
            for asset in range(3):
                if name == "cross_asset":
                    pairs = [(month, other, 1 / 12) for other in range(asset + 1, 3)]
                else:
                    pairs = [
                        (end, asset, 1 / (9 * (4 - end + month))) for end in range(month + 1, 4)
                    ]
                for end, other, weight in pairs:
                    observed = abs(y[:, month, asset] - y[:, end, other]) ** 0.5
                    simulated_mean = np.mean(abs(x[:, month, asset] - x[:, end, other]) ** 0.5)
                    terms.append((observed - simulated_mean) ** 2)
                    weights.append(weight)
        assert sum(weights) == pytest.approx(1)
        np.testing.assert_allclose(variograms[name], np.array(weights) @ terms, rtol=1e-14)


def test_metric_applicability_degenerate_statistics_and_initial_nav():
    monthly = np.full((2, 24, 3), 0.015)
    monthly[:, 0, 0] = -0.20
    center, scale, *_ = standardization(monthly[0])
    stats = path_metrics(monthly, center, scale)
    short = path_metrics(monthly[:, :3], center, scale)
    assert not any(key.startswith("acf") for key in short)
    assert "skewness" not in short and "monthly_correlation" not in short
    assert "acf_returns_12" in stats and "excess_kurtosis" in stats
    assert np.isnan(stats["monthly_correlation"]).all()
    assert np.isnan(stats["acf_returns_1"]).all()
    np.testing.assert_allclose(stats["max_drawdown"][:, 0], [0.20, 0.20])
    assert stats["recovery_censored"][0, 0] == 0
    assert short["recovery_censored"][0, 0] == 1


def test_score_detail_scale_and_reference_are_train_only():
    rng = np.random.default_rng(11)
    observations = rng.normal(0, 0.04, (155, 3))
    altered = observations.copy()
    altered[48:96] *= 3
    settings = _request().settings()
    with threadpool_limits(limits=1):
        original, d1 = analyze_calibration(_panel(observations), settings=settings)
        modified, d2 = analyze_calibration(_panel(altered), settings=settings)
    metrics, member, lengths = ["compounded_return"], ("1",), [3, 12]
    a = calibration_detail(d1, 1, 12, metrics, member, lengths)
    b = calibration_detail(d2, 1, 12, metrics, member, lengths)
    x, y = a["metrics"][metrics[0]], b["metrics"][metrics[0]]
    assert x["scale"] == y["scale"] and x["train"] == y["train"]
    assert x["actual"] != y["actual"]
    for length in ("3", "12"):
        assert x["series"][length]["histogram"] == y["series"][length]["histogram"]
        assert x["series"][length]["median"] == y["series"][length]["median"]
        assert x["series"][length]["crps"] != y["series"][length]["crps"]
        assert sum(x["series"][length]["histogram"]) == settings["generations"]
    assert (
        original["reproducibility"]["sample_fingerprints"]["1"]
        == (modified["reproducibility"]["sample_fingerprints"]["1"])
    )


def test_holdout_isolation_plus_reproducibility_and_partial_period():
    rng = np.random.default_rng(103)
    values = rng.normal(0.01, 0.05, (155, 3))
    after = values.copy()
    after[144:] *= 5
    settings = _request().settings()
    progress = []
    holdout_progress = []
    with threadpool_limits(limits=1):
        calibration, _ = analyze_calibration(
            _panel(values), settings=settings, _report_progress=progress.append
        )
        repeated, _ = analyze_calibration(_panel(after), settings=settings)
        holdout, _ = analyze_calibration(
            _panel(after),
            settings=settings,
            _holdout_length=calibration["optimal_length"],
            _report_progress=holdout_progress.append,
        )
        no_holdout, _ = analyze_calibration(
            _panel(after), settings={**settings, "final_holdout": False}
        )
    for key in ("ranking", "rows", "pit", "by_fold", "sensitivity"):
        assert calibration[key] == repeated[key]
    for key in ("calculation_sha256", "sample_fingerprints"):
        assert calibration["reproducibility"][key] == repeated["reproducibility"][key]
    assert calibration["preview"]["holdout"]["test"]["months"] == 11
    assert {row["fold"] for row in holdout["rows"]} == {3}
    assert {row["horizon"] for row in holdout["rows"]} == {3, 6}
    assert {row["length"] for row in holdout["rows"]} == {calibration["optimal_length"]}
    assert {row["horizon"] for row in no_holdout["rows"] if row["fold"] == 3} == {3, 6}
    assert [update["completed_steps"] for update in progress] == list(range(7))
    assert {update["total_steps"] for update in progress} == {7}
    assert progress[3]["phase"] == "reference" and progress[3]["fold_index"] == 2
    assert progress[-1]["phase"] == "summarizing"
    assert [update["phase"] for update in holdout_progress] == [
        "reference",
        "scoring",
        "summarizing",
    ]
    assert holdout_progress[1]["block_length"] == calibration["optimal_length"]


def _forecast_structure(train, length, horizon=60):
    indices = calibration_indices(len(train), horizon, 768, length, 32, 1)
    paths = train[indices]
    scales = standardization(train)
    metrics = path_metrics(paths, *scales[:2])
    vgrams = {key: values.mean(axis=0) for key, values in variogram_components(paths, scales)}
    return metrics, vgrams


def test_iid_bootstrap_does_not_impose_long_lag_dependence():
    values = np.random.default_rng(2409).normal(0, 0.025, (4600, 2))
    train = values[:4000]
    short, _ = _forecast_structure(train, 3)
    long, _ = _forecast_structure(train, 24)
    assert abs(short["acf_returns_1"].mean()) < 0.06
    assert abs(long["acf_returns_1"].mean()) < 0.06
    assert abs(long["acf_returns_4"].mean() - short["acf_returns_4"].mean()) < 0.035
    observed = real_intervals(values[4000:], 60)
    real = path_metrics(observed, *standardization(train)[:2])
    for key in ("compounded_return", "volatility", "acf_returns_1"):
        a = ensemble_crps(short[key], real[key])[0].mean()
        b = ensemble_crps(long[key], real[key])[0].mean()
        assert abs(a - b) / min(a, b) < 0.20


def test_ar1_clustering_and_multivariate_dependence():
    rng = np.random.default_rng(2409)
    innovations = rng.normal(0, 0.009, (6000, 2))
    innovations[:, 1] = 0.85 * innovations[:, 0] + 0.5 * innovations[:, 1]
    persistent = lfilter([1], [1, -0.88], innovations, axis=0)
    short, _ = _forecast_structure(persistent, 3)
    long, _ = _forecast_structure(persistent, 24)
    assert long["acf_returns_1"].mean() > short["acf_returns_1"].mean() + 0.15
    assert long["acf_returns_4"].mean() > short["acf_returns_4"].mean() + 0.20
    for metrics in (short, long):
        assert metrics["monthly_correlation"].mean() > 0.75

    fluctuating_vol = np.exp(lfilter([1], [1, -0.96], rng.normal(0, 0.23, 6000)))
    clustered = innovations * fluctuating_vol[:, None]
    short, short_v = _forecast_structure(clustered[:4000], 3)
    long, long_v = _forecast_structure(clustered[:4000], 24)
    for family in ("absolute", "squared"):
        assert long[f"acf_{family}_3"].mean() > short[f"acf_{family}_3"].mean() + 0.045
    test = real_intervals(clustered[4000:], 60)
    scale = standardization(clustered[:4000])
    actual = dict(variogram_components(test, scale))
    first = variogram_scores(short_v, actual.items())
    second = variogram_scores(long_v, actual.items())
    assert second["temporal_absolute"].mean() < first["temporal_absolute"].mean()


def test_calibration_live_jobs_detail_cancel_and_freeze(panel, settings, session_factory):
    seed_database(session_factory, panel)
    request = _request(min_block_length=1, block_length_step=11).model_dump(mode="json")
    url = "/api/v1/analytics/bootstrap-calibration"
    with TestClient(create_app(settings)) as client:
        preview = client.post(url + "/preview", json=request)
        assert preview.status_code == 200
        assert preview.json()["full_calibration_folds"] == 3
        assert preview.json()["lengths"] == [1, 12]
        full_grid = client.post(
            url + "/preview", json={**request, "max_block_length": 24, "block_length_step": 1}
        )
        assert full_grid.status_code == 200
        assert full_grid.json()["lengths"] == list(range(1, 25))
        assert client.post(url, json={**request, "step": 12}).status_code == 422
        job = client.post(url, json=request)
        assert job.status_code == 202, job.text
        assert job.json()["progress"]["phase"] == "starting"
        completed = wait_result(client, job.json())
        assert completed["status"] == "completed", completed
        final_progress = client.get(job.json()["status_url"]).json()["progress"]
        assert final_progress["phase"] == "completed"
        assert final_progress["completed_steps"] == final_progress["total_steps"] == 10
        source = completed["result"]
        assert source["preview"] == preview.json()
        assert source["reproducibility"]["api_request"] == request
        detail = client.get(
            url + f"/{job.json()['job_id']}/detail",
            params={
                "fold": 1,
                "horizon": 12,
                "member": "1",
                "metrics": "compounded_return",
                "lengths": [1, 12],
            },
        )
        assert detail.status_code == 200
        assert len(detail.json()["intervals"]) == 4
        assert set(detail.json()["metrics"]["compounded_return"]["series"]) == {"1", "12"}
        request_holdout = {"calibration_job_id": job.json()["job_id"]}
        holdout = client.post(url + "-holdout", json=request_holdout)
        assert holdout.status_code == 202
        done = wait_result(client, holdout.json())
        assert done["status"] == "completed", done
        assert done["progress"]["completed_steps"] == done["progress"]["total_steps"] == 3
        result = done["result"]
        assert result["phase"] == "holdout" and result["holdout_status"] == "evaluated"
        assert [row["length"] for row in result["ranking"]] == [source["optimal_length"]]
        assert (
            client.post(url + "-holdout", json=request_holdout).json()["job_id"] == done["job_id"]
        )

        second = client.post(url, json={**request, "random_seed": 8}).json()
        assert wait_result(client, second)["status"] == "completed"
        replay = client.post(url + "-holdout", json={"calibration_job_id": second["job_id"]})
        assert wait_result(client, replay.json())["result"]["holdout_status"] == "previously_viewed"

        cancellable = client.post(url, json={**request, "generations": 2000}).json()
        cancelled = client.delete(url + "/" + cancellable["job_id"])
        assert cancelled.json()["status"] == "cancelled"
