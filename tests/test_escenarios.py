"""Escenarios sintéticos: demuestran las reglas con casos distintos a las versiones A y B del reto."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from perf_gate.cli import main

ROOT = Path(__file__).resolve().parents[1]
PROD = ROOT / "examples" / "resultados" / "produccion-actual.csv"
SINT = ROOT / "examples" / "escenarios-sinteticos"


def _evaluar(tmp_path, nombre, config=None):
    args = ["--baseline", str(PROD), "--candidate", str(SINT / f"{nombre}.csv"),
            "--name", nombre, "--report-dir", str(tmp_path)]
    if config:
        args += ["--config", str(config)]
    code = main(args)
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    return code, {e["label"]: e for e in report["endpoints"]}


def test_sintetica_pasa_con_limites_por_defecto_y_del_equipo(tmp_path):
    for i, cfg in enumerate((None, ROOT / "perf-gate.yml")):
        code, eps = _evaluar(tmp_path / str(i), "sintetica-pasa", cfg)
        assert code == 0
        assert all(e["status"] != "FAIL" for e in eps.values())
        assert 4 <= eps["POST /pagos"]["change_pct"] <= 6        # +5 %: dentro de los límites


def test_sintetica_falla_por_reglas_distintas_a_la_version_a(tmp_path):
    code, eps = _evaluar(tmp_path, "sintetica-falla")
    assert code == 1
    clientes = eps["GET /clientes"]
    assert clientes["status"] == "FAIL" and clientes["change_pct"] > 20          # regla de latencia
    saldo = eps["Consultar saldo, cuenta ahorros"]
    assert saldo["status"] == "FAIL"
    assert any("máximo absoluto" in f["message"] for f in saldo["findings"])     # regla de errores
    assert eps["POST /pagos"]["status"] == "PASS"                               # pagos no cambió


def test_generador_es_reproducible(tmp_path):
    """Volver a generar los escenarios produce exactamente los mismos archivos versionados."""
    antes = {p.name: p.read_bytes() for p in SINT.glob("*.csv")}
    subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "generar_escenarios.py")], check=True,
                   capture_output=True)
    despues = {p.name: p.read_bytes() for p in SINT.glob("*.csv")}
    assert antes == despues and len(antes) == 2
