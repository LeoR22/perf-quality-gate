"""Genera la tabla de resultados del README a partir de los CSV del kit, con la propia herramienta.

Así cada cifra del README se puede rastrear a un archivo de entrada y a un comando, y no se
copia a mano. El CI corre `--check` y falla si el README quedó desactualizado.

Uso:
  python scripts/readme_results.py          # reescribe el bloque entre los marcadores del README
  python scripts/readme_results.py --check  # exit 1 si el README no coincide con lo calculado
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from perf_gate.compare import Status, evaluate  # noqa: E402
from perf_gate.config import load_config  # noqa: E402
from perf_gate.readers import load_results  # noqa: E402
from perf_gate.stats import compute_run_stats  # noqa: E402

DATA = ROOT / "examples" / "resultados"
SINT = ROOT / "examples" / "escenarios-sinteticos"
README = ROOT / "README.md"
START, END = "<!-- resultados:inicio -->", "<!-- resultados:fin -->"
ICON = {Status.PASS: "✅", Status.WARN: "⚠️", Status.FAIL: "❌", Status.SKIP: "⏭️"}


def _ms(v):
    return "—" if v is None else f"{v:.0f}"


def build() -> str:
    # Veredicto oficial con los límites POR DEFECTO de la herramienta: el reto no define SLA.
    cfg = load_config(None)
    w = cfg.global_rules.warmup_seconds
    prod = compute_run_stats(load_results(DATA / "produccion-actual.csv"), w)
    runs = {v: compute_run_stats(load_results(DATA / f"{v}.csv"), w) for v in ("version-a", "version-b")}
    results = {v: evaluate(prod, s, cfg, v) for v, s in runs.items()}

    labels = sorted(set(prod.endpoints) | {lbl for s in runs.values() for lbl in s.endpoints})
    out = [
        START,
        "<!-- Generado por scripts/readme_results.py: no editar a mano. -->",
        "",
        f"Límites por defecto de la herramienta (el reto no define SLA). Fase estable (sin los primeros "
        f"{w:.0f} s). Formato de cada celda: percentil en ms "
        "(cambio frente a producción) · tasa de error · muestras.",
        "",
        "| Endpoint | Producción | Versión A | Versión B |",
        "|---|---|---|---|",
    ]
    for lbl in labels:
        b = prod.endpoints.get(lbl)
        p = cfg.rules_for(lbl).percentile
        cells = [f"p{p} {_ms(b.pct(p))} · {b.error_rate_pct:.2f}% · n={b.count}" if b else "— (no existe)"]
        for v in ("version-a", "version-b"):
            e = next(x for x in results[v].endpoints if x.label == lbl)
            c = e.candidate
            if c is None:
                cells.append("— (no se probó)")
                continue
            chg = "sin línea base" if e.change_pct is None else f"{e.change_pct:+.0f}%"
            cells.append(f"{ICON[e.status]} p{p} {_ms(c.pct(p))} ({chg}) · {c.error_rate_pct:.2f}% · n={c.count}")
        out.append(f"| `{lbl}` | " + " | ".join(cells) + " |")

    def g(fmt, attr):
        return [fmt(getattr(s, attr)) for s in (prod, *runs.values())]

    def wu(fmt, attr):
        return [fmt(getattr(s.warmup, attr)) if s.warmup else "—" for s in (prod, *runs.values())]

    rows = [
        ("Throughput (fase estable)", g(lambda v: f"{v:.1f} req/s", "throughput_rps")),
        ("Tasa de error (fase estable)", g(lambda v: f"{v:.2f}%", "error_rate_pct")),
        ("Peticiones en fase estable / totales",
         [f"{s.steady_samples} / {s.total_samples}" for s in (prod, *runs.values())]),
        ("Tiempo hasta estabilizarse", g(lambda v: f"{_ms(v)} s", "stabilization_s")),
        ("p95 durante el calentamiento", wu(lambda v: f"{v:.0f} ms", "p95")),
    ]
    out += ["", "| Global | Producción | Versión A | Versión B |", "|---|---:|---:|---:|"]
    out += [f"| {name} | " + " | ".join(vals) + " |" for name, vals in rows]

    # Sin excluir el calentamiento (para mostrar por qué se excluye).
    full_prod = compute_run_stats(load_results(DATA / "produccion-actual.csv"), 0)
    full_b = compute_run_stats(load_results(DATA / "version-b.csv"), 0)
    saldo = "Consultar saldo, cuenta ahorros"
    out += [
        "",
        "**Veredicto de la herramienta:**",
        "",
        *(f"- `{v}` → **{r.status.value}** (exit {r.exit_code}): {_headline(r)}" for v, r in results.items()),
        "",
        f"Sin excluir el calentamiento, `{saldo}` en B tendría p90 de {full_b.endpoints[saldo].p90:.0f} ms "
        f"contra {full_prod.endpoints[saldo].p90:.0f} ms en producción: B *parecería* peor por su arranque.",
        "",
        "**Escenarios sintéticos** (no son del reto: se generan desde producción con "
        "`scripts/generar_escenarios.py` para demostrar las reglas con más casos):",
        "",
        "| Escenario | Qué cambia frente a producción | Veredicto |",
        "|---|---|---|",
        *_escenarios(prod, cfg, w),
        "",
        "Fuente: `examples/resultados/*.csv` y `examples/escenarios-sinteticos/*.csv`, con los límites por defecto. "
        "Para reproducirlo: `python scripts/readme_results.py`.",
        END,
    ]
    return "\n".join(out)


def _escenarios(prod, cfg, w) -> list[str]:
    cambios = {
        "sintetica-pasa": "Todos los endpoints +5 % de latencia",
        "sintetica-falla": "`GET /clientes` +35 % de latencia y errores en `Consultar saldo` (≈2 %)",
        "version-c": "10 % más rápida en todos los endpoints y sin errores",
        "version-d": "Latencia x2 y 5 % de errores en todo, 30 % menos throughput y arranque en frío",
    }
    filas = []
    for nombre, cambio in cambios.items():
        r = evaluate(prod, compute_run_stats(load_results(SINT / f"{nombre}.csv"), w), cfg, nombre)
        filas.append(f"| `{nombre}` | {cambio} | {ICON[r.status]} **{r.status.value}** (exit {r.exit_code}): "
                     f"{_headline(r)} |")
    return filas


def _headline(r) -> str:
    from perf_gate.report import headline
    return headline(r).split(": ", 1)[-1]


def main(argv: list[str]) -> int:
    text = README.read_text(encoding="utf-8")
    if START not in text or END not in text:
        print(f"README sin los marcadores {START} / {END}", file=sys.stderr)
        return 2
    before, rest = text.split(START, 1)
    _, after = rest.split(END, 1)
    new = before + build() + after
    if "--check" in argv:
        if new != text:
            print("README desactualizado: ejecutar `python scripts/readme_results.py`", file=sys.stderr)
            return 1
        print("README coincide con los resultados calculados.")
        return 0
    README.write_text(new, encoding="utf-8", newline="\n")
    print("README actualizado.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
