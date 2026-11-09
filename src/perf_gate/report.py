"""Salidas del veredicto: consola, Markdown (resumen de GitHub), JSON y anotaciones."""
from __future__ import annotations

import json
from dataclasses import asdict

from .compare import EndpointResult, Finding, GateResult, Status


def _ms(v: float | None) -> str:
    return "—" if v is None else f"{v:.0f}"


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:+.0f}%"


def _problems(r: GateResult) -> list[Finding]:
    return [f for f in r.findings if f.status in (Status.FAIL, Status.WARN)]


def headline(r: GateResult) -> str:
    fails = [f for f in r.findings if f.status == Status.FAIL]
    if r.status == Status.FAIL:
        first = fails[0]
        return (f"La versión '{r.candidate_name}' NO PASA: {first.scope} — {first.message}"
                + (f" (y {len(fails) - 1} problema(s) más)" if len(fails) > 1 else ""))
    if r.status == Status.WARN:
        return (f"La versión '{r.candidate_name}' PASA con {len(_problems(r))} advertencia(s): "
                "no es más lenta que producción, pero revisa las advertencias.")
    return f"La versión '{r.candidate_name}' PASA: no es más lenta que producción."


# ---------------------------------------------------------------- consola
def to_text(r: GateResult) -> str:
    lines = [
        "=" * 78,
        f"{r.status.icon}  PERFORMANCE QUALITY GATE — {r.status.value}",
        headline(r),
        "=" * 78,
        f"{'Endpoint':34} {'p':>3} {'Prod ms':>8} {'Nueva ms':>9} {'Cambio':>7} {'Err %':>11}  Estado",
    ]
    for e in r.endpoints:
        b, c = e.baseline, e.candidate
        err = f"{b.error_rate_pct:.1f}→{c.error_rate_pct:.1f}" if b and c else (
            f"→{c.error_rate_pct:.1f}" if c else "—")
        lines.append(
            f"{e.label[:34]:34} {e.percentile:>3} {_ms(b and b.pct(e.percentile)):>8} "
            f"{_ms(c and c.pct(e.percentile)):>9} {_pct(e.change_pct):>7} {err:>11}  {e.status.value}"
        )
    lines.append(
        f"\nThroughput (estado estable): {r.baseline.throughput_rps:.1f} → {r.candidate.throughput_rps:.1f} req/s"
        f" | Estabilización: {_ms(r.baseline.stabilization_s)}s → {_ms(r.candidate.stabilization_s)}s"
    )
    probs = _problems(r)
    if probs:
        lines.append("\nPor qué y qué mejorar:")
        for f in sorted(probs, key=lambda f: f.status != Status.FAIL):
            lines.append(f"  {f.status.value:4} [{f.scope}] {f.message}")
            if f.recommendation:
                lines.append(f"       → {f.recommendation}")
    notes = [f for f in r.findings if f.scope == "datos"]
    if notes:
        lines.append("\nCalidad de datos:")
        lines.extend(f"  - {f.message}" for f in notes)
    return "\n".join(lines)


# ---------------------------------------------------------------- markdown
def _bar(change: float | None, limit: float) -> str:
    """Barra de texto proporcional al cambio vs producción."""
    if change is None:
        return ""
    blocks = min(10, max(0, round(abs(change) / 10)))
    char = "🟥" if change > limit else ("🟩" if change <= 0 else "🟨")
    return char * max(1, blocks) if abs(change) >= 2.5 else "▫️"


def _row(e: EndpointResult, limit_by_label: dict[str, float]) -> str:
    b, c, p = e.baseline, e.candidate, e.percentile
    err = (f"{b.error_rate_pct:.2f}% → {c.error_rate_pct:.2f}%" if b and c
           else f"— → {c.error_rate_pct:.2f}%" if c else "—")
    n = f"{b.count if b else '—'} / {c.count if c else '—'}"
    return (f"| {e.status.icon} | `{e.label}` | p{p} | {_ms(b and b.pct(p))} | {_ms(c and c.pct(p))} | "
            f"**{_pct(e.change_pct)}** {_bar(e.change_pct, limit_by_label.get(e.label, 20))} | {err} | {n} |")


def to_markdown(r: GateResult, limits: dict[str, float] | None = None) -> str:
    limits = limits or {}
    title = {Status.FAIL: "NO PASA", Status.WARN: "PASA con advertencias", Status.PASS: "PASA"}.get(
        r.status, r.status.value)
    out = [
        f"## {r.status.icon} Performance Quality Gate — `{r.candidate_name}`: {title}",
        "",
        f"> {headline(r)}",
        "",
        "### Endpoint por endpoint (estado estable, sin calentamiento)",
        "",
        "| | Endpoint | Métrica | Producción ms | Nueva ms | Cambio | Errores | Muestras (prod/nueva) |",
        "|---|---|---|---:|---:|---|---|---:|",
        *(_row(e, limits) for e in r.endpoints),
        "",
        "| Global | Producción | Nueva |",
        "|---|---:|---:|",
        f"| Throughput (req/s) | {r.baseline.throughput_rps:.1f} | {r.candidate.throughput_rps:.1f} |",
        f"| Tasa de error | {r.baseline.error_rate_pct:.2f}% | {r.candidate.error_rate_pct:.2f}% |",
        f"| Tiempo hasta estabilizarse | {_ms(r.baseline.stabilization_s)} s | {_ms(r.candidate.stabilization_s)} s |",
        f"| Peticiones analizadas / totales | {r.baseline.steady_samples} / {r.baseline.total_samples} | "
        f"{r.candidate.steady_samples} / {r.candidate.total_samples} |",
        "",
    ]
    probs = sorted(_problems(r), key=lambda f: f.status != Status.FAIL)
    if probs:
        out += ["### Por qué y qué debería mejorar el equipo", ""]
        for f in probs:
            out.append(f"- {f.status.icon} **{f.scope}** — {f.message}")
            if f.recommendation:
                out.append(f"  - 💡 {f.recommendation}")
        out.append("")
    notes = [f for f in r.findings if f.scope == "datos"]
    if notes:
        out += ["<details><summary>Calidad de datos (cómo se manejó lo raro)</summary>", ""]
        out += [f"- {f.message}" for f in notes]
        out += ["", "</details>", ""]
    return "\n".join(out)


# ---------------------------------------------------------------- json
def to_json(r: GateResult) -> str:
    def ep(e: EndpointResult) -> dict:
        return {
            "label": e.label, "status": e.status.value, "percentile": e.percentile,
            "change_pct": e.change_pct,
            "baseline": asdict(e.baseline) if e.baseline else None,
            "candidate": asdict(e.candidate) if e.candidate else None,
            "findings": [_f(f) for f in e.findings],
        }

    def _f(f: Finding) -> dict:
        return {"status": f.status.value, "scope": f.scope, "message": f.message,
                "recommendation": f.recommendation}

    def run(s) -> dict:
        d = asdict(s)
        d.pop("endpoints")
        return d

    return json.dumps({
        "candidate": r.candidate_name, "status": r.status.value, "passed": r.exit_code == 0,
        "headline": headline(r), "baseline": run(r.baseline), "candidate_run": run(r.candidate),
        "global_findings": [_f(f) for f in r.global_findings],
        "endpoints": [ep(e) for e in r.endpoints],
        "rows": rows(r),
    }, ensure_ascii=False, indent=2)


def rows(r: GateResult) -> list[dict]:
    """Una fila plana por endpoint: fácil de graficar en Grafana, Excel o cualquier BI."""
    out = []
    for e in r.endpoints:
        b, c, p = e.baseline, e.candidate, e.percentile
        out.append({
            "version": r.candidate_name,
            "endpoint": e.label,
            "status": e.status.value,
            "percentile": p,
            "baseline_ms": round(b.pct(p), 1) if b else None,
            "candidate_ms": round(c.pct(p), 1) if c else None,
            "change_pct": None if e.change_pct is None else round(e.change_pct, 1),
            "baseline_error_pct": round(b.error_rate_pct, 2) if b else None,
            "candidate_error_pct": round(c.error_rate_pct, 2) if c else None,
            "baseline_samples": b.count if b else 0,
            "candidate_samples": c.count if c else 0,
            "reason": " | ".join(f.message for f in e.findings if f.status in (Status.FAIL, Status.WARN)),
        })
    return out


# ---------------------------------------------------------------- anotaciones GitHub
def _escape(s: str) -> str:
    return s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def github_annotations(r: GateResult) -> list[str]:
    out = []
    for f in sorted(_problems(r), key=lambda f: f.status != Status.FAIL):
        level = "error" if f.status == Status.FAIL else "warning"
        msg = f.message + (f" → {f.recommendation}" if f.recommendation else "")
        out.append(f"::{level} title={_escape('Perf gate: ' + f.scope).replace(',', '%2C').replace(':', '%3A')}::{_escape(msg)}")
    return out
