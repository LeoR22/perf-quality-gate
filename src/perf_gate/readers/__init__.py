"""Registro de lectores. Para soportar una herramienta nueva (p. ej. k6) basta con
agregar un lector que devuelva un LoadTestResult y registrarlo aquí: el comparador
y los reportes no cambian."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..models import LoadTestResult
from .jmeter_csv import looks_like_jmeter, read_jmeter_csv


def _read_k6(path: str | Path) -> LoadTestResult:
    raise NotImplementedError(
        "El formato k6 aún no está soportado. Ver README > 'Cómo agregar k6'."
    )


READERS: dict[str, Callable[[str | Path], LoadTestResult]] = {
    "jmeter-csv": read_jmeter_csv,
    "k6": _read_k6,
}


def detect_format(path: str | Path) -> str:
    p = Path(path)
    if looks_like_jmeter(p):
        return "jmeter-csv"
    if p.suffix.lower() == ".json":
        return "k6"
    raise ValueError(f"No se reconoce el formato de {p}. Usa format: jmeter-csv | k6")


def load_results(path: str | Path, fmt: str = "auto") -> LoadTestResult:
    if not Path(path).is_file():
        raise FileNotFoundError(f"No existe el archivo de resultados: {path}")
    fmt = detect_format(path) if fmt == "auto" else fmt
    if fmt not in READERS:
        raise ValueError(f"Formato desconocido '{fmt}'. Opciones: {', '.join(READERS)}")
    return READERS[fmt](path)
