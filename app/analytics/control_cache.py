"""Portable controlled-path summaries, keyed by actual data and simulation settings."""

import hashlib
import json
import math

from app.analytics.provenance import engine_identity, panel_provenance


def control_cache_key(panel, user_weights, expected_block_length, random_seed):
    identity = {
        "panel": panel_provenance(panel)["reference_panel_sha256"],
        "engine": engine_identity()["source_sha256"],
        "simulation": panel.simulation_options,
        "weights": user_weights,
        "block": expected_block_length,
        "seed": random_seed,
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def indices_hash(indices):
    return hashlib.sha256(indices.astype("<i8").tobytes()).hexdigest()


def usable_original(path):
    if not isinstance(path, dict):
        return False
    return (
        all(
            k in path
            for k in (
                "net",
                "gross",
                "total_commissions",
                "trade_count",
                "annualized_turnover",
                "rebalancing_events",
            )
        )
        and all(isinstance(path.get(k), dict) for k in ("net", "gross"))
        and all(
            isinstance(path["net"].get(k), (int, float)) and math.isfinite(path["net"][k])
            for k in (
                "annualized_arithmetic_return",
                "annualized_volatility",
                "annualized_cagr",
                "maximum_drawdown",
                "terminal_wealth",
            )
        )
        and "historical_sharpe_ratio" in path["net"]
    )


def usable_sample(sample):
    return isinstance(sample, dict) and (
        bool(sample.get("error"))
        or all(
            isinstance(sample.get(k), (int, float)) and math.isfinite(sample[k])
            for k in ("expected_return", "volatility")
        )
    )


def export_control_cache(panel, user_weights, result):
    samples = []
    for sample in result["portfolio_control_samples"]:
        net = sample.get("controlled", {}).get("net")
        samples.append(
            {
                "indices_sha256": sample["indices_sha256"],
                "expected_return": net["annualized_arithmetic_return"] if net else None,
                "volatility": net["annualized_volatility"] if net else None,
                "error": sample.get("reason") if not net else None,
            }
        )
    return {
        "version": 1,
        "key": control_cache_key(
            panel, user_weights, result["expected_block_length"], result["random_seed"]
        ),
        "original": result["portfolio_controls"],
        "samples": samples,
    }
