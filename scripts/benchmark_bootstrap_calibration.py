"""Run the default calibration against validated QCross SQLite history."""

import argparse
import json
import tracemalloc
from pathlib import Path
from time import perf_counter

from threadpoolctl import threadpool_limits

from app.analytics.bootstrap_calibration import analyze_calibration, calibration_detail
from app.api.routes.bootstrap_calibration import calibration_panel
from app.db.session import make_engine, make_session_factory
from app.schemas.bootstrap_calibration import CalibrationRequest


def demo_details(result, details):
    """A compact, offline drill-down example for each included analysis."""
    fold = next(iter(details))
    horizon = 12 if 12 in details[fold] else max(details[fold])
    lengths = sorted({row["length"] for row in result["ranking"]})
    display = sorted({result["optimal_length"], *({3, 6, 12, 24} & set(lengths))})
    available = result["runs"][str(fold)][str(horizon)]
    asset = tuple(available["compounded_return"][0])
    metrics = ["compounded_return", "max_drawdown", "drawdown_duration"]
    metrics += [
        f"acf_{family}_{lag}"
        for family in ("returns", "absolute", "squared")
        for lag in range(1, 5)
    ]
    metrics = [name for name in metrics if name in available]
    result["runs"] = {str(fold): {str(horizon): {name: [asset] for name in metrics}}}
    result["demo_detail_lengths"] = display
    return calibration_detail(details, fold, horizon, metrics, asset, display)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/qcross.db"))
    parser.add_argument("--generations", type=int, default=1000)
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--end-date", help="Last eligible historical date (YYYY-MM-DD).")
    parser.add_argument("--demo", type=Path)
    parser.add_argument("--trace-memory", action="store_true")
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error(f"Database does not exist: {args.database}")
    request = CalibrationRequest(
        instrument_ids=[1, 175, 243, 158, 150],
        start_date="1919-01-01",
        end_date=args.end_date,
        return_basis="real" if args.real else "nominal",
        generations=args.generations,
    )
    with make_session_factory(make_engine(f"sqlite:///{args.database.as_posix()}"))() as session:
        panel = calibration_panel(request, session)
    if args.trace_memory:
        tracemalloc.start()
    with threadpool_limits(limits=1):
        start = perf_counter()
        result, details = analyze_calibration(panel, settings=request.settings())
        wall = perf_counter() - start
        result["reproducibility"]["api_request"] = request.model_dump(mode="json")
        print(
            json.dumps(
                {
                    "wall_seconds": wall,
                    "history": result["preview"]["history"],
                    "assets": panel.assets,
                    "folds": result["preview"]["full_calibration_folds"],
                    "generations": args.generations,
                    "trajectories": result["preview"]["trajectory_count"],
                    "lengths": len(result["ranking"]),
                    "basis": panel.return_basis,
                    "winner": result["optimal_length"],
                    "score": result["minimum_score"],
                    "comparable": [r["length"] for r in result["ranking"] if r["comparable"]],
                    "peak_traced_mib": tracemalloc.get_traced_memory()[1] / 2**20
                    if args.trace_memory
                    else None,
                }
            ),
            flush=True,
        )
        if result["holdout_status"] != "available":
            print(json.dumps({"holdout": "unavailable"}), flush=True)
            if args.demo:
                parser.error("The Demo archive requires an evaluable Final Holdout.")
            return
        start = perf_counter()
        holdout, holdout_details = analyze_calibration(
            panel, settings=request.settings(), _holdout_length=result["optimal_length"]
        )
        holdout["reproducibility"]["api_request"] = request.model_dump(mode="json")
        print(
            json.dumps(
                {
                    "holdout_seconds": perf_counter() - start,
                    "holdout_score": holdout["minimum_score"],
                    "holdout": result["preview"]["holdout"],
                }
            ),
            flush=True,
        )
    if args.demo:
        payload = {
            "calibration_detail": demo_details(result, details),
            "holdout_detail": demo_details(holdout, holdout_details),
            "calibration": result,
            "holdout": holdout,
        }
        args.demo.write_text(
            json.dumps(payload, separators=(",", ":"), allow_nan=False), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
