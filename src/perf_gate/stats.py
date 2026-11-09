"""Estadísticas de una prueba de carga."""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from .models import LoadTestResult, Sample


def percentile(sorted_values: list[float], p: float) -> float:
    """Percentil con interpolación lineal (mismo criterio que numpy por defecto)."""
    if not sorted_values:
        return math.nan
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


@dataclass
class EndpointStats:
    label: str
    count: int
    errors: int
    avg: float
    p50: float
    p90: float
    p95: float
    p99: float
    max: float
    avg_latency: float | None   # TTFB promedio
    avg_bytes: float | None

    @property
    def error_rate_pct(self) -> float:
        return 100.0 * self.errors / self.count if self.count else 0.0

    def pct(self, p: int) -> float:
        values = {50: self.p50, 90: self.p90, 95: self.p95, 99: self.p99}
        if p not in values:
            raise ValueError(f"Percentil {p} no soportado; usa 50, 90, 95 o 99")
        return values[p]


def endpoint_stats(label: str, samples: list[Sample]) -> EndpointStats:
    values = sorted(s.elapsed_ms for s in samples)
    lat = [s.latency_ms for s in samples if s.latency_ms is not None]
    size = [s.bytes for s in samples if s.bytes is not None]
    return EndpointStats(
        label=label,
        count=len(values),
        errors=sum(1 for s in samples if not s.success),
        avg=sum(values) / len(values),
        p50=percentile(values, 50),
        p90=percentile(values, 90),
        p95=percentile(values, 95),
        p99=percentile(values, 99),
        max=values[-1],
        avg_latency=sum(lat) / len(lat) if lat else None,
        avg_bytes=sum(size) / len(size) if size else None,
    )


@dataclass
class RunStats:
    source: str
    total_samples: int
    steady_samples: int
    duration_s: float
    steady_duration_s: float
    throughput_rps: float            # peticiones/segundo en estado estable
    error_rate_pct: float            # en estado estable
    stabilization_s: float | None    # cuánto tardó en estabilizarse
    endpoints: dict[str, EndpointStats]      # SOLO fase estable (sin calentamiento)
    data_notes: list[str]
    warmup_s: float = 0.0            # segundos excluidos realmente
    warmup: WarmupStats | None = None        # métricas de la fase de calentamiento (no deciden por endpoint)


@dataclass
class WarmupStats:
    """La fase excluida sigue medida y visible: excluirla de la comparación no la vuelve irrelevante."""
    samples: int
    avg: float
    p95: float
    error_rate_pct: float


def stabilization_time(samples: list[Sample], start_ms: int, steady_avg: float,
                       bucket_s: int = 10, tolerance: float = 1.25) -> float | None:
    """Segundos desde el inicio hasta que el tiempo promedio por bloque de `bucket_s`
    queda (y se mantiene) por debajo de `tolerance` x el promedio en estado estable."""
    buckets: dict[int, list[float]] = defaultdict(list)
    for s in samples:
        buckets[(s.timestamp_ms - start_ms) // (bucket_s * 1000)].append(s.elapsed_ms)
    if not buckets:
        return None
    keys = sorted(buckets)
    limit = steady_avg * tolerance
    stable_from = None
    for k in keys:
        avg = sum(buckets[k]) / len(buckets[k])
        if avg <= limit:
            if stable_from is None:
                stable_from = k
        else:
            stable_from = None
    return None if stable_from is None else float(stable_from * bucket_s)


def compute_run_stats(result: LoadTestResult, warmup_s: float) -> RunStats:
    samples = result.samples
    start = min(s.timestamp_ms for s in samples)
    end = max(s.timestamp_ms + s.elapsed_ms for s in samples)
    cutoff = start + warmup_s * 1000
    steady = [s for s in samples if s.timestamp_ms >= cutoff]
    warm = [s for s in samples if s.timestamp_ms < cutoff]
    notes = list(result.data_notes)
    applied = float(warmup_s)
    if not steady:
        notes.append(
            f"La ventana de calentamiento ({warmup_s:.0f}s) cubre toda la prueba; se usan todas las peticiones."
        )
        steady, warm, cutoff, applied = samples, [], start, 0.0
    warm_values = sorted(s.elapsed_ms for s in warm)
    warmup = WarmupStats(
        samples=len(warm),
        avg=sum(warm_values) / len(warm_values),
        p95=percentile(warm_values, 95),
        error_rate_pct=100.0 * sum(1 for s in warm if not s.success) / len(warm),
    ) if warm else None

    steady_duration = max((end - cutoff) / 1000.0, 1e-9)
    by_label: dict[str, list[Sample]] = defaultdict(list)
    for s in steady:
        by_label[s.label].append(s)

    steady_avg = sum(s.elapsed_ms for s in steady) / len(steady)
    return RunStats(
        source=result.source,
        total_samples=len(samples),
        steady_samples=len(steady),
        duration_s=(end - start) / 1000.0,
        steady_duration_s=steady_duration,
        throughput_rps=len(steady) / steady_duration,
        error_rate_pct=100.0 * sum(1 for s in steady if not s.success) / len(steady),
        stabilization_s=stabilization_time(samples, start, steady_avg),
        endpoints={lbl: endpoint_stats(lbl, ss) for lbl, ss in sorted(by_label.items())},
        data_notes=notes,
        warmup_s=applied,
        warmup=warmup,
    )
