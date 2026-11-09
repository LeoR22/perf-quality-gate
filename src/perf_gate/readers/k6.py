"""Lector de resultados k6 por petición.

Soporta las dos salidas de k6 que traen cada petición individual:

* JSON (NDJSON):  k6 run --out json=resultados.json script.js
* CSV:            k6 run --out csv=resultados.csv  script.js

El resumen agregado (--summary-export / handleSummary) NO sirve: solo trae
promedios y percentiles globales, sin peticiones individuales ni ventana de tiempo.

Cada petición HTTP de k6 emite varias métricas con el mismo timestamp y los mismos
tags. Se toma `http_req_duration` como tiempo total y se le unen
`http_req_waiting` (TTFB, equivalente a Latency de JMeter) y `http_req_failed`.
"""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from ..models import InvalidDataError, LoadTestResult, Sample

DURATION, WAITING, FAILED = "http_req_duration", "http_req_waiting", "http_req_failed"
_WANTED = {DURATION, WAITING, FAILED}
# Tags que identifican una petición. Se excluyen los que pueden variar entre métricas.
_KEY_TAGS = ("name", "method", "url", "status", "scenario", "group", "vu", "iter")

_RFC3339 = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?$"
)


def parse_time_ms(value: str | float | int) -> int:
    """RFC3339 con nanosegundos (JSON) o epoch en s / ms / µs (CSV) → epoch ms."""
    if isinstance(value, (int, float)) or re.fullmatch(r"\d+(\.\d+)?", str(value).strip()):
        v = float(value)
        if v > 1e14:      # microsegundos
            return int(v / 1000)
        if v > 1e11:      # milisegundos
            return int(v)
        return int(v * 1000)  # segundos (default de --out csv)
    m = _RFC3339.match(str(value).strip())
    if not m:
        raise ValueError(f"timestamp no reconocido: {value!r}")
    day, clock, frac, tz = m.groups()
    dt = datetime.fromisoformat(f"{day}T{clock}")
    micros = int((frac or "0")[:6].ljust(6, "0"))  # Python solo maneja 6 decimales
    if tz in (None, "Z"):
        offset = timedelta(0)
    else:
        sign = 1 if tz[0] == "+" else -1
        hh, mm = tz[1:].replace(":", "")[:2], tz[1:].replace(":", "")[2:]
        offset = sign * timedelta(hours=int(hh), minutes=int(mm or 0))
    dt = dt.replace(microsecond=micros, tzinfo=timezone(offset))
    return int(dt.timestamp() * 1000)


def _label(tags: dict) -> str:
    """Usa el tag `name` (recomendado: tags: { name: 'POST /pagos' }).
    Ojo: k6 2.x copia `name` también en el tag `url`, así que name == url es normal.
    Solo si `name` falta o es una URL completa (default de k6) se arma 'MÉTODO /ruta'."""
    name = (tags.get("name") or "").strip()
    url = (tags.get("url") or "").strip()
    method = (tags.get("method") or "").strip().upper()
    if name and not name.lower().startswith(("http://", "https://")):
        return name
    source = name or url
    if source:
        path = urlsplit(source).path or "/"
        return f"{method} {path}".strip()
    return "(sin nombre)"


def _key(time_raw, tags: dict) -> tuple:
    return (str(time_raw), *(str(tags.get(t, "")) for t in _KEY_TAGS))


class _Builder:
    """Acumula las métricas de cada petición y arma los Sample al final."""

    def __init__(self) -> None:
        self.durations: dict[tuple, list] = {}
        self.waiting: dict[tuple, list[float]] = {}
        self.failed: dict[tuple, list[float]] = {}
        self.order: list[tuple] = []
        self.errors: list[str] = []      # datos críticos inválidos: detienen la evaluación
        self.optional_bad = 0            # TTFB inválido: solo afecta el diagnóstico
        self.second_precision = False    # CSV de k6: timestamp en segundos enteros

    def add(self, metric: str, time_raw, value, tags: dict, where: str = "") -> None:
        try:
            val = float(value)
        except (TypeError, ValueError):
            val = None
        if metric == WAITING and (val is None or val < 0):
            self.optional_bad += 1
            return
        if val is None or val < 0:
            self.errors.append(f"{where}: {metric} con valor inválido {value!r}")
            return
        if metric == FAILED and val not in (0.0, 1.0):
            self.errors.append(f"{where}: {FAILED} debe ser 0 o 1 y llegó {value!r}")
            return
        try:
            parse_time_ms(time_raw)
        except ValueError:
            self.errors.append(f"{where}: timestamp inválido {time_raw!r}")
            return
        k = _key(time_raw, tags)
        if metric == DURATION:
            if k not in self.durations:
                self.order.append(k)
                self.durations[k] = []
            self.durations[k].append((time_raw, val, tags))
        elif metric == WAITING:
            self.waiting.setdefault(k, []).append(val)
        elif metric == FAILED:
            self.failed.setdefault(k, []).append(val)

    def samples(self) -> list[Sample]:
        out: list[Sample] = []
        for k in self.order:
            waits, fails = self.waiting.get(k, []), self.failed.get(k, [])
            for i, (time_raw, dur, tags) in enumerate(self.durations[k]):
                t_ms = parse_time_ms(time_raw)  # ya validado en add()
                if re.fullmatch(r"\d{9,10}", str(time_raw).strip()):
                    self.second_precision = True
                status = str(tags.get("status", ""))
                if i < len(fails):
                    ok = fails[i] == 0
                elif "expected_response" in tags:
                    ok = str(tags["expected_response"]).lower() == "true"
                else:  # k6 muy antiguo: se infiere del código HTTP
                    ok = status.isdigit() and 0 < int(status) < 400
                out.append(Sample(
                    # k6 registra el momento en que TERMINA la petición; se resta la
                    # duración para obtener el inicio, como en JMeter.
                    timestamp_ms=t_ms - int(round(dur)),
                    elapsed_ms=dur,
                    label=_label(tags),
                    success=ok,
                    latency_ms=waits[i] if i < len(waits) else None,
                    response_code=status,
                    bytes=None,  # k6 no reporta bytes por petición
                ))
        return out


def _read_json(path: Path, b: _Builder) -> tuple[bool, bool]:
    """Devuelve (hay puntos, la última línea estaba cortada)."""
    lines, has_points = 0, False
    pending_bad: int | None = None  # una línea ilegible solo se tolera si resulta ser la ÚLTIMA
    with path.open(encoding="utf-8-sig", errors="replace") as fh:
        for number, line in enumerate(fh, start=1):  # línea por línea: los archivos de k6 pueden pesar GB
            line = line.strip()
            if not line:
                continue
            if pending_bad is not None:
                b.errors.append(f"línea {pending_bad}: JSON inválido")
                pending_bad = None
            lines += 1
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                pending_bad = number
                continue
            if lines == 1 and "metrics" in obj and "type" not in obj:
                raise InvalidDataError(
                    f"{path}: es el resumen agregado de k6 (--summary-export), sin peticiones "
                    "individuales. Ejecuta k6 con --out json=resultados.json o --out csv=resultados.csv"
                )
            if obj.get("type") != "Point":
                continue
            has_points = True
            metric = obj.get("metric")
            if metric in _WANTED:
                data = obj.get("data") or {}
                b.add(metric, data.get("time"), data.get("value"), data.get("tags") or {}, f"línea {number}")
    return has_points, pending_bad is not None


def _read_csv(path: Path, b: _Builder) -> None:
    with path.open(encoding="utf-8-sig", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            metric = row.get("metric_name")
            if metric not in _WANTED:
                continue
            tags = {k: v for k, v in row.items()
                    if k not in ("metric_name", "timestamp", "metric_value", "extra_tags", "metadata") and v}
            # extra_tags viene como "vu=1&iter=3"
            for pair in (row.get("extra_tags") or "").split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    tags.setdefault(k, v)
            b.add(metric, row.get("timestamp"), row.get("metric_value"), tags, f"línea {reader.line_num}")


def looks_like_k6(path: Path) -> str | None:
    """Devuelve 'json', 'csv' o None mirando el contenido, no la extensión."""
    try:
        with path.open(encoding="utf-8-sig", errors="replace") as fh:
            head = fh.read(4096).lstrip()
    except OSError:
        return None
    if head.startswith("metric_name,"):
        return "csv"
    if head.startswith("{") and ('"type"' in head or '"metrics"' in head):
        return "json"
    return None


def read_k6(path: str | Path) -> LoadTestResult:
    path = Path(path)
    kind = looks_like_k6(path)
    if kind is None:
        raise InvalidDataError(f"{path}: no parece una salida de k6 (--out json o --out csv)")

    b = _Builder()
    notes: list[str] = [f"Resultados k6 ({kind.upper()}); etiquetas tomadas del tag 'name'."]
    if kind == "json":
        has_points, truncated_tail = _read_json(path, b)
        if not has_points:
            raise InvalidDataError(f"{path}: el archivo k6 no tiene puntos de métricas (type=Point)")
        if truncated_tail:
            notes.append("La última línea estaba incompleta (k6 interrumpido al escribir); se ignoró.")
    else:
        _read_csv(path, b)

    if b.errors:
        shown = "\n  - ".join(b.errors[:10])
        more = f"\n  ... y {len(b.errors) - 10} más" if len(b.errors) > 10 else ""
        raise InvalidDataError(
            f"{path}: {len(b.errors)} punto(s) inválido(s) en métricas críticas; no se evalúa para no "
            f"decidir con datos corruptos:\n  - {shown}{more}"
        )
    samples = b.samples()
    if not samples:
        raise InvalidDataError(f"{path}: no hay peticiones HTTP (métrica {DURATION})")
    if b.optional_bad:
        notes.append(f"{b.optional_bad} valor(es) inválido(s) de {WAITING} se ignoraron "
                     "(solo afectan el diagnóstico, no la decisión).")
    if b.second_precision:
        notes.append("El CSV de k6 trae el tiempo en segundos: la ventana de calentamiento tiene "
                     "precisión de 1 s.")
    if all(s.latency_ms is None for s in samples):
        notes.append(f"Sin métrica {WAITING}: no se puede diagnosticar el tiempo al primer byte.")

    out_of_order = sum(1 for a, c in zip(samples, samples[1:]) if c.timestamp_ms < a.timestamp_ms)
    if out_of_order:
        samples.sort(key=lambda s: s.timestamp_ms)
    return LoadTestResult(source=str(path), samples=samples, data_notes=notes)
