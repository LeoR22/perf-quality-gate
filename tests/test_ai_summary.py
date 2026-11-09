"""Resumen con IA (Groq): se prueba sin red, con una función `post` falsa. Nunca debe romper el pipeline."""
from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

from perf_gate import ai_summary
from perf_gate.cli import main as gate_main

DATA = Path(__file__).resolve().parents[1] / "examples" / "resultados"


@pytest.fixture()
def report_a(tmp_path) -> dict:
    gate_main(["--baseline", str(DATA / "produccion-actual.csv"), "--candidate", str(DATA / "version-a.csv"),
               "--name", "version-a", "--report-dir", str(tmp_path)])
    return json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))


def ok(text="### 🤖 Resumen para el equipo\nPagos tarda el doble.", model="openai/gpt-oss-120b"):
    return {"model": model, "choices": [{"message": {"content": text}, "finish_reason": "stop"}]}


def http_error(code):
    return urllib.error.HTTPError("u", code, "err", {}, io.BytesIO(b"{}"))


def test_sends_only_aggregated_data_and_marks_ai_text(report_a):
    calls = []

    def post(payload, key):
        calls.append((payload, key))
        return ok()

    text = ai_summary.summarize(report_a, "k", post=post)
    assert "Pagos tarda el doble" in text and "El veredicto lo decide el gate" in text
    payload, key = calls[0]
    assert key == "k" and payload["model"] == "openai/gpt-oss-120b"
    user = payload["messages"][1]["content"]
    assert '"status": "FAIL"' in user and "POST /pagos" in user
    assert "timestamp" not in user.lower()  # nada de peticiones individuales


def test_falls_back_to_next_model_when_one_is_retired(report_a):
    used = []

    def post(payload, key):
        used.append(payload["model"])
        if payload["model"] == "openai/gpt-oss-120b":
            raise http_error(404)
        return ok(model=payload["model"])

    text = ai_summary.summarize(report_a, "k", post=post)
    assert used == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"] and "gpt-oss-20b" in text


@pytest.mark.parametrize("code,msg", [(401, "inválida"), (429, "límite")])
def test_auth_and_rate_limit_errors_are_clear(report_a, code, msg):
    def post(payload, key):
        raise http_error(code)

    with pytest.raises(ai_summary.AIError, match=msg):
        ai_summary.summarize(report_a, "k", post=post)


def test_without_api_key_it_skips_with_exit_0(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    report = tmp_path / "report.json"
    report.write_text("{}", encoding="utf-8")
    assert ai_summary.main(["--report", str(report)]) == 0
    assert "se omite" in capsys.readouterr().out
    assert not (tmp_path / "ai-summary.md").exists()
