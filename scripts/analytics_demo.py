"""Reproducible read-only stored-USD demonstration; no ingestion or synthetic fallback.

Run: .venv/Scripts/python.exe -m scripts.analytics_demo
Artifacts are written to docs/analytics-demo. Requires the project's dev extras.
"""

import argparse
import hashlib
import json
import logging
import time
from datetime import date
from importlib.metadata import version
from pathlib import Path

import matplotlib
import numpy as np
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import make_url
from threadpoolctl import threadpool_limits

from app.analytics.bootstrap import analyze_bootstrap, analyze_resampled_frontier
from app.analytics.cross_validation import analyze_cross_validation
from app.analytics.data_loader import load_returns
from app.analytics.frontier import analyze_frontier
from app.config import get_settings
from app.db.models import Instrument
from app.db.session import make_engine, make_session_factory
from app.main import create_app
from app.schemas.frontier import AnalyticsRequest

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def save(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")


def save_calculation(path: Path, value: dict, request: dict) -> None:
    full_request = AnalyticsRequest.model_validate(request).model_dump(mode="json")
    value["reproducibility"].update(
        api_request=full_request,
        random_seed=full_request["random_seed"],
        optimization_parameters=full_request,
    )
    save(path, value)


def digest_database(url: str) -> str | None:
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite" or not parsed.database:
        return None
    with Path(parsed.database).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("docs/analytics-demo"))
    parser.add_argument("--skip-api", action="store_true")
    arguments = parser.parse_args()
    output = arguments.output
    output.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    before = digest_database(settings.database_url)
    engine = make_engine(settings.database_url)
    try:
        with make_session_factory(engine)() as session:
            ids = []
            for ticker in ["VTI", "TLT", "GLD"]:
                candidates = list(
                    session.scalars(
                        select(Instrument).where(
                            Instrument.source == "lazyportfolioetf", Instrument.ticker == ticker
                        )
                    )
                )
                if len(candidates) != 1:
                    raise RuntimeError(f"Expected one stored catalog instrument for {ticker}.")
                ids.append(candidates[0].id)
            panel = load_returns(session, ids, start_date=date(1990, 1, 1))
        request = {
            "instrument_ids": ids,
            "start_date": "1990-01-01",
            "currency": "USD",
            "covariance_method": "sample",
            "risk_free_rate": 0.03,
            "random_seed": 42,
            "asset_constraints": {str(ids[1]): {"min": 0.1, "max": 0.7}},
            "group_constraints": {
                "Equity": {"max": 0.7},
                "US Stocks": {"max": 0.6},
                "Fixed Income": {"max": 0.7},
                "Commodities": {"max": 0.4},
            },
        }
        options = {
            k: request[k]
            for k in [
                "covariance_method",
                "asset_constraints",
                "group_constraints",
                "risk_free_rate",
            ]
        }
        save(output / "request.json", request)
        timings, methods = {}, {}
        with threadpool_limits(limits=1):
            for method in ["sample", "ledoit_wolf", "nonlinear", "cv_linear"]:
                print(f"Deterministic frontier: {method}", flush=True)
                result = analyze_frontier(panel, **{**options, "covariance_method": method})
                if result["status"] != "complete":
                    raise RuntimeError(f"{method} frontier incomplete; inspect solver diagnostics.")
                save_calculation(
                    output / f"frontier-{method}.json",
                    result,
                    {**request, "covariance_method": method},
                )
                methods[method] = {
                    "parameters": result["covariance_parameters"],
                    "diagnostics": result["covariance_diagnostics"],
                }
                timings[f"frontier_{method}"] = result["execution_seconds"]
            frontier = json.loads((output / "frontier-sample.json").read_text())
            print("Five-fold CV and ensemble", flush=True)
            cv = analyze_cross_validation(panel, **options, cv_folds=5)
            save_calculation(output / "cross-validation.json", cv, {**request, "cv_folds": 5})
            timings["cross_validation"] = cv["execution_seconds"]
            print("1,000 stationary bootstrap iterations, GMV and Max Sharpe", flush=True)
            bootstrap = analyze_bootstrap(
                panel, **options, bootstrap_iterations=1000, random_seed=42
            )
            save_calculation(
                output / "bootstrap.json",
                bootstrap,
                {
                    **request,
                    "bootstrap_iterations": 1000,
                    "bootstrap_objectives": ["gmv", "max_sharpe"],
                },
            )
            timings["bootstrap_1000_both_objectives"] = bootstrap["execution_seconds"]
            print("Resampled frontier: 250 samples x 51 common risk aversions", flush=True)
            resampled = analyze_resampled_frontier(
                panel, **options, bootstrap_iterations=250, frontier_points=51, random_seed=42
            )
            save_calculation(
                output / "resampled-frontier.json",
                resampled,
                {**request, "bootstrap_iterations": 250, "frontier_points": 51},
            )
            timings["resampled_frontier_250x51"] = resampled["execution_seconds"]
        for label, result in [
            ("CV", cv),
            ("Bootstrap", bootstrap),
            ("Resampled frontier", resampled),
        ]:
            if result["status"] != "complete":
                raise RuntimeError(
                    f"{label} demonstration is incomplete; inspect saved diagnostics."
                )
        print("Plotting saved numerical results", flush=True)
        fig, ax = plt.subplots(figsize=(9, 6), layout="constrained")
        ax.plot(
            [p["metrics"]["volatility"] * 100 for p in frontier["frontier"]],
            [p["metrics"]["expected_return"] * 100 for p in frontier["frontier"]],
            label="Classical efficient frontier",
        )
        ax.plot(
            [p["metrics"]["volatility"] * 100 for p in resampled["frontier"]],
            [p["metrics"]["expected_return"] * 100 for p in resampled["frontier"]],
            linestyle="--",
            label="Resampled frontier (shared risk aversion)",
        )
        for label, portfolio in [
            ("GMV", frontier["gmv"]),
            ("Max Sharpe", frontier["max_sharpe"]),
            ("Equal weight", frontier["equal_weight"]),
            ("CV ensemble", cv["ensemble"]),
            ("Resampled GMV", bootstrap["objectives"]["gmv"]["resampled"]),
            ("Resampled Max Sharpe", bootstrap["objectives"]["max_sharpe"]["resampled"]),
        ]:
            metrics = portfolio["metrics"]
            ax.scatter(
                metrics["volatility"] * 100, metrics["expected_return"] * 100, label=label, s=45
            )
        ax.set(
            xlabel="Annual volatility (%)",
            ylabel="Expected annual arithmetic return (%)",
            title=(
                "Stored USD histories: VTI / TLT / GLD\n"
                f"{panel.returns.index[0]}–{panel.returns.index[-1]} "
                f"({len(panel.values)} months)"
            ),
        )
        ax.grid(alpha=0.25)
        ax.legend(fontsize=8)
        fig.savefig(output / "frontiers.svg")
        fig.savefig(output / "frontiers.png", dpi=160)
        plt.close(fig)
        fig, axes = plt.subplots(1, 2, figsize=(10, 4), layout="constrained")
        for ax, objective in zip(axes, ["gmv", "max_sharpe"], strict=True):
            statistics = bootstrap["objectives"][objective]["stability"]["assets"]
            mean = np.array([statistics[a]["mean"] for a in panel.assets]) * 100
            lows = np.array([statistics[a]["p5"] for a in panel.assets]) * 100
            highs = np.array([statistics[a]["p95"] for a in panel.assets]) * 100
            original = np.array([statistics[a]["original"] for a in panel.assets]) * 100
            ax.bar(["VTI", "TLT", "GLD"], mean, alpha=0.6, label="Mean bootstrap weight")
            # Percentile intervals can exclude the mean, so draw endpoints directly.
            for i, (low, high) in enumerate(zip(lows, highs, strict=True)):
                ax.plot([i, i], [low, high], color="black", linewidth=2)
            ax.scatter(range(3), original, marker="D", color="tab:red", label="Original weight")
            ax.set(
                title=objective.replace("_", " ").upper(), ylabel="Allocation (%)", ylim=(0, 100)
            )
            ax.legend(fontsize=8)
        fig.suptitle(
            "Stationary bootstrap allocation sensitivity: 1,000 iterations; L=12\n"
            "Black lines: P5–P95 of optimized weights, not future-return intervals"
        )
        fig.savefig(output / "weight-stability.svg")
        fig.savefig(output / "weight-stability.png", dpi=160)
        plt.close(fig)
        for svg in output.glob("*.svg"):
            svg.write_text(
                "\n".join(line.rstrip() for line in svg.read_text(encoding="utf-8").splitlines())
                + "\n",
                encoding="utf-8",
            )
        application = create_app(settings, engine=engine)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        save(output / "openapi.json", application.openapi())
        api_timings = {}
        if not arguments.skip_api:
            with TestClient(application) as client:
                for endpoint, overrides in [
                    ("frontier", {}),
                    ("cross-validation", {"cv_folds": 5}),
                    (
                        "bootstrap",
                        {
                            "bootstrap_iterations": 1000,
                            "bootstrap_objectives": ["gmv", "max_sharpe"],
                        },
                    ),
                    ("resampled-frontier", {"bootstrap_iterations": 250, "frontier_points": 51}),
                ]:
                    print(f"API timing: {endpoint}", flush=True)
                    started = time.perf_counter()
                    response = client.post(
                        f"/api/v1/analytics/{endpoint}", json={**request, **overrides}
                    )
                    if response.status_code != 202:
                        raise RuntimeError(response.text)
                    submitted = time.perf_counter() - started
                    job = response.json()
                    while True:
                        status = client.get(job["status_url"]).json()
                        if status["status"] != "running":
                            break
                        time.sleep(0.05)
                    if status["status"] != "completed":
                        raise RuntimeError(str(status))
                    result_started = time.perf_counter()
                    retrieved = client.get(job["result_url"]).json()
                    retrieval = time.perf_counter() - result_started
                    assert retrieved["result"]["status"] == "complete"
                    api_timings[endpoint] = {
                        "submit_seconds": submitted,
                        "wall_seconds_including_polling": time.perf_counter() - started,
                        "job_seconds": status["elapsed_seconds"],
                        "retrieval_seconds": retrieval,
                        "numerical_seconds": retrieved["result"]["execution_seconds"],
                    }
        after = digest_database(settings.database_url)
        if before is not None and before != after:
            raise RuntimeError("Database bytes changed during the read-only demonstration.")
        summary = {
            "instrument_ids": ids,
            "tickers": ["VTI", "TLT", "GLD"],
            "period": frontier["period"],
            "database_sha256_before": before,
            "database_sha256_after": after,
            "database_unchanged": before == after,
            "numerical_timings_seconds": timings,
            "api_timings": api_timings,
            "versions": {
                package: version(package)
                for package in [
                    "numpy",
                    "pandas",
                    "scipy",
                    "scikit-learn",
                    "cvxpy",
                    "clarabel",
                    "fastapi",
                ]
            },
            "methods": methods,
            "warnings": frontier["warnings"],
        }
        save(output / "summary.json", summary)
        write_report(output, frontier, cv, bootstrap, resampled, summary)
        print(
            json.dumps(
                {
                    "period": summary["period"],
                    "numerical_times": timings,
                    "api_times": api_timings,
                    "database_unchanged": before == after,
                },
                indent=2,
            ),
            flush=True,
        )
    finally:
        engine.dispose()


def write_report(output, frontier, cv, bootstrap, resampled, summary):
    lines = [
        "# Stored USD demonstration",
        "",
        "Generated by `scripts/analytics_demo.py`. "
        "All observations come from the existing database. No synthetic or redownloaded histories.",
        "",
        f"Period: **{summary['period']['start']} to {summary['period']['end']}**, "
        f"**{summary['period']['observations']} months**. Annual risk-free rate: 3%.",
        "",
        "![Frontiers](frontiers.png)",
        "",
        "![Weight sensitivity](weight-stability.png)",
        "",
        "## Portfolio comparison",
        "",
        "| Portfolio | VTI | TLT | GLD | Expected return | Volatility "
        "| Sharpe | CAGR | Max drawdown |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    portfolios = [
        ("GMV", frontier["gmv"]),
        ("Max Sharpe", frontier["max_sharpe"]),
        ("Equal weight", frontier["equal_weight"]),
        ("CV ensemble (full-sample)", cv["ensemble"]),
    ]
    portfolios += [
        (f"Resampled {objective}", bootstrap["objectives"][objective]["resampled"])
        for objective in ["gmv", "max_sharpe"]
    ]
    for label, portfolio in portfolios:
        weights, m = portfolio["weights"], portfolio["metrics"]
        cells = [label] + [f"{weights[a] * 100:.2f}%" for a in frontier["assets"]]
        cells += [f"{m[k] * 100:.2f}%" for k in ["expected_return", "volatility"]]
        cells += [f"{m['sharpe_ratio']:.3f}"]
        cells += [f"{m[k] * 100:.2f}%" for k in ["historical_cagr", "historical_max_drawdown"]]
        lines.append("| " + " | ".join(cells) + " |")
    lines += [
        "",
        "CV ensemble and resampled statistics above use original full-sample estimates. "
        "They are not independent out-of-sample performance. The resampled portfolios are "
        "not assumed superior.",
        "",
        "## Five-fold validation",
        "",
        "| Fold | Training months | Validation interval | Months | VTI | TLT | GLD "
        "| Realized arithmetic return | Volatility | Sharpe |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for fold in cv["folds"]:
        v, m = fold["validation"], fold["validation_metrics"]
        cells = [
            str(fold["fold"]),
            str(fold["training"]["observations"]),
            f"{v['start']}–{v['end']}",
            str(v["observations"]),
        ]
        cells += [f"{fold['weights'][a] * 100:.2f}%" for a in frontier["assets"]]
        cells += [
            f"{m[k] * 100:.2f}%"
            for k in ["realized_annual_arithmetic_return", "realized_annual_volatility"]
        ]
        cells += [f"{m['realized_sharpe_ratio']:.3f}"]
        lines.append("| " + " | ".join(cells) + " |")
    lines += [
        "",
        "CV stability:",
        "",
        "```json",
        json.dumps(cv["stability"], indent=2),
        "```",
        "",
        "## Bootstrap weight stability",
    ]
    for objective, result in bootstrap["objectives"].items():
        lines += [
            "",
            f"### {objective}",
            "",
            f"Requested: {result['requested_iterations']}; "
            f"successful: {result['successful_iterations']}; "
            f"failed: {result['failed_iterations']}; status: {result['status']}.",
            "",
            "| Asset | Original | Mean | Median | SD | P5 | P95 | Min | Max "
            "| Lower hit | Upper hit |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for asset, ticker in zip(frontier["assets"], summary["tickers"], strict=True):
            statistics = result["stability"]["assets"][asset]
            keys = [
                "original",
                "mean",
                "median",
                "standard_deviation",
                "p5",
                "p95",
                "minimum",
                "maximum",
                "lower_bound_frequency",
                "upper_bound_frequency",
            ]
            lines.append(
                "| " + " | ".join([ticker] + [f"{statistics[k] * 100:.2f}%" for k in keys]) + " |"
            )
        lines += [
            "",
            "Allocation distance:",
            "",
            "```json",
            json.dumps(result["stability"]["allocation_distance"], indent=2),
            "```",
        ]
    lines += [
        "",
        "## Resampled frontier",
        "",
        f"{resampled['requested_iterations']} requested samples; "
        f"{resampled['successful_iterations']} successful; "
        f"{resampled['failed_iterations']} failed; "
        f"{len(resampled['frontier'])} shared risk-aversion points.",
        "",
        "## Execution times",
        "",
        "Numerical times (seconds):",
        "",
        "```json",
        json.dumps(summary["numerical_timings_seconds"], indent=2),
        "```",
        "",
        "Local FastAPI TestClient timings include actual spawned worker processes and polling, "
        "not external network latency:",
        "",
        "```json",
        json.dumps(summary["api_timings"], indent=2),
        "```",
        "",
        "## Data quality and provenance",
        "",
    ]
    lines += [f"- {warning}" for warning in summary["warnings"]]
    lines += [
        "",
        "Publication endpoints and reconstruction details are retained in each result's "
        "source metadata. High correlation alone does not establish a shared historical source.",
        "",
        f"Database byte hash unchanged: **{summary['database_unchanged']}**.",
        "",
        f"SHA-256: `{summary['database_sha256_after']}`.",
        "",
        "Full per-iteration results, diagnostics, frontier points and estimator parameters are "
        "in the adjacent JSON artifacts. `openapi.json` contains all API request "
        "and response schemas.",
    ]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
