"""Small bounded in-memory process runner; no uncontrolled executor threads/queues."""

import asyncio
import multiprocessing as mp
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock
from typing import Any


class JobCapacityError(RuntimeError):
    pass


def _worker(connection, panel, kind: str, options: dict) -> None:
    # Imports inside the worker keep process management independent of numerical code.
    from threadpoolctl import threadpool_limits

    from app.analytics.bootstrap import analyze_bootstrap, analyze_resampled_frontier
    from app.analytics.cross_validation import analyze_cross_validation
    from app.analytics.frontier import analyze_frontier
    from app.analytics.portfolio_controls import (
        analyze_asset_trend_sensitivity,
        analyze_bootstrap_trend_sensitivity,
        has_enabled_trend_rules,
    )
    from app.analytics.rebalancing import analyze_rebalancing
    from app.analytics.rolling_efficiency import analyze_rolling_efficiency

    handlers = {
        "frontier": analyze_frontier,
        "cross-validation": analyze_cross_validation,
        "bootstrap": analyze_bootstrap,
        "resampled-frontier": analyze_resampled_frontier,
        "rolling-efficiency": analyze_rolling_efficiency,
        "rebalancing-optimization": analyze_rebalancing,
        "asset-trend-sensitivity": analyze_asset_trend_sensitivity,
        "bootstrap-trend-sensitivity": analyze_bootstrap_trend_sensitivity,
    }
    try:
        api_request = options.pop("_api_request", None)
        bootstrap_trend_source = None
        user_weights = options.get("user_weights")
        if kind == "bootstrap" and has_enabled_trend_rules(panel, user_weights):
            sample_indices = []
            options["_sample_indices"] = sample_indices
            bootstrap_trend_source = (panel, user_weights, sample_indices)
        with threadpool_limits(limits=1):
            result = handlers[kind](panel, **options)
        result["reproducibility"]["api_request"] = api_request
        result["reproducibility"]["random_seed"] = (
            api_request.get("random_seed") if api_request else None
        )
        result["reproducibility"]["optimization_parameters"] = {
            k: v
            for k, v in options.items()
            if k != "bootstrap_control_cache" and not k.startswith("_")
        }
        connection.send({"result": result, "bootstrap_trend_source": bootstrap_trend_source})
    except Exception as exc:
        connection.send({"error": f"{type(exc).__name__}: {exc}"})
    finally:
        connection.close()


@dataclass
class _Job:
    job_id: str
    kind: str
    submitted_at: str
    started: float
    process: Any
    connection: Any
    status: str = "running"
    result: dict | None = None
    error: str | None = None
    finished: float | None = None
    bootstrap_trend_source: tuple[Any, dict, list] | None = None


class AnalyticsJobs:
    def __init__(
        self,
        *,
        workers: int = 2,
        timeout_seconds: float = 300,
        max_jobs: int = 32,
        retention_seconds: float = 3600,
    ) -> None:
        self.workers, self.timeout = workers, timeout_seconds
        self.max_jobs, self.retention = max_jobs, retention_seconds
        self.context = mp.get_context("spawn")
        self.jobs: dict[str, _Job] = {}
        self.lock = RLock()

    def _finish(self, job: _Job) -> None:
        if job.process.is_alive():
            job.process.join(timeout=0.1)
        if job.process.is_alive():
            job.process.terminate()
            job.process.join(timeout=0.5)
        if job.process.is_alive():
            job.process.kill()
            job.process.join(timeout=0.5)
        job.connection.close()
        job.process.close()
        job.finished = time.monotonic()

    def refresh(self) -> None:
        with self.lock:
            now = time.monotonic()
            for key, job in list(self.jobs.items()):
                if job.finished is not None:
                    if now - job.finished > self.retention:
                        del self.jobs[key]
                    continue
                # Deadlines include child startup and transfer; late results are not accepted.
                if now - job.started >= self.timeout:
                    job.status, job.error = "timed_out", "Analytics execution exceeded its timeout."
                    self._finish(job)
                elif job.connection.poll():
                    try:
                        payload = job.connection.recv()
                        job.result, job.error = payload.get("result"), payload.get("error")
                        job.bootstrap_trend_source = payload.get("bootstrap_trend_source")
                        job.status = "failed" if job.error else "completed"
                    except (EOFError, OSError) as exc:
                        job.status, job.error = "failed", f"Worker transfer failed: {exc}"
                    self._finish(job)
                elif not job.process.is_alive():
                    job.status, job.error = (
                        "failed",
                        f"Worker exited with code {job.process.exitcode}.",
                    )
                    self._finish(job)

    def submit(self, panel, kind: str, options: dict) -> dict:
        with self.lock:
            self.refresh()
            running = sum(job.status == "running" for job in self.jobs.values())
            if running >= self.workers or len(self.jobs) >= self.max_jobs:
                raise JobCapacityError(
                    "Analytics capacity is full; retry after a running job finishes."
                )
            reader, writer = self.context.Pipe(duplex=False)
            process = self.context.Process(
                target=_worker, args=(writer, panel, kind, options), daemon=True
            )
            job_id = uuid.uuid4().hex
            job = _Job(
                job_id, kind, datetime.now(UTC).isoformat(), time.monotonic(), process, reader
            )
            try:
                process.start()
            except Exception:
                reader.close()
                writer.close()
                raise
            writer.close()
            self.jobs[job_id] = job
            return self._read(job)

    def submit_bootstrap_trend_sensitivity(self, job_id: str) -> dict:
        with self.lock:
            self.refresh()
            job = self.jobs.get(job_id)
            if job is None:
                raise LookupError("Исходный расчёт Bootstrap истёк. Выполните его снова.")
            if job.bootstrap_trend_source is None:
                raise ValueError(
                    "Нужен завершённый расчёт Bootstrap с активными правилами SMA/Momentum."
                )
            panel, weights, sample_indices = job.bootstrap_trend_source
            return self.submit(
                panel,
                "bootstrap-trend-sensitivity",
                {
                    "user_weights": weights,
                    "_sample_indices": sample_indices,
                    "_api_request": job.result["reproducibility"]["api_request"],
                },
            )

    def _read(self, job: _Job, *, include_result: bool = False) -> dict:
        base = f"/api/v1/analytics/jobs/{job.job_id}"
        result = {
            "job_id": job.job_id,
            "kind": job.kind,
            "status": job.status,
            "submitted_at": job.submitted_at,
            "elapsed_seconds": (job.finished or time.monotonic()) - job.started,
            "timeout_seconds": self.timeout,
            "status_url": base,
            "result_url": f"{base}/result",
            "error": job.error,
            "bootstrap_trend_sensitivity_available": job.bootstrap_trend_source is not None,
        }
        if include_result:
            result["result"] = job.result
        return result

    def get(self, job_id: str, *, include_result: bool = False) -> dict | None:
        with self.lock:
            self.refresh()
            job = self.jobs.get(job_id)
            return self._read(job, include_result=include_result) if job else None

    async def monitor(self) -> None:
        while True:
            self.refresh()
            await asyncio.sleep(0.25)

    def close(self) -> None:
        with self.lock:
            for job in self.jobs.values():
                if job.status == "running":
                    job.status, job.error = "failed", "Application shutdown."
                    self._finish(job)
