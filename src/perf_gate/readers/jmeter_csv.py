"""Lector de resultados JMeter en formato CSV (archivo .jtl / .csv de "Simple Data Writer")."""
from __future__ import annotations

import csv
import io
from pathlib import Path

from ..models import LoadTestResult, Sample

REQUIRED = ("timeStamp", "elapsed", "label", "success")


def _detect_delimiter(header_line: str) -> str:
    # Los archivos del kit mezclan ';' (producción) y ',' (versiones A y B).
    # Se usa el encabezado porque no tiene comillas ni valores con comas.
    candidates = [",", ";", "\t", "|"]
    return max(candidates, key=header_line.count)


def _to_bool(value: str) -> bool:
    return value.strip().lower() == "true"


def _to_int(value: str) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def looks_like_jmeter(path: Path) -> bool:
    try:
        with path.open(encoding="utf-8-sig", errors="replace") as fh:
            first = fh.readline()
    except OSError:
        return False
    return "timeStamp" in first and "elapsed" in first


def read_jmeter_csv(path: str | Path) -> LoadTestResult:
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig", errors="replace")  # tolera BOM
    lines = text.splitlines()
    if not lines:
        raise ValueError(f"{path}: el archivo está vacío")

    delimiter = _detect_delimiter(lines[0])
    notes: list[str] = []
    if delimiter != ",":
        notes.append(f"Separador '{delimiter}' detectado (no ','); se leyó igual.")

    # csv respeta comillas: "Consultar saldo, cuenta ahorros" es UNA sola etiqueta.
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    header = [h.strip() for h in (reader.fieldnames or [])]
    reader.fieldnames = header
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise ValueError(f"{path}: faltan columnas obligatorias {missing}. Encabezado: {header}")

    samples: list[Sample] = []
    malformed = 0
    for row in reader:
        ts = _to_int(row.get("timeStamp", ""))
        elapsed = _to_int(row.get("elapsed", ""))
        label = (row.get("label") or "").strip()
        if ts is None or elapsed is None or elapsed < 0 or not label or None in row:
            malformed += 1
            continue
        samples.append(
            Sample(
                timestamp_ms=ts,
                elapsed_ms=float(elapsed),
                label=label,
                success=_to_bool(row.get("success", "")),
                latency_ms=_to_int(row.get("Latency", "")),
                response_code=(row.get("responseCode") or "").strip(),
                bytes=_to_int(row.get("bytes", "")),
            )
        )

    if malformed:
        notes.append(f"{malformed} línea(s) mal formadas se descartaron.")

    out_of_order = sum(
        1 for a, b in zip(samples, samples[1:]) if b.timestamp_ms < a.timestamp_ms
    )
    if out_of_order:
        notes.append(
            f"{out_of_order} línea(s) con timestamp fuera de orden; se ordenaron por tiempo "
            "antes de analizar (la ventana de calentamiento se calcula por timestamp, no por posición)."
        )
        samples.sort(key=lambda s: s.timestamp_ms)

    if not samples:
        raise ValueError(f"{path}: no hay peticiones válidas")

    return LoadTestResult(source=str(path), samples=samples, data_notes=notes)
