"""Punto de entrada: python -m perf_gate --baseline prod.csv --candidate nueva.csv"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .compare import evaluate
from .config import load_config
from .html_report import to_html
from .readers import load_results
from .report import github_annotations, headline, to_json, to_markdown, to_text
from .stats import compute_run_stats


def _append(path: str | None, text: str) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def _utf8_console() -> None:
    """Consolas de Windows (cp1252) no imprimen ✅/❌: forzar UTF-8 y nunca caerse por eso."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


# Códigos de salida (contrato con el pipeline):
EXIT_PASS = 0    # PASS, o WARN sin fail_on_warnings
EXIT_FAIL = 1    # FAIL (o WARN con fail_on_warnings): la versión no pasa
EXIT_ERROR = 2   # error de ejecución: entrada, configuración o error interno. Nunca se reporta como PASS


def _error(kind: str, exc: BaseException) -> int:
    msg = str(exc) or exc.__class__.__name__
    if os.getenv("GITHUB_ACTIONS"):
        msg = msg.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        print(f"::error title=Perf gate: {kind}::{msg}", file=sys.stderr)
    else:
        print(f"ERROR ({kind}): {msg}", file=sys.stderr)
    return EXIT_ERROR


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    try:
        return _run(argv)
    except SystemExit:
        raise  # argparse ya imprime el uso y sale con 2
    except Exception as exc:  # noqa: BLE001 - cualquier fallo inesperado bloquea con exit 2, nunca PASS
        import traceback
        traceback.print_exc()
        return _error("error interno", exc)


def _run(argv: list[str] | None) -> int:
    ap = argparse.ArgumentParser(prog="perf-gate", description="Performance Quality Gate")
    ap.add_argument("--baseline", required=True, help="Resultados de producción (línea base)")
    ap.add_argument("--candidate", required=True, help="Resultados de la versión nueva")
    ap.add_argument("--config", default=None, help="Archivo YAML/JSON con los límites del equipo")
    ap.add_argument("--name", default=None, help="Nombre de la versión candidata en el reporte")
    ap.add_argument("--format", default="auto", help="auto | jmeter-csv | k6")
    ap.add_argument("--report-dir", default="perf-gate-report", help="Carpeta para report.md y report.json")
    ap.add_argument("--fail-on-warnings", action="store_true", help="Las advertencias también bloquean")
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except (OSError, ValueError, TypeError) as exc:
        return _error("configuración inválida", exc)
    if args.fail_on_warnings:
        from dataclasses import replace
        cfg = replace(cfg, global_rules=replace(cfg.global_rules, fail_on_warnings=True))
    try:
        warmup = cfg.global_rules.warmup_seconds
        base = compute_run_stats(load_results(args.baseline, args.format), warmup)
        cand = compute_run_stats(load_results(args.candidate, args.format), warmup)
    except (OSError, ValueError, NotImplementedError) as exc:
        return _error("datos de entrada inválidos", exc)

    name = args.name or Path(args.candidate).stem
    result = evaluate(base, cand, cfg, name)
    limits = {e.label: cfg.rules_for(e.label).max_increase_pct for e in result.endpoints}

    md = to_markdown(result, limits)
    out_dir = Path(args.report_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.md").write_text(md, encoding="utf-8")
    (out_dir / "report.json").write_text(to_json(result), encoding="utf-8")
    (out_dir / "report.html").write_text(to_html(result), encoding="utf-8")

    print(to_text(result))
    print(f"\nReportes en {out_dir}: report.html, report.md y report.json")

    if os.getenv("GITHUB_ACTIONS"):
        for line in github_annotations(result):
            print(line)
    _append(os.getenv("GITHUB_STEP_SUMMARY"), md)
    _append(os.getenv("GITHUB_OUTPUT"),
            f"result={result.status.value}\npassed={'true' if result.exit_code == 0 else 'false'}\n"
            f"headline={headline(result)}\nreport-json={out_dir / 'report.json'}\nname={name}")
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
