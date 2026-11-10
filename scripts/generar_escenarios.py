"""Genera dos escenarios SINTÉTICOS a partir de los resultados reales de producción, para demostrar
que las reglas del gate funcionan con más casos que las versiones A y B del reto.

No son datos del reto: parten de `produccion-actual.csv` y aplican cambios controlados, con
semilla fija (son reproducibles: correr el script siempre da los mismos archivos).

  sintetica-pasa.csv   todos los endpoints +5 % de latencia (dentro del +20 % permitido)       → PASA
  sintetica-falla.csv  GET /clientes +35 % de latencia (supera el +20 %) y "Consultar saldo" con
                       errores inyectados al 2,5 % (≈2 %, supera el máximo absoluto de 1 %);
                       pagos igual que producción                                                 → NO PASA

Uso: python scripts/generar_escenarios.py
"""
from __future__ import annotations

import csv
import io
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "examples" / "resultados" / "produccion-actual.csv"
OUT = ROOT / "examples" / "escenarios-sinteticos"
SEED = 2026

ESCENARIOS = {
    "sintetica-pasa": {
        "latencia": {"*": 1.05},
        "errores_pct": {},
    },
    "sintetica-falla": {
        "latencia": {"GET /clientes": 1.35},
        "errores_pct": {"Consultar saldo, cuenta ahorros": 2.5},
    },
}


def _leer_produccion() -> tuple[list[str], list[dict]]:
    texto = SRC.read_text(encoding="utf-8-sig")
    lector = csv.DictReader(io.StringIO(texto), delimiter=";")  # producción viene con ';'
    return list(lector.fieldnames), list(lector)


def generar(nombre: str, reglas: dict, columnas: list[str], filas: list[dict]) -> Path:
    rnd = random.Random(f"{SEED}-{nombre}")
    salida = []
    for fila in filas:
        f = dict(fila)
        factor = reglas["latencia"].get(f["label"], reglas["latencia"].get("*", 1.0))
        if factor != 1.0:
            f["elapsed"] = str(round(int(f["elapsed"]) * factor))
            if f.get("Latency", "").isdigit():
                f["Latency"] = str(round(int(f["Latency"]) * factor))
        pct = reglas["errores_pct"].get(f["label"])
        if pct is not None:
            if rnd.random() * 100 < pct:
                f.update(success="false", responseCode="500", responseMessage="Internal Server Error")
            else:
                f.update(success="true", responseCode="200", responseMessage="OK")
        salida.append(f)
    OUT.mkdir(parents=True, exist_ok=True)
    destino = OUT / f"{nombre}.csv"
    with destino.open("w", encoding="utf-8", newline="") as fh:
        escritor = csv.DictWriter(fh, fieldnames=columnas, lineterminator="\n")  # formato JMeter con ','
        escritor.writeheader()
        escritor.writerows(salida)
    return destino


def main() -> None:
    columnas, filas = _leer_produccion()
    for nombre, reglas in ESCENARIOS.items():
        print("generado:", generar(nombre, reglas, columnas, filas).relative_to(ROOT))


if __name__ == "__main__":
    main()
