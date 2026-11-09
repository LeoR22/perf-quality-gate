"""Resumen opcional con IA (Groq, nivel gratuito) del resultado del gate.

La IA NO decide: el veredicto ya lo tomó el gate de forma determinista. Esto solo redacta, para el
equipo, una explicación y un plan de mejora a partir del report.json.

Garantías:
* Solo se envía el reporte agregado (endpoints, percentiles, tasas de error, hallazgos), nunca las
  peticiones individuales, URLs ni datos de clientes.
* Sin GROQ_API_KEY, o si la API falla, se omite con un aviso y termina en 0: nunca rompe el pipeline.
* Sin dependencias: usa la librería estándar (API compatible con OpenAI de Groq).

Uso: perf-gate-ai --report perf-gate-report/report.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API_URL = "https://api.groq.com/openai/v1/chat/completions"
# Primero el más capaz; si Groq lo retira o no está en el plan, se intenta el siguiente.
DEFAULT_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]

SYSTEM_PROMPT = """Eres un ingeniero de performance senior que explica a un equipo de desarrollo el resultado \
de un quality gate de pruebas de carga. Recibes el reporte en JSON.

Reglas:
- El veredicto (status) YA fue decidido por el gate. No lo cambies ni lo cuestiones; explícalo.
- Usa solo los datos del reporte. No inventes métricas, causas confirmadas ni endpoints.
- Escribe en español, claro y directo, para desarrolladores. Formato Markdown, máximo ~250 palabras.

Estructura exacta:
### 🤖 Resumen para el equipo
2-3 frases: qué pasó y por qué la versión pasa o no.
### Plan de mejora priorizado
Lista numerada (máximo 4), lo más importante primero. Cada punto: qué revisar y en qué endpoint, \
apoyado en la evidencia del reporte (por ejemplo, si el tiempo al primer byte subió, la demora está en el servidor).
### Cómo verificar
1-2 viñetas: qué medir en la próxima prueba para confirmar la mejora.

Si la versión pasa sin advertencias, dilo en una frase y omite el plan de mejora."""


class AIError(RuntimeError):
    """Fallo controlado: se reporta como aviso, nunca rompe el pipeline."""


def _ep(stats: dict | None) -> dict | None:
    if not stats:
        return None
    keys = ("count", "errors", "avg", "p50", "p95", "p99", "avg_latency", "avg_bytes")
    return {k: round(stats[k], 1) if isinstance(stats.get(k), float) else stats.get(k) for k in keys}


def compact_report(report: dict) -> dict:
    """Solo lo agregado: lo que necesita la explicación y nada más."""
    pick = ("throughput_rps", "error_rate_pct", "stabilization_s")
    return {
        "version": report.get("candidate"),
        "status": report.get("status"),
        "headline": report.get("headline"),
        "global": {k: report.get("candidate_run", {}).get(k) for k in pick},
        "baseline_global": {k: report.get("baseline", {}).get(k) for k in pick},
        "endpoints": [
            {
                "endpoint": e["label"], "status": e["status"], "percentile": e["percentile"],
                "change_pct": e["change_pct"],
                "baseline": _ep(e.get("baseline")), "candidate": _ep(e.get("candidate")),
                "findings": [f["message"] for f in e.get("findings", [])],
            }
            for e in report.get("endpoints", [])
        ],
        "global_findings": [f["message"] for f in report.get("global_findings", []) if f["scope"] != "datos"],
    }


def _post(payload: dict, api_key: str, timeout: float = 60) -> dict:
    req = urllib.request.Request(
        API_URL, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}",
                 "User-Agent": "perf-quality-gate"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def summarize(report: dict, api_key: str, models: list[str] | None = None, post=_post) -> str:
    """Devuelve el resumen en Markdown o lanza AIError con un motivo legible."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "Reporte del gate:\n```json\n"
         + json.dumps(compact_report(report), ensure_ascii=False, indent=1) + "\n```"},
    ]
    last_error = "sin modelos configurados"
    for model in models or DEFAULT_MODELS:
        try:
            data = post({"model": model, "messages": messages, "temperature": 0.2,
                         "max_completion_tokens": 2000}, api_key)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise AIError("GROQ_API_KEY inválida") from exc
            if exc.code == 429:
                raise AIError("límite del plan gratuito de Groq alcanzado; reintentar más tarde") from exc
            if exc.code in (400, 404):  # modelo retirado o no disponible en el plan: probar el siguiente
                last_error = f"modelo {model} no disponible ({exc.code})"
                continue
            raise AIError(f"Groq respondió {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise AIError(f"no se pudo conectar con Groq ({exc})") from exc

        choice = (data.get("choices") or [{}])[0]
        text = ((choice.get("message") or {}).get("content") or "").strip()
        if not text:
            last_error = f"respuesta vacía de {model} (finish_reason={choice.get('finish_reason')})"
            continue
        if choice.get("finish_reason") == "length":
            text += "\n\n_(Resumen truncado.)_"
        used = data.get("model", model)
        return text + f"\n\n<sub>Generado con IA ({used} vía Groq). El veredicto lo decide el gate, no la IA.</sub>"
    raise AIError(last_error)


def _notice(msg: str) -> None:
    print(f"::notice title=Resumen IA::{msg}" if os.getenv("GITHUB_ACTIONS") else f"Resumen IA: {msg}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="perf-gate-ai", description="Resumen opcional con IA del reporte")
    ap.add_argument("--report", required=True, help="report.json generado por perf-gate")
    ap.add_argument("--out", default=None, help="Archivo Markdown de salida (default: junto al reporte)")
    ap.add_argument("--model", action="append", default=None,
                    help="Modelo de Groq (repetible; se prueban en orden). Default: " + ", ".join(DEFAULT_MODELS))
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        _notice("sin GROQ_API_KEY: se omite (el gate funciona igual).")
        return 0

    report_path = Path(args.report)
    models = args.model or [m for m in os.getenv("PERF_GATE_AI_MODEL", "").split(",") if m.strip()] or None
    try:
        text = summarize(json.loads(report_path.read_text(encoding="utf-8")), api_key, models)
    except AIError as exc:
        _notice(f"{exc}. Se omite el resumen.")
        return 0

    out = Path(args.out) if args.out else report_path.with_name("ai-summary.md")
    out.write_text(text + "\n", encoding="utf-8")
    print(text)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write("\n" + text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
