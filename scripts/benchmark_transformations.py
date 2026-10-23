"""Engine-only timings and bounded-stream allocation measurements.

Run from the repository root with:
    uv run python -m scripts.benchmark_transformations --memory

Input creation and operation resolution are outside the timed region. Memory
measurements use separate calls; tracemalloc is not total process/native RSS.
"""

from __future__ import annotations

import argparse
import gc
import json
import platform
import statistics
import time
import tracemalloc
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import partial

import numpy as np
import pyproj
from numpy.typing import NDArray

from geodetic_engine.geodesy import Transformation


@dataclass(frozen=True)
class Case:
    source: str
    target: str
    operation: str | None
    point: tuple[float, ...]
    epoch: float | None = None


CASES = {
    "projection": Case("EPSG:4326", "EPSG:32632", None, (10.75, 59.91)),
    "helmert": Case("EPSG:4230", "EPSG:4326", "EPSG:1612", (2.5, 63.5, 100.0)),
    "chained": Case("EPSG:4230", "EPSG:32632", "EPSG:8047", (10.0, 60.0, 100.0)),
    "vertical": Case("EPSG:4979", "EPSG:3855", "EPSG:3858", (-144.0, 72.0, 548.4082)),
    "dynamic": Case(
        "EPSG:4896",
        "EPSG:4938",
        "EPSG:6277",
        (1137080.2487, -214618.1963, 6252133.9585),
        1993.0,
    ),
}


def measure(call: Callable[[], object], repeats: int, memory: bool) -> dict[str, float]:
    call()
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        result = call()
        samples.append(time.perf_counter() - start)
        del result
    measured = {
        "median_ms": statistics.median(samples) * 1000,
        "p95_ms": float(np.percentile(samples, 95)) * 1000,
    }
    if memory:
        gc.collect()
        tracemalloc.start()
        result = call()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        del result
        measured["traced_peak_mib"] = peak / 1024**2
    return measured


def raw_transform(
    transformer: pyproj.Transformer,
    axes: tuple[NDArray[np.float64], ...],
    times: NDArray[np.float64] | None,
) -> tuple[NDArray[np.float64], ...]:
    return transformer.transform(
        axes[0],
        axes[1],
        axes[2] if len(axes) > 2 else None,
        times,
        errcheck=True,
    )[: len(axes)]


def benchmark(case: Case, sizes: list[int], repeats: int, memory: bool) -> None:
    start = time.perf_counter()
    engine = Transformation(case.source, case.target, operation=case.operation)
    construction_ms = (time.perf_counter() - start) * 1000
    probe = engine.transform_arrays(case.point, coordinate_epoch=case.epoch)
    if probe.pipeline is None:
        raise RuntimeError("benchmark needs a replayable pipeline")
    raw = pyproj.Transformer.from_pipeline(probe.pipeline)
    print(json.dumps({"construction_ms": construction_ms, "operation": case.operation}))

    for size in sizes:
        rows = np.tile(case.point, (size, 1))
        rows[:, 0] += np.linspace(0, 0.01, size)
        axes = tuple(np.ascontiguousarray(rows[:, i]) for i in range(rows.shape[1]))
        times = None if case.epoch is None else np.full(size, case.epoch)
        raw_call = partial(raw_transform, raw, axes, times)
        raw_values = raw_call()
        expected = (
            np.column_stack((raw_values[2],))
            if engine.target_crs.crs.is_vertical
            else np.column_stack(raw_values[: len(probe.columns)])
        )
        np.testing.assert_allclose(
            engine.transform_arrays(*axes, coordinate_epoch=case.epoch).to_numpy(),
            expected,
            rtol=0,
            atol=1e-8,
        )
        calls: dict[str, Callable[[], object]] = {
            "raw_pyproj_axes": raw_call,
            "engine_array_axes": partial(
                engine.transform_arrays, *axes, coordinate_epoch=case.epoch
            ),
            "engine_array_rows": partial(
                engine.transform_arrays, rows, coordinate_epoch=case.epoch
            ),
            "legacy_numpy_rows": partial(
                engine.transform, rows, coordinate_epoch=case.epoch
            ),
        }
        for name, call in calls.items():
            measured = measure(call, repeats, memory)
            measured["points_per_second"] = size / (measured["median_ms"] / 1000)
            print(json.dumps({"points": size, "path": name, **measured}))

    batch = np.tile(case.point, (4096, 1))

    def source(count: int) -> Iterator[NDArray[np.float64]]:
        for _ in range(count):
            yield batch

    def consume(count: int) -> int:
        points = 0
        for result in engine.transform_batches(
            source(count), coordinate_epoch=case.epoch
        ):
            points += result.count
        return points

    for count in (10, 100):
        measured = measure(partial(consume, count), repeats, memory)
        assert consume(count) == count * len(batch)
        print(
            json.dumps({"stream_batches": count, "batch_size": len(batch), **measured})
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=CASES, default="projection")
    parser.add_argument("--sizes", nargs="+", type=int, default=[1000, 10_000, 100_000])
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--memory", action="store_true")
    args = parser.parse_args()
    if args.repeats < 1 or any(size < 1 for size in args.sizes):
        parser.error("repeats and sizes must be positive")
    print(
        json.dumps(
            {
                "python": platform.python_version(),
                "numpy": np.__version__,
                "pyproj": pyproj.__version__,
                "proj": pyproj.proj_version_str,
                "case": args.case,
                "repeats": args.repeats,
                "memory": "tracemalloc; excludes input creation; not total process RSS",
            }
        )
    )
    benchmark(CASES[args.case], args.sizes, args.repeats, args.memory)


if __name__ == "__main__":
    main()
