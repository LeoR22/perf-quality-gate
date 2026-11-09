"""Modelo de datos neutral, independiente de la herramienta de carga (JMeter, k6, ...)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Sample:
    """Una petición hecha por un usuario virtual."""

    timestamp_ms: int      # inicio de la petición (epoch ms)
    elapsed_ms: float      # tiempo total de respuesta
    label: str             # endpoint / transacción
    success: bool
    latency_ms: float | None = None   # tiempo hasta el primer byte (TTFB)
    response_code: str = ""
    bytes: int | None = None


@dataclass
class LoadTestResult:
    """Resultado de una prueba de carga ya normalizado."""

    source: str
    samples: list[Sample]
    # Observaciones de calidad de datos encontradas al leer el archivo.
    data_notes: list[str] = field(default_factory=list)
