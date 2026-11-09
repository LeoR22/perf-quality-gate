"""Compara una versión candidata contra la línea base (producción) y emite un veredicto."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .config import GateConfig
from .stats import EndpointStats, RunStats


class Status(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"

    @property
    def icon(self) -> str:
        return {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌", "SKIP": "⏭️"}[self.value]


_ORDER = {Status.SKIP: 0, Status.PASS: 1, Status.WARN: 2, Status.FAIL: 3}


def worst(*statuses: Status) -> Status:
    return max(statuses, key=_ORDER.__getitem__, default=Status.PASS)


@dataclass
class Finding:
    status: Status
    scope: str            # endpoint o "global"
    message: str          # por qué
    recommendation: str = ""   # qué mejorar


@dataclass
class EndpointResult:
    label: str
    status: Status
    percentile: int
    baseline: EndpointStats | None
    candidate: EndpointStats | None
    findings: list[Finding] = field(default_factory=list)

    @property
    def change_pct(self) -> float | None:
        if not (self.baseline and self.candidate) or self.baseline.pct(self.percentile) == 0:
            return None
        b, c = self.baseline.pct(self.percentile), self.candidate.pct(self.percentile)
        return 100.0 * (c - b) / b


@dataclass
class GateResult:
    candidate_name: str
    status: Status
    exit_code: int
    baseline: RunStats
    candidate: RunStats
    endpoints: list[EndpointResult]
    global_findings: list[Finding]

    @property
    def findings(self) -> list[Finding]:
        out = list(self.global_findings)
        for e in self.endpoints:
            out.extend(e.findings)
        return out


def _policy_status(policy: str) -> Status | None:
    return {"fail": Status.FAIL, "warn": Status.WARN, "ignore": None}[policy]


def _latency_hint(label: str, b: EndpointStats, c: EndpointStats) -> str:
    """Usa TTFB (Latency) y bytes para orientar dónde está el problema."""
    hints = []
    if b.avg_latency and c.avg_latency:
        ttfb_ratio = c.avg_latency / b.avg_latency
        total_ratio = c.avg / b.avg if b.avg else 1
        if ttfb_ratio >= 0.8 * total_ratio:
            hints.append(
                f"el tiempo al primer byte también subió ({b.avg_latency:.0f} → {c.avg_latency:.0f} ms), "
                "así que la demora está en el procesamiento del servidor: perfilar el endpoint, revisar "
                "consultas a base de datos, llamadas a servicios externos, locks y pools de conexiones"
            )
        else:
            hints.append(
                "el tiempo al primer byte casi no cambió; la demora está en transferir la respuesta"
            )
    if b.avg_bytes and c.avg_bytes and c.avg_bytes > 1.3 * b.avg_bytes:
        hints.append(
            f"la respuesta pesa más ({b.avg_bytes:.0f} → {c.avg_bytes:.0f} bytes): revisar el tamaño del payload"
        )
    return f"Optimizar {label}: " + "; ".join(hints) + "." if hints else f"Perfilar {label} bajo carga."


def _compare_endpoint(label: str, b: EndpointStats | None, c: EndpointStats | None,
                      cfg: GateConfig) -> EndpointResult:
    r = cfg.rules_for(label)
    g = cfg.global_rules
    p = r.percentile
    res = EndpointResult(label, Status.PASS, p, b, c)

    def add(st: Status, msg: str, rec: str = "") -> None:
        if rec and any(f.recommendation == rec for f in res.findings):
            rec = ""  # no repetir la misma recomendación en el mismo endpoint
        res.findings.append(Finding(st, label, msg, rec))

    if r.ignore:
        res.status = Status.SKIP
        add(Status.SKIP, "Ignorado por configuración.")
        return res

    if c is None:  # existe en producción pero no en la versión nueva
        st = _policy_status(g.missing_endpoint_policy)
        if st:
            add(st, "Existe en producción pero no se probó en esta versión.",
                "Confirmar si el endpoint se eliminó a propósito o si falta en el plan de pruebas.")
        res.status = st or Status.SKIP
        return res

    # Desde aquí siempre hay datos de la versión candidata.
    cand_p = c.pct(p)
    if c.count < r.min_samples or (b is not None and b.count < r.min_samples):
        n = min(c.count, b.count) if b else c.count
        add(Status.WARN,
            f"Solo {n} muestras (mínimo {r.min_samples}); el resultado no es estadísticamente confiable "
            f"y no se usa para decidir (p{p} actual {cand_p:.0f} ms, máx {c.max:.0f} ms).",
            f"Aumentar las peticiones de {label} en el plan de carga para poder evaluarlo.")
        res.status = Status.WARN
        return res

    if b is None:  # endpoint nuevo: no hay con qué comparar
        st = _policy_status(g.new_endpoint_policy)
        if st and r.max_ms is None:  # con SLA definido, el SLA es el criterio
            add(st, f"Endpoint nuevo, sin línea base en producción (p{p} {cand_p:.0f} ms, "
                f"{c.error_rate_pct:.2f}% errores). Solo se validaron los límites absolutos.",
                f"Definir un SLA (max_ms) para {label} en la configuración.")
    else:
        base_p = b.pct(p)
        diff = cand_p - base_p
        pct = 100.0 * diff / base_p if base_p else 0.0
        if pct > r.max_increase_pct and diff > r.min_increase_ms:
            factor = cand_p / base_p if base_p else float("inf")
            add(Status.FAIL,
                f"p{p} de {cand_p:.0f} ms vs {base_p:.0f} ms en producción "
                f"(+{pct:.0f}%, {factor:.1f}x); límite +{r.max_increase_pct:.0f}%.",
                _latency_hint(label, b, c))

    if r.max_ms is not None and cand_p > r.max_ms:
        add(Status.FAIL, f"p{p} de {cand_p:.0f} ms supera el SLA absoluto de {r.max_ms:.0f} ms.",
            _latency_hint(label, b, c) if b else f"Perfilar {label} bajo carga.")

    if c.error_rate_pct > r.max_error_rate_pct or (
        b is not None and c.error_rate_pct - b.error_rate_pct > r.max_error_rate_increase_pp
    ):
        base_txt = f" vs {b.error_rate_pct:.2f}% en producción" if b else ""
        add(Status.FAIL,
            f"Tasa de error {c.error_rate_pct:.2f}% ({c.errors} de {c.count}){base_txt}; "
            f"límite {r.max_error_rate_pct:.2f}% y +{r.max_error_rate_increase_pp:.2f} pp.",
            f"Revisar en los logs los errores (HTTP 5xx) de {label}: bajo carga suelen indicar timeouts, "
            "pools agotados o excepciones no controladas.")

    res.status = worst(*(f.status for f in res.findings)) if res.findings else Status.PASS
    return res


def _global_checks(base: RunStats, cand: RunStats, cfg: GateConfig) -> list[Finding]:
    g = cfg.global_rules
    out: list[Finding] = []

    if base.throughput_rps:
        drop = 100.0 * (base.throughput_rps - cand.throughput_rps) / base.throughput_rps
        if drop > g.max_throughput_decrease_pct:
            out.append(Finding(Status.FAIL, "global",
                f"El throughput bajó {drop:.0f}% ({base.throughput_rps:.1f} → "
                f"{cand.throughput_rps:.1f} req/s); límite -{g.max_throughput_decrease_pct:.0f}%.",
                "Con los mismos usuarios se atienden menos peticiones: buscar el cuello de botella."))

    bs, cs = base.stabilization_s, cand.stabilization_s
    st = _policy_status(g.stabilization_policy)
    if st and bs is not None and cs is not None and cs - bs > g.max_stabilization_increase_s:
        out.append(Finding(st, "global",
            f"Tarda {cs:.0f}s en estabilizarse vs {bs:.0f}s en producción "
            f"(+{cs - bs:.0f}s). Ese arranque se excluyó del análisis.",
            "Revisar el arranque en frío: precalentar cachés, pools de conexiones y JIT. En producción "
            "afecta cada despliegue y cada nueva instancia que levante el autoescalado."))
    if cs is None or cs >= g.warmup_seconds:
        out.append(Finding(Status.WARN, "global",
            f"La versión no se estabilizó dentro de la ventana de calentamiento de {g.warmup_seconds:.0f}s; "
            "las métricas pueden incluir parte del arranque.",
            "Aumentar warmup_seconds o la duración de la prueba."))

    for run, who in ((base, "producción"), (cand, "versión candidata")):
        for note in run.data_notes:
            out.append(Finding(Status.PASS, "datos", f"[{who}] {note}"))
    return out


def evaluate(base: RunStats, cand: RunStats, cfg: GateConfig, candidate_name: str = "candidata") -> GateResult:
    labels = sorted(set(base.endpoints) | set(cand.endpoints))
    endpoints = [
        _compare_endpoint(lbl, base.endpoints.get(lbl), cand.endpoints.get(lbl), cfg) for lbl in labels
    ]
    global_findings = _global_checks(base, cand, cfg)
    status = worst(Status.PASS, *(e.status for e in endpoints), *(f.status for f in global_findings))
    failing = status == Status.FAIL or (status == Status.WARN and cfg.global_rules.fail_on_warnings)
    return GateResult(candidate_name, status, 1 if failing else 0, base, cand, endpoints, global_findings)
