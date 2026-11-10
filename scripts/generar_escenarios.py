"""Genera escenarios SINTÉTICOS a partir de los resultados reales de producción, para demostrar
que las reglas del gate funcionan con más casos que las versiones A y B del reto.

No son datos del reto: parten de `produccion-actual.csv` y aplican cambios controlados, con
semilla fija (son reproducibles: correr el script siempre da los mismos archivos).

  sintetica-pasa.csv   todos los endpoints +5 % de latencia (dentro del +20 % permitido)       → PASA
  sintetica-falla.csv  GET /clientes +35 % de latencia (supera el +20 %) y "Consultar saldo" con
                       errores inyectados al 2,5 % (≈2 %, supera el máximo absoluto de 1 %);
                       pagos igual que producción                                                 → NO PASA
  version-c.csv        10 % más rápida en todos los endpoints y sin errores                       → PASA
  version-d.csv        falla en todo: latencia x2 y 5 % de errores en todos los endpoints,
                       30 % menos throughput y arranque en frío de ~55 s                          → NO PASA

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
    "version-c": {                       # mejor que producción en todo
        "latencia": {"*": 0.90},
        "errores_pct": {"*": 0.0},
    },
    "version-d": {                       # activa todas las reglas a la vez
        "latencia": {"*": 2.0},
        "errores_pct": {"*": 5.0},
        "descartar_pct": 30,             # 30 % menos peticiones → cae el throughput
        "arranque_frio": (55, 4.0),      # (segundos, factor inicial) → tarda en estabilizarse
    },
}


def _leer_produccion() -> tuple[list[str], list[dict]]:
    texto = SRC.read_text(encoding="utf-8-sig")
    lector = csv.DictReader(io.StringIO(texto), delimiter=";")  # producción viene con ';'
    return list(lector.fieldnames), list(lector)


def generar(nombre: str, reglas: dict, columnas: list[str], filas: list[dict]) -> Path:
    rnd = random.Random(f"{SEED}-{nombre}")
    inicio = min(int(f["timeStamp"]) for f in filas)
    frio_s, frio_factor = reglas.get("arranque_frio", (0, 1.0))
    salida = []
    for fila in filas:
        if reglas.get("descartar_pct") and rnd.random() * 100 < reglas["descartar_pct"]:
            continue  # (solo consume aleatorios si aplica: los demás escenarios no cambian)
        f = dict(fila)
        factor = reglas["latencia"].get(f["label"], reglas["latencia"].get("*", 1.0))
        t = (int(f["timeStamp"]) - inicio) / 1000
        if t < frio_s:  # arranque en frío: más lento al principio, se recupera gradualmente
            factor *= 1 + (frio_factor - 1) * (1 - t / frio_s)
        if factor != 1.0:
            f["elapsed"] = str(round(int(f["elapsed"]) * factor))
            if f.get("Latency", "").isdigit():
                f["Latency"] = str(round(int(f["Latency"]) * factor))
        pct = reglas["errores_pct"].get(f["label"], reglas["errores_pct"].get("*"))
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
