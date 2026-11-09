"""Lector de resultados JMeter en formato CSV (archivo .jtl / .csv de "Simple Data Writer").

Validación estricta de los campos que deciden el resultado (timeStamp, elapsed, label, success):
cualquier valor inválido detiene la evaluación con archivo, línea, columna y valor. Un dato
corrupto nunca se interpreta en silencio (p. ej. success="tru" NO cuenta como petición fallida).
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

from ..models import InvalidDataError, LoadTestResult, Sample

REQUIRED = ("timeStamp", "elapsed", "label", "success")
OPTIONAL_NUMERIC = ("Latency", "bytes")
MAX_ERRORS_SHOWN = 10


def _detect_delimiter(header_line: str) -> str:
    # Los archivos del kit mezclan ';' (producción) y ',' (versiones A y B).
    # Se usa el encabezado porque no tiene comillas ni valores con comas.
    candidates = [",", ";", "\t", "|"]
    return max(candidates, key=header_line.count)


def parse_success(value: str | None) -> bool:
    """Solo 'true' o 'false' (sin importar mayúsculas ni espacios). Cualquier otro valor es inválido."""
    v = (value or "").strip().lower()
    if v == "true":
        return True
    if v == "false":
        return False
    raise ValueError(f"se esperaba true o false y llegó {value!r}")


def _parse_non_negative_int(value: str | None) -> int:
    v = (value or "").strip()
    if not v.isdigit():  # rechaza vacío, negativos, decimales y texto
        raise ValueError(f"se esperaba un entero >= 0 y llegó {value!r}")
    return int(v)


def _optional_int(value: str | None) -> int | None:
    v = (value or "").strip()
    return int(v) if v.isdigit() else None


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
        raise InvalidDataError(f"{path}: el archivo está vacío")

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
        raise InvalidDataError(f"{path}: faltan columnas obligatorias {missing}. Encabezado: {header}")

    samples: list[Sample] = []
    errors: list[str] = []
    optional_invalid = 0
    for row in reader:
        line = reader.line_num
        if None in row or any(row.get(c) is None for c in header):
            errors.append(f"línea {line}: número de columnas distinto al encabezado ({len(header)})")
            continue
        try:
            column = "timeStamp"
            ts = _parse_non_negative_int(row["timeStamp"])
            column = "elapsed"
            elapsed = _parse_non_negative_int(row["elapsed"])
            column = "label"
            label = row["label"].strip()
            if not label:
                raise ValueError("está vacío")
            column = "success"
            success = parse_success(row["success"])
        except ValueError as exc:
            errors.append(f"línea {line}, columna {column}: {exc}")
            continue

        latency = _optional_int(row.get("Latency"))
        size = _optional_int(row.get("bytes"))
        optional_invalid += sum(
            1 for c, v in (("Latency", latency), ("bytes", size)) if c in header and v is None
        )
        samples.append(Sample(timestamp_ms=ts, elapsed_ms=float(elapsed), label=label, success=success,
                              latency_ms=latency, response_code=(row.get("responseCode") or "").strip(),
                              bytes=size))

    if errors:
        shown = "\n  - ".join(errors[:MAX_ERRORS_SHOWN])
        more = f"\n  ... y {len(errors) - MAX_ERRORS_SHOWN} más" if len(errors) > MAX_ERRORS_SHOWN else ""
        raise InvalidDataError(
            f"{path}: {len(errors)} registro(s) inválido(s) en campos críticos; no se evalúa para no "
            f"decidir con datos corruptos:\n  - {shown}{more}"
        )
    if optional_invalid:
        notes.append(f"{optional_invalid} valor(es) inválido(s) en columnas opcionales (Latency/bytes); "
                     "se ignoraron (solo afectan el diagnóstico, no la decisión).")

    out_of_order = sum(1 for a, b in zip(samples, samples[1:]) if b.timestamp_ms < a.timestamp_ms)
    if out_of_order:
        notes.append(
            f"{out_of_order} línea(s) con timestamp fuera de orden; se ordenaron por tiempo "
            "antes de analizar (la ventana de calentamiento se calcula por timestamp, no por posición)."
        )
        samples.sort(key=lambda s: s.timestamp_ms)

    if not samples:
        raise InvalidDataError(f"{path}: no hay peticiones")

    return LoadTestResult(source=str(path), samples=samples, data_notes=notes)
