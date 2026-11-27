"""Out-of-sample calibration of the circular stationary scenario generator."""

import hashlib
import json
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
from time import perf_counter

import numpy as np

from app.analytics.calibration_metrics import (
    GROUPS,
    METRICS,
    _sorted_crps,
    energy_score,
    path_metrics,
    standardization,
    variogram_components,
    variogram_scores,
)
from app.analytics.models import AnalyticsError
from app.analytics.provenance import panel_provenance

METHODOLOGY = "chronological_bootstrap_block_length_calibration_v1"
SUMMARY = ("min", "p05", "p25", "median", "p75", "p95", "max", "mean", "std")
EXTRA = ("energy", "temporal_returns", "temporal_absolute", "cross_asset")
HISTOGRAM_BINS = 32
MAX_SCENARIO_ASSET_MONTHS = 1_000_000_000
MAX_SCORE_ROWS = 3_000


@dataclass(frozen=True)
class ChronologicalFold:
    number: int
    train_start: int
    test_start: int
    test_stop: int


def chronological_folds(months, train_length, test_length, step=None):
    step = test_length if step is None else step
    if min(train_length, test_length) <= 0 or step < test_length:
        raise AnalyticsError("Train and Test must be positive, and Step must be at least Test.")
    return [
        ChronologicalFold(
            i, start, start + train_length, min(months, start + train_length + test_length)
        )
        for i, start in enumerate(range(0, months - train_length, step), 1)
    ]


def real_intervals(values, horizon):
    count = len(values) // horizon
    return values[: count * horizon].reshape(count, horizon, values.shape[1])


def calibration_indices(
    source_length, output_length, generations, length, seed, fold, *, reference=False
):
    """Bernoulli restarts are equivalent to geometric blocks, with circular continuation."""
    rng = np.random.default_rng(np.random.SeedSequence([seed, fold, int(reference)]))
    starts = rng.integers(source_length, size=(generations, output_length), dtype=np.int32)
    restart = rng.random((generations, output_length)) < 1 / length
    indices = np.empty_like(starts)
    indices[:, 0] = starts[:, 0]
    for month in range(1, output_length):
        indices[:, month] = np.where(
            restart[:, month], starts[:, month], (indices[:, month - 1] + 1) % source_length
        )
    return indices


def _period(panel, start, stop):
    months = panel.returns.index
    return {"start": str(months[start]), "end": str(months[stop - 1]), "months": stop - start}


def describe_fold(panel, fold, horizons, holdout=False):
    return {
        "fold": fold.number,
        "train": _period(panel, fold.train_start, fold.test_start),
        "test": _period(panel, fold.test_start, fold.test_stop),
        "holdout": holdout,
        "intervals": {str(h): (fold.test_stop - fold.test_start) // h for h in horizons},
    }


def calibration_preview(panel, settings):
    folds = chronological_folds(
        len(panel.values), settings["train_length"], settings["test_length"], settings.get("step")
    )
    holdout = folds.pop() if settings["final_holdout"] and folds else None
    horizons = settings["horizons"]
    full = sum(f.test_stop - f.test_start == settings["test_length"] for f in folds)
    lengths = list(
        range(
            settings["min_block_length"],
            settings["max_block_length"] + 1,
            settings["block_length_step"],
        )
    )
    descriptions = [describe_fold(panel, f, horizons) for f in folds]
    warnings = list(panel.warnings)
    active_horizons = [[h for h in horizons if f["intervals"][str(h)]] for f in descriptions]
    active_horizons = [available for available in active_horizons if available]
    trajectory_count = len(active_horizons) * len(lengths) * settings["generations"]
    simulated_asset_months = (
        sum(map(max, active_horizons))
        * (len(lengths) + 1)
        * settings["generations"]
        * len(panel.assets)
    )
    score_rows = sum(map(len, active_horizons)) * len(lengths)
    blocking_reason = None
    if not full:
        blocking_reason = (
            "At least one full Calibration Test is required outside the final Holdout."
            if settings["final_holdout"]
            else "At least one full Calibration Test is required."
        )
    elif simulated_asset_months > MAX_SCENARIO_ASSET_MONTHS or score_rows > MAX_SCORE_ROWS:
        blocking_reason = (
            "This Calibration exceeds the job limit. Reduce generations, lengths, "
            "horizons or the requested historical period."
        )
    if blocking_reason:
        warnings.append(blocking_reason)
    if holdout and holdout.test_stop - holdout.test_start < min(horizons):
        warnings.append("The final Holdout is shorter than every selected horizon.")
    selected_intervals = {
        str(h): sum(f["intervals"][str(h)] for f in descriptions) for h in horizons
    }
    return {
        "ready": blocking_reason is None,
        "blocking_reason": blocking_reason,
        "history": _period(panel, 0, len(panel.values)),
        "folds": descriptions,
        "full_calibration_folds": full,
        "holdout": describe_fold(panel, holdout, horizons, True) if holdout else None,
        "horizons": horizons,
        "lengths": lengths,
        "interval_counts": selected_intervals,
        "generations": settings["generations"],
        "trajectory_count": trajectory_count,
        "simulated_asset_months": simulated_asset_months,
        "warnings": warnings,
        "assets": panel.metadata,
    }


def _flatten(metrics):
    return np.concatenate(list(metrics.values()), axis=1)


def _scales(reference, floors):
    count = np.isfinite(reference).sum(axis=0)
    q25, q75 = np.nanquantile(reference[:, count > 0], [0.25, 0.75], axis=0)
    iqr = np.zeros(len(floors))
    std = np.zeros(len(floors))
    iqr[count > 0] = q75 - q25
    std[count > 0] = np.nanstd(reference[:, count > 0], axis=0)
    method = np.where(iqr >= floors, "IQR", np.where(std >= floors, "SD", "minimum"))
    return np.where(iqr >= floors, iqr, np.maximum(std, floors)), method, count


def _forecast(train, indices, horizons, train_scale, *, variograms=False):
    matrices = {}
    variogram_sums = {}
    for start in range(0, len(indices), 128):
        paths = train[indices[start : start + 128]]
        for h in horizons:
            matrices.setdefault(h, []).append(
                _flatten(path_metrics(paths[:, :h], *train_scale[:2]))
            )
        if variograms:
            for key, values in variogram_components(paths, train_scale):
                total = values.sum(axis=0)
                if key in variogram_sums:
                    variogram_sums[key] += total
                else:
                    variogram_sums[key] = total
    return (
        {h: np.concatenate(chunks) for h, chunks in matrices.items()},
        {key: total / len(indices) for key, total in variogram_sums.items()},
    )


def _histograms(samples, low, high):
    width = high - low
    positions = (samples - low) / np.where(width > 0, width, 1)
    bins = np.minimum((positions * HISTOGRAM_BINS).astype(np.int32), HISTOGRAM_BINS - 1)
    offsets = HISTOGRAM_BINS * np.arange(samples.shape[1])
    counts = np.bincount((bins + offsets).ravel(), minlength=int(offsets[-1] + HISTOGRAM_BINS))
    return counts.reshape(-1, HISTOGRAM_BINS).astype(np.uint16)


def _mean(values):
    return float(np.mean(values)) if len(values) else None


def _by_column(values):
    finite = np.isfinite(values)
    count = finite.sum(axis=0)
    return np.divide(
        np.nansum(values, axis=0), count, out=np.full(len(count), np.nan), where=count > 0
    )


def _describe_forecast(raw_samples, item, forecast_variograms, train_scale):
    # Undefined simulated statistics receive a fixed neutral zero. A statistic with
    # no finite reference draw is excluded for every L. Undefined real observations
    # always remain missing, independently of the candidates.
    invalid = ~np.isfinite(raw_samples)
    samples = np.where(invalid, 0.0, raw_samples)
    actual, scale = item["actual"], item["scale"]
    sorted_samples = np.sort(samples, axis=0)
    crps, ranks = _sorted_crps(sorted_samples, actual)
    crps[:, ~item["applicable"]] = np.nan
    ranks[:, ~item["applicable"]] = np.nan
    quantiles = np.quantile(sorted_samples, [0, 0.05, 0.25, 0.5, 0.75, 0.95, 1], axis=0)
    summary = np.vstack((quantiles, samples.mean(axis=0), samples.std(axis=0)))
    component_scores = _by_column(crps / scale)
    metric_scores = {}
    groups = {group: [] for group in GROUPS}
    primary = np.zeros(len(scale), dtype=bool)
    for metric, start, stop, _ in item["metrics"]:
        group, _, main = METRICS[metric]
        components = component_scores[start:stop]
        score = _mean(components[np.isfinite(components)])
        metric_scores[metric] = score
        if main and score is not None:
            groups[group].append(score)
            primary[start:stop] = True
    group_scores = {k: _mean(v) for k, v in groups.items()}
    energy = energy_score(
        samples[:, : item["assets"]] / scale[: item["assets"]],
        actual[:, : item["assets"]] / scale[: item["assets"]],
    )
    variograms = variogram_scores(
        forecast_variograms, variogram_components(item["raw_test"], train_scale)
    )
    report = {
        "crps": _mean([v for v in group_scores.values() if v is not None]),
        "groups": group_scores,
        "metrics": metric_scores,
        "group_weights": {
            k: 1 / sum(bool(v) for v in groups.values()) if groups[k] else 0 for k in GROUPS
        },
        "primary_metrics": {k: len(v) for k, v in groups.items()},
        "available_members": int(np.count_nonzero(primary & item["applicable"])),
        "minimum_scale_count": int(
            np.count_nonzero(item["applicable"] & (item["scale_method"] == "minimum"))
        ),
        "energy": float(energy.mean()),
        **{metric: float(values.mean()) for metric, values in variograms.items()},
    }
    detail = {
        "summary": summary,
        "histogram": _histograms(samples, quantiles[0], quantiles[-1]),
        "undefined": invalid.sum(axis=0),
        "crps": crps,
        "ranks": ranks,
    }
    ranks = ranks[:, primary]
    actual = actual[:, primary]
    finite = np.isfinite(ranks)
    coverage = [
        ((actual >= quantiles[lo, primary]) & (actual <= quantiles[hi, primary]))[finite]
        for lo, hi in ((1, 5), (2, 4))
    ]
    return report, detail, (ranks[finite], *coverage)


def _fold_scores(panel, fold, settings, lengths, *, report_progress=None):
    train = panel.values[fold.train_start : fold.test_start]
    test = panel.values[fold.test_start : fold.test_stop]
    train_scale = standardization(train)
    horizons = [h for h in settings["horizons"] if len(test) >= h]
    if not horizons:
        return [], {}, {}
    if report_progress:
        report_progress("reference", 0)
    max_horizon = max(horizons)
    generations = settings["generations"]
    seed = settings["random_seed"]
    args = (len(train), max_horizon, generations)
    indices = calibration_indices(
        *args, settings["reference_block_length"], seed, fold.number, reference=True
    )
    fingerprints = {
        "reference": hashlib.sha256(indices.astype("<i4", copy=False).tobytes()).hexdigest()
    }
    reference, _ = _forecast(train, indices, horizons, train_scale)
    left, right = np.triu_indices(len(panel.assets), 1)
    pair_names = [(panel.assets[a], panel.assets[b]) for a, b in zip(left, right, strict=True)]
    items = {}
    for h in horizons:
        real = real_intervals(test, h)
        training = real_intervals(train, h)
        actual_metrics = path_metrics(real, *train_scale[:2])
        metrics, floors = [], []
        for metric, values in actual_metrics.items():
            members = (
                pair_names if METRICS[metric][0] == "cross_asset" else [(a,) for a in panel.assets]
            )
            metrics.append((metric, len(floors), len(floors) + values.shape[1], members))
            floors.extend([METRICS[metric][1]] * values.shape[1])
        actual = _flatten(actual_metrics)
        scale, method, reference_count = _scales(reference[h], np.asarray(floors))
        items[h] = {
            "actual": actual,
            "train": _flatten(path_metrics(training, *train_scale[:2])),
            "intervals": [
                _period(panel, fold.test_start + i * h, fold.test_start + (i + 1) * h)
                for i in range(len(real))
            ],
            "scale": scale,
            "scale_method": method,
            "reference_count": reference_count,
            "applicable": (reference_count > 0) & np.isfinite(actual).any(axis=0),
            "metrics": metrics,
            "assets": len(panel.assets),
            "raw_test": real,
            "forecasts": {},
        }
    rows, pit = [], {}
    for step, length in enumerate(lengths, start=1):
        if report_progress:
            report_progress("scoring", step, length)
        indices = calibration_indices(*args, length, seed, fold.number)
        fingerprints[str(length)] = hashlib.sha256(
            indices.astype("<i4", copy=False).tobytes()
        ).hexdigest()
        forecasts, variograms = _forecast(train, indices, horizons, train_scale, variograms=True)
        for h, item in items.items():
            components = {
                k: v[: h - k[1]] if k[0] != "cross_asset" else v[:h]
                for k, v in variograms.items()
                if k[0] == "cross_asset" or k[1] < h
            }
            report, detail, calibration = _describe_forecast(
                forecasts[h], item, components, train_scale
            )
            rows.append(
                {
                    "fold": fold.number,
                    "length": length,
                    "horizon": h,
                    "intervals": len(item["actual"]),
                    **report,
                }
            )
            item["forecasts"][length] = detail
            pit.setdefault(length, []).append(calibration)
    for item in items.values():
        del item["raw_test"]
    return rows, items, {"fingerprints": fingerprints, "pit": pit}


def _aggregate(rows, field="crps", subgroup=None):
    horizons = {}
    for row in rows:
        value = row[field] if subgroup is None else row[field].get(subgroup)
        if value is not None:
            horizons.setdefault(row["horizon"], []).append(value)
    return _mean([_mean(values) for values in horizons.values()])


def _metric_matrix(rows, columns, field, subgroup=False):
    return {
        str(column): {
            str(length): _aggregate(
                [
                    row
                    for row in rows
                    if row["length"] == length and (subgroup or row[field] == column)
                ],
                field if subgroup else "crps",
                column if subgroup else None,
            )
            for length in sorted({row["length"] for row in rows})
        }
        for column in columns
    }


def _best(scores):
    available = [(score, int(length)) for length, score in scores.items() if score is not None]
    return min(available)[1] if available else None


def _pit_summary(data):
    ranks, outer, inner = (np.concatenate(parts) for parts in zip(*data, strict=True))
    return {
        "count": len(ranks),
        "histogram": np.histogram(ranks, bins=10, range=(0, 1))[0].tolist(),
        "p05_p95_coverage": _mean(outer),
        "p25_p75_coverage": _mean(inner),
        "median_absolute_deviation": float(np.median(abs(ranks - 0.5))) if len(ranks) else None,
    }


def analyze_calibration(
    panel, *, settings, _holdout_length=None, _holdout_viewed=False, _report_progress=None
):
    started = perf_counter()
    preview = calibration_preview(panel, settings)
    if not preview["ready"]:
        raise AnalyticsError(preview["blocking_reason"])
    folds = chronological_folds(
        len(panel.values), settings["train_length"], settings["test_length"], settings.get("step")
    )
    holdout = folds.pop() if settings["final_holdout"] else None
    holdout_run = _holdout_length is not None
    if holdout_run:
        if holdout is None or not any(preview["holdout"]["intervals"].values()):
            raise AnalyticsError(
                "No final Holdout interval is available for the selected horizons."
            )
        folds = [holdout]
    lengths = [_holdout_length] if holdout_run else preview["lengths"]
    minimum_horizon = min(settings["horizons"])
    scored_folds = [fold for fold in folds if fold.test_stop - fold.test_start >= minimum_horizon]
    total_folds = len(scored_folds)
    steps_per_fold = len(lengths) + 1
    total_steps = total_folds * steps_per_fold + 1

    def report_fold(fold_index, phase, completed, length=None):
        _report_progress(
            {
                "phase": phase,
                "completed_steps": (fold_index - 1) * steps_per_fold + completed,
                "total_steps": total_steps,
                "fold_index": fold_index,
                "total_folds": total_folds,
                "block_length": length,
            }
        )

    rows, details, fingerprints, pit = [], {}, {}, {length: [] for length in lengths}
    for fold_index, fold in enumerate(scored_folds, start=1):
        report = partial(report_fold, fold_index) if _report_progress else None
        fold_rows, fold_details, metadata = _fold_scores(
            panel, fold, settings, lengths, report_progress=report
        )
        rows.extend(fold_rows)
        if metadata:
            details[fold.number] = fold_details
            fingerprints[str(fold.number)] = metadata["fingerprints"]
            for length, intervals in metadata["pit"].items():
                pit[length].extend(intervals)
    if _report_progress:
        _report_progress(
            {
                "phase": "summarizing",
                "completed_steps": total_steps - 1,
                "total_steps": total_steps,
                "total_folds": total_folds,
            }
        )
    rows_by_length = {
        length: [row for row in rows if row["length"] == length] for length in lengths
    }
    score = {length: _aggregate(items) for length, items in rows_by_length.items()}
    selected = _best(score)
    minimum = score[selected]
    if holdout_run:
        selected = _holdout_length
    ranking = [
        {
            "length": length,
            "score": value,
            "relative_difference": value / minimum - 1 if minimum else (0 if value == 0 else None),
            "comparable": value <= minimum * (1 + settings["practical_tolerance"]),
            **{field: _aggregate(rows_by_length[length], field) for field in EXTRA},
        }
        for length, value in sorted(score.items(), key=lambda item: (item[1], item[0]))
    ]
    horizons = sorted({row["horizon"] for row in rows})
    by_horizon = _metric_matrix(rows, horizons, "horizon")
    by_fold = _metric_matrix(rows, details, "fold")
    by_group = _metric_matrix(rows, GROUPS, "groups", True)
    by_metric = _metric_matrix(rows, METRICS, "metrics", True)
    sensitivity = (
        {
            field: {
                str(item): _best(
                    {
                        length: _aggregate([row for row in group if row[field] != item])
                        for length, group in rows_by_length.items()
                    }
                )
                for item in keys
            }
            for field, keys in (("fold", details), ("horizon", horizons))
        }
        if not holdout_run
        else {}
    )
    provenance = panel_provenance(panel)
    provenance.pop("tax_methodology")
    source_paths = (Path(__file__), Path(__file__).with_name("calibration_metrics.py"))
    provenance["calibration_engine_sha256"] = hashlib.sha256(
        b"\n".join(path.read_bytes().replace(b"\r\n", b"\n") for path in source_paths)
    ).hexdigest()
    provenance.update(
        {
            "settings": settings,
            "asset_ids": panel.assets,
            "return_basis": panel.return_basis,
            "sample_fingerprints": fingerprints,
            "sample_encoding": "row-major little-endian int32, positions local to Train",
            "fold_positions": [asdict(fold) for fold in folds],
            "common_random_numbers": "SeedSequence([random_seed, fold, reference_flag]), "
            "shared by candidates",
            "normalization": "Train-only reference IQR, SD fallback, then metric-specific minimum",
            "crps": "exact empirical CRPS with all ordered pairs; sorted prefix sums",
            "energy": "exact Euclidean, Train-scaled compound returns, 128-row distance batches",
            "variogram": "p=0.5, equal lags to 12 or equal asset pairs, Train standardized",
            "reference_metadata": {
                key: value
                for key, value in panel.reference_metadata.items()
                if key not in {"fixed_annual_rate", "monthly_convention"}
            },
        }
    )
    provenance["calculation_sha256"] = hashlib.sha256(
        json.dumps(
            {
                "methodology": METHODOLOGY,
                "engine": provenance["calibration_engine_sha256"],
                "settings": settings,
                "assets": panel.assets,
                "return_basis": panel.return_basis,
                "fold_positions": provenance["fold_positions"],
                "fold_dates": [
                    [
                        str(panel.returns.index[f.train_start]),
                        str(panel.returns.index[f.test_stop - 1]),
                    ]
                    for f in folds
                ],
                "samples": fingerprints,
                "train_test": [
                    hashlib.sha256(
                        panel.values[f.train_start : f.test_stop].astype("<f8").tobytes()
                    ).hexdigest()
                    for f in folds
                    if f.number in details
                ],
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return {
        "methodology": METHODOLOGY,
        "phase": "holdout" if holdout_run else "calibration",
        "holdout_status": ("previously_viewed" if _holdout_viewed else "evaluated")
        if holdout_run
        else (
            "available"
            if holdout and any(preview["holdout"]["intervals"].values())
            else "unavailable"
        ),
        "preview": preview,
        "optimal_length": selected,
        "minimum_score": minimum,
        "ranking": ranking,
        "rows": rows,
        "group_weights": {group: 1 / len(GROUPS) for group in GROUPS},
        "horizon_weights": {str(h): 1 / len(horizons) for h in horizons},
        "by_horizon": by_horizon,
        "by_fold": by_fold,
        "by_group": by_group,
        "by_metric": by_metric,
        "best_by_horizon": {key: _best(values) for key, values in by_horizon.items()},
        "best_by_group": {key: _best(values) for key, values in by_group.items()},
        "sensitivity": sensitivity,
        "pit": {str(length): _pit_summary(parts) for length, parts in pit.items()},
        "metrics": {
            key: {"group": group, "minimum_scale": floor, "primary": primary}
            for key, (group, floor, primary) in METRICS.items()
        },
        "runs": {
            str(fold): {
                str(h): {metric: members for metric, _, _, members in item["metrics"]}
                for h, item in data.items()
            }
            for fold, data in details.items()
        },
        "reproducibility": provenance,
        "execution_seconds": perf_counter() - started,
    }, details


def _finite(value):
    return float(value) if np.isfinite(value) else None


def calibration_detail(details, fold, horizon, metrics, member, lengths):
    item = details[fold][horizon]
    selection = set(metrics)
    output = {}
    for metric, start, _, members in item["metrics"]:
        if metric not in selection or member not in members:
            continue
        column = start + members.index(member)
        series = {}
        for length in lengths:
            forecast = item["forecasts"][length]
            valid = forecast["crps"][:, column]
            mean_crps = _mean(valid[np.isfinite(valid)])
            series[str(length)] = {
                **dict(zip(SUMMARY, forecast["summary"][:, column].tolist(), strict=True)),
                "histogram": forecast["histogram"][column].tolist(),
                "undefined": int(forecast["undefined"][column]),
                "crps": [_finite(v) for v in valid],
                "mean_crps": mean_crps,
                "ranks": [_finite(v) for v in forecast["ranks"][:, column]],
            }
        output[metric] = {
            "scale": float(item["scale"][column]),
            "scale_method": item["scale_method"][column],
            "reference_count": int(item["reference_count"][column]),
            "train": [_finite(v) for v in item["train"][:, column]],
            "actual": [_finite(v) for v in item["actual"][:, column]],
            "series": series,
        }
    return {"intervals": item["intervals"], "metrics": output}
