"""Lector k6. Los fixtures son un recorte de una ejecución REAL de k6 2.2.0
(perf-gate-demo: local/k6/script.js contra la API simulada)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from perf_gate.compare import evaluate
from perf_gate.config import load_config
from perf_gate.readers import detect_format, load_results
from perf_gate.readers.k6 import _label, parse_time_ms
from perf_gate.stats import compute_run_stats

FIX = Path(__file__).resolve().parent / "fixtures"
KIT = Path(__file__).resolve().parents[1] / "examples" / "resultados"
LABELS = {"GET /clientes", "Consultar saldo, cuenta ahorros", "POST /pagos", "POST /transferencias"}


@pytest.mark.parametrize("name", ["k6-2.2-out.json", "k6-2.2-out.csv"])
def test_reads_real_k6_output(name):
    path = FIX / name
    assert detect_format(path) == "k6"
    r = load_results(path)
    assert {s.label for s in r.samples} == LABELS          # k6 2.x copia name en url: no debe duplicar
    assert all(s.latency_ms is not None for s in r.samples)  # http_req_waiting unido a cada petición
    assert all(s.success for s in r.samples)
    assert [s.timestamp_ms for s in r.samples] == sorted(s.timestamp_ms for s in r.samples)


def test_csv_reports_second_precision():
    notes = load_results(FIX / "k6-2.2-out.csv").data_notes
    assert any("segundos" in n for n in notes)


def test_json_and_csv_agree():
    j = load_results(FIX / "k6-2.2-out.json").samples
    c = load_results(FIX / "k6-2.2-out.csv").samples
    # Los dos archivos son recortes de la misma ejecución: casi todas las peticiones coinciden.
    jv, cv = {round(s.elapsed_ms, 3) for s in j}, {round(s.elapsed_ms, 3) for s in c}
    assert len(jv & cv) >= 0.8 * min(len(jv), len(cv))


def _point(metric, t, value, **tags):
    return json.dumps({"type": "Point", "metric": metric, "data": {"time": t, "value": value, "tags": tags}})


def test_failed_requests_and_truncated_last_line(tmp_path):
    t1, t2 = "2026-01-01T10:00:00.5-05:00", "2026-01-01T10:00:01.123456789-05:00"
    tags_ok = {"name": "GET /x", "method": "GET", "status": "200", "expected_response": "true"}
    tags_ko = {"name": "GET /x", "method": "GET", "status": "500", "expected_response": "false"}
    lines = [_point("http_req_duration", t1, 100, **tags_ok), _point("http_req_failed", t1, 0, **tags_ok),
             _point("http_req_duration", t2, 300, **tags_ko), _point("http_req_failed", t2, 1, **tags_ko),
             '{"type":"Point","metric":"http_req_dur']  # k6 interrumpido a mitad de línea
    p = tmp_path / "out.json"
    p.write_text("\n".join(lines), encoding="utf-8")
    r = load_results(p)
    assert [s.success for s in r.samples] == [True, False]
    assert r.samples[0].timestamp_ms == parse_time_ms(t1) - 100  # k6 marca el fin: se resta la duración
    assert any("última línea estaba incompleta" in n for n in r.data_notes)


T = "2026-01-01T10:00:00Z"
OK_TAGS = {"name": "GET /x", "method": "GET", "status": "200"}


@pytest.mark.parametrize("lines,expected", [
    ([_point("http_req_duration", T, 100, **OK_TAGS), "{roto", _point("http_req_failed", T, 0, **OK_TAGS)],
     "línea 2: JSON inválido"),                                  # línea corrupta en el medio: no se tolera
    ([_point("http_req_duration", T, 100, **OK_TAGS), _point("http_req_failed", T, 2, **OK_TAGS)],
     "debe ser 0 o 1"),
    ([_point("http_req_duration", T, "abc", **OK_TAGS)], "valor inválido"),
    ([_point("http_req_duration", "ayer", 100, **OK_TAGS)], "timestamp inválido"),
])
def test_invalid_critical_k6_data_stops_with_location(tmp_path, lines, expected):
    from perf_gate.models import InvalidDataError
    p = tmp_path / "out.json"
    p.write_text("\n".join(lines), encoding="utf-8")
    with pytest.raises(InvalidDataError, match=expected):
        load_results(p)


def test_summary_export_gives_clear_error(tmp_path):
    p = tmp_path / "summary.json"
    p.write_text(json.dumps({"metrics": {"http_req_duration": {"avg": 1}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="resumen agregado"):
        load_results(p)


@pytest.mark.parametrize("raw,expected", [
    ("2026-01-01T00:00:00Z", 1767225600000),
    ("2026-01-01T00:00:00.123456789Z", 1767225600123),
    ("2025-12-31T19:00:00-05:00", 1767225600000),
    ("1767225600", 1767225600000),           # CSV: segundos
    ("1767225600123", 1767225600123),        # ms
    ("1767225600123456", 1767225600123),     # µs
])
def test_parse_time(raw, expected):
    assert parse_time_ms(raw) == expected


def test_label_from_full_url_when_no_name():
    assert _label({"method": "post", "url": "https://api.test/pagos?id=1"}) == "POST /pagos"
    assert _label({"name": "https://api.test/clientes", "method": "GET"}) == "GET /clientes"
    assert _label({"name": "POST /pagos", "url": "POST /pagos", "method": "POST"}) == "POST /pagos"


def test_jmeter_baseline_vs_k6_candidate_can_be_compared():
    """Producción medida con JMeter y versión nueva con k6: el modelo neutral lo permite."""
    cfg = load_config(None)
    base = compute_run_stats(load_results(KIT / "produccion-actual.csv"), 60)
    cand = compute_run_stats(load_results(FIX / "k6-2.2-out.json"), 0)
    result = evaluate(base, cand, cfg, "k6")
    labels = {e.label for e in result.endpoints if e.baseline and e.candidate}
    assert {"GET /clientes", "POST /pagos", "Consultar saldo, cuenta ahorros"} <= labels
