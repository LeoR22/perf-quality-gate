from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from perf_gate.cli import main
from perf_gate.compare import Status, evaluate
from perf_gate.config import load_config
from perf_gate.readers import load_results
from perf_gate.stats import compute_run_stats, percentile

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "examples" / "resultados"
HEADER = "timeStamp,elapsed,label,responseCode,responseMessage,threadName,success,bytes,Latency,allThreads"


def write_csv(path: Path, endpoints: dict[str, tuple[int, float]], seconds: int = 300,
              delimiter: str = ",", warmup_factor: float = 1.0, warmup_s: int = 0, seed: int = 1) -> Path:
    """endpoints: label -> (ms promedio, % de error). ~20 peticiones/s repartidas."""
    rnd = random.Random(seed)
    labels = list(endpoints)
    lines = [HEADER.replace(",", delimiter)]
    t0 = 1_762_500_000_000
    for i in range(seconds * 20):
        ts = t0 + i * 50
        label = labels[i % len(labels)]
        avg, err = endpoints[label]
        f = warmup_factor if (ts - t0) < warmup_s * 1000 else 1.0
        elapsed = int(avg * f * rnd.uniform(0.9, 1.1))
        ok = rnd.random() * 100 >= err
        quoted = f'"{label}"' if delimiter in label else label
        row = [str(ts), str(elapsed), quoted, "200" if ok else "500", "OK", "Usuarios 1-1",
               "true" if ok else "false", "1000", str(int(elapsed * 0.8)), "50"]
        lines.append(delimiter.join(row))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run(base: Path, cand: Path, cfg_path: Path | None = None):
    cfg = load_config(cfg_path)
    w = cfg.global_rules.warmup_seconds
    b = compute_run_stats(load_results(base), w)
    c = compute_run_stats(load_results(cand), w)
    return evaluate(b, c, cfg, "test")


# ------------------------------------------------------------------ lectura
def test_percentile_interpolates():
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([10], 95) == 10


def test_reads_semicolon_and_quoted_label_with_comma(tmp_path):
    p = write_csv(tmp_path / "p.csv", {"Consultar saldo, cuenta": (100, 0)}, delimiter=";", seconds=5)
    r = load_results(p)
    assert {s.label for s in r.samples} == {"Consultar saldo, cuenta"}
    assert any("Separador ';'" in n for n in r.data_notes)


def test_sorts_out_of_order_and_drops_malformed(tmp_path):
    p = write_csv(tmp_path / "p.csv", {"GET /x": (100, 0)}, seconds=5)
    with p.open("a", encoding="utf-8") as fh:
        fh.write("1762499999000,50,GET /x,200,OK,U,true,1,1,50\n")
        fh.write("no-es-un-numero,50,GET /x,200,OK,U,true,1,1,50\n")
    r = load_results(p)
    assert r.samples[0].timestamp_ms == 1762499999000
    assert any("fuera de orden" in n for n in r.data_notes)
    assert any("mal formadas" in n for n in r.data_notes)


def test_missing_columns_is_a_clear_error(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("timeStamp,elapsed\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="faltan columnas"):
        load_results(p)


# ------------------------------------------------------------------ reglas
def test_same_performance_passes(tmp_path):
    eps = {"GET /a": (100, 0), "POST /b": (300, 0)}
    r = run(write_csv(tmp_path / "b.csv", eps), write_csv(tmp_path / "c.csv", eps, seed=2))
    assert r.status == Status.PASS and r.exit_code == 0


def test_latency_regression_fails_with_reason(tmp_path):
    base = write_csv(tmp_path / "b.csv", {"GET /a": (100, 0), "POST /pagos": (300, 0)})
    cand = write_csv(tmp_path / "c.csv", {"GET /a": (100, 0), "POST /pagos": (600, 0)})
    r = run(base, cand)
    assert r.exit_code == 1
    pagos = next(e for e in r.endpoints if e.label == "POST /pagos")
    assert pagos.status == Status.FAIL
    assert "2.0x" in pagos.findings[0].message
    assert pagos.findings[0].recommendation


def test_error_rate_regression_fails(tmp_path):
    base = write_csv(tmp_path / "b.csv", {"GET /a": (100, 0)})
    cand = write_csv(tmp_path / "c.csv", {"GET /a": (100, 5)})
    r = run(base, cand)
    assert r.status == Status.FAIL
    assert "Tasa de error" in r.endpoints[0].findings[0].message


def test_small_noise_below_min_increase_ms_is_ignored(tmp_path):
    # 10 ms -> 13 ms es +30 % pero solo 3 ms: ruido, no regresión.
    base = write_csv(tmp_path / "b.csv", {"GET /a": (10, 0)})
    cand = write_csv(tmp_path / "c.csv", {"GET /a": (13, 0)})
    assert run(base, cand).status == Status.PASS


def test_warmup_is_excluded_but_reported(tmp_path):
    eps = {"GET /a": (100, 0)}
    base = write_csv(tmp_path / "b.csv", eps, warmup_factor=2, warmup_s=10)
    cand = write_csv(tmp_path / "c.csv", eps, warmup_factor=4, warmup_s=50)
    r = run(base, cand)
    assert r.endpoints[0].status == Status.PASS          # estado estable igual
    assert r.status == Status.WARN and r.exit_code == 0  # pero se advierte el arranque lento
    assert any("estabilizarse" in f.message for f in r.global_findings)


def test_new_and_sparse_endpoints_warn_not_fail(tmp_path):
    base = write_csv(tmp_path / "b.csv", {"GET /a": (100, 0)})
    cand = write_csv(tmp_path / "c.csv", {"GET /a": (100, 0), "POST /nuevo": (200, 0)})
    r = run(base, cand)
    nuevo = next(e for e in r.endpoints if e.label == "POST /nuevo")
    assert nuevo.status == Status.WARN and r.exit_code == 0


def test_per_endpoint_override_and_glob(tmp_path):
    base = write_csv(tmp_path / "b.csv", {"GET /a": (100, 0), "POST /pagos": (300, 0)})
    cand = write_csv(tmp_path / "c.csv", {"GET /a": (100, 0), "POST /pagos": (345, 0)})  # +15 %
    assert run(base, cand).status == Status.PASS          # límite por defecto: 20 %
    cfg = tmp_path / "cfg.yml"
    cfg.write_text('endpoints:\n  "POST /*":\n    max_increase_pct: 10\n', encoding="utf-8")
    assert run(base, cand, cfg).status == Status.FAIL     # equipo más estricto con POST


def test_absolute_sla(tmp_path):
    eps = {"GET /a": (500, 0)}
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"endpoints": {"GET /a": {"max_ms": 400}}}), encoding="utf-8")
    r = run(write_csv(tmp_path / "b.csv", eps), write_csv(tmp_path / "c.csv", eps), cfg)
    assert r.status == Status.FAIL


def test_unknown_config_key_is_rejected(tmp_path):
    cfg = tmp_path / "cfg.yml"
    cfg.write_text("defaults:\n  max_increse_pct: 10\n", encoding="utf-8")
    with pytest.raises(ValueError, match="desconocidas"):
        load_config(cfg)


# ------------------------------------------------------------------ datos reales del kit
def test_kit_version_a_fails_because_of_pagos(tmp_path):
    code = main(["--baseline", str(DATA / "produccion-actual.csv"), "--candidate",
                 str(DATA / "version-a.csv"), "--report-dir", str(tmp_path)])
    assert code == 1
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    pagos = next(e for e in report["endpoints"] if e["label"] == "POST /pagos")
    assert pagos["status"] == "FAIL" and pagos["change_pct"] > 100


def test_kit_version_b_passes(tmp_path):
    code = main(["--baseline", str(DATA / "produccion-actual.csv"), "--candidate",
                 str(DATA / "version-b.csv"), "--report-dir", str(tmp_path)])
    assert code == 0
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert all(e["status"] != "FAIL" for e in report["endpoints"])


def test_html_and_flat_rows_are_generated(tmp_path):
    main(["--baseline", str(DATA / "produccion-actual.csv"), "--candidate",
          str(DATA / "version-a.csv"), "--name", "version-a", "--report-dir", str(tmp_path)])
    html = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "<svg" in html and "POST /pagos" in html and "No pasa" in html
    assert 'src="http' not in html and 'href="http' not in html  # autocontenido: sin recursos externos
    rows = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))["rows"]
    pagos = next(r for r in rows if r["endpoint"] == "POST /pagos")
    assert pagos["version"] == "version-a" and pagos["status"] == "FAIL" and pagos["baseline_ms"] == 353.0


def test_kit_with_team_config(tmp_path):
    args = ["--baseline", str(DATA / "produccion-actual.csv"), "--config", str(ROOT / "perf-gate.yml"),
            "--report-dir", str(tmp_path)]
    assert main(args + ["--candidate", str(DATA / "version-a.csv")]) == 1
    assert main(args + ["--candidate", str(DATA / "version-b.csv")]) == 0


def test_bad_input_returns_exit_2(tmp_path):
    assert main(["--baseline", "no-existe.csv", "--candidate", "x.csv", "--report-dir", str(tmp_path)]) == 2
