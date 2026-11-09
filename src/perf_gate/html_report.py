"""Reporte visual HTML autocontenido (sin dependencias externas): endpoint por endpoint,
producción vs versión nueva, qué falló y qué mejorar. Soporta modo claro y oscuro."""
from __future__ import annotations

from html import escape

from .compare import EndpointResult, GateResult, Status
from .report import headline, warmup_rows

_STATUS = {
    Status.PASS: ("good", "✓", "Pasa"),
    Status.WARN: ("warning", "!", "Advertencia"),
    Status.FAIL: ("critical", "✕", "No pasa"),
    Status.SKIP: ("muted", "–", "Omitido"),
}

_CSS = """
:root{color-scheme:light;--surface:#fcfcfb;--panel:#ffffff;--border:#e4e3df;--grid:#ecebe7;
--text:#0b0b0b;--text2:#52514e;--muted:#7a7974;--s1:#2a78d6;--s2:#eb6834;
--good:#0ca30c;--warning:#b87800;--critical:#d03b3b;--good-bg:#e7f6e7;--warning-bg:#fdf3dc;--critical-bg:#fbe9e9}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--surface:#1a1a19;
--panel:#222220;--border:#383835;--grid:#2e2e2b;--text:#f0efec;--text2:#c3c2b7;--muted:#8f8e86;
--s1:#3987e5;--s2:#d95926;--good:#3fc23f;--warning:#fab219;--critical:#e66767;
--good-bg:#1d2e1d;--warning-bg:#33290f;--critical-bg:#3a1f1f}}
:root[data-theme="dark"]{color-scheme:dark;--surface:#1a1a19;--panel:#222220;--border:#383835;--grid:#2e2e2b;
--text:#f0efec;--text2:#c3c2b7;--muted:#8f8e86;--s1:#3987e5;--s2:#d95926;--good:#3fc23f;--warning:#fab219;
--critical:#e66767;--good-bg:#1d2e1d;--warning-bg:#33290f;--critical-bg:#3a1f1f}
*{box-sizing:border-box}
body{margin:0;background:var(--surface);color:var(--text);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1040px;margin:0 auto;padding:28px 16px 48px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 12px}
.sub{color:var(--text2);margin:0 0 20px}
.badge{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;font-weight:600;font-size:13px}
.badge.good{background:var(--good-bg);color:var(--good)}.badge.warning{background:var(--warning-bg);color:var(--warning)}
.badge.critical{background:var(--critical-bg);color:var(--critical)}.badge.muted{background:var(--grid);color:var(--muted)}
.hero{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:18px 20px;margin-bottom:16px}
.hero p{margin:10px 0 0;font-size:16px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.tile{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:14px 16px}
.tile .k{color:var(--text2);font-size:13px}.tile .v{font-size:22px;font-weight:650;font-variant-numeric:tabular-nums}
.tile .d{color:var(--muted);font-size:13px}
.card{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:16px;overflow:hidden}
.legend{display:flex;gap:18px;flex-wrap:wrap;color:var(--text2);font-size:13px;margin-bottom:8px}
.legend i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:6px;vertical-align:-1px}
svg{display:block;width:100%;height:auto}
svg text{fill:var(--text2);font-size:13px;font-variant-numeric:tabular-nums}
svg .lbl{fill:var(--text);font-size:14.5px;font-weight:600}
svg .val{fill:var(--text2);font-size:13px}
svg .grid{stroke:var(--grid);stroke-width:1}
svg .hit{fill:transparent;cursor:default}
svg .row:hover .hit{fill:var(--grid);opacity:.5}
.tip{position:fixed;pointer-events:none;background:var(--panel);color:var(--text);border:1px solid var(--border);
border-radius:8px;padding:8px 10px;font-size:13px;box-shadow:0 4px 16px rgba(0,0,0,.15);display:none;max-width:280px;z-index:9}
table{width:100%;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums}
th,td{padding:8px 10px;border-bottom:1px solid var(--border);text-align:right;white-space:nowrap}
th{color:var(--text2);font-weight:600;font-size:13px}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}
.scroll{overflow-x:auto}
ul.f{list-style:none;padding:0;margin:0;display:grid;gap:10px}
ul.f li{background:var(--panel);border:1px solid var(--border);border-left-width:4px;border-radius:8px;padding:10px 14px}
ul.f li.critical{border-left-color:var(--critical)}ul.f li.warning{border-left-color:var(--warning)}
ul.f .scope{font-weight:650}ul.f .rec{color:var(--text2);margin-top:4px}
details{margin-top:24px;color:var(--text2)}summary{cursor:pointer}
footer{margin-top:32px;color:var(--muted);font-size:12px}
"""

_JS = """
(()=>{const t=document.getElementById('tip');
document.querySelectorAll('svg .row').forEach(r=>{
r.addEventListener('mousemove',e=>{t.innerHTML=r.dataset.tip;t.style.display='block';
const x=Math.min(e.clientX+14,innerWidth-t.offsetWidth-8);t.style.left=x+'px';t.style.top=(e.clientY+14)+'px'});
r.addEventListener('mouseleave',()=>{t.style.display='none'})})})();
"""


def _fmt(v: float | None, unit: str = "") -> str:
    return "—" if v is None else f"{v:,.0f}{unit}".replace(",", ".")


def _badge(status: Status) -> str:
    cls, icon, label = _STATUS[status]
    return f'<span class="badge {cls}"><span aria-hidden="true">{icon}</span>{label}</span>'


def _chart(endpoints: list[EndpointResult]) -> str:
    rows = [e for e in endpoints if e.candidate or e.baseline]
    if not rows:
        return ""
    w, label_w, right_w = 960, 250, 150
    plot_w = w - label_w - right_w
    bar_h, gap, row_h = 14, 2, 52
    top = 24
    h = top + row_h * len(rows) + 8
    vmax = max(max((e.baseline.pct(e.percentile) if e.baseline else 0),
                   (e.candidate.pct(e.percentile) if e.candidate else 0)) for e in rows) or 1
    step = next(s for s in (50, 100, 200, 250, 500, 1000, 2000, 5000, 10000) if vmax / s <= 6)
    xmax = step * (int(vmax // step) + 1)
    sx = lambda v: label_w + plot_w * v / xmax  # noqa: E731

    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Tiempo de respuesta por endpoint, producción vs versión nueva">']
    for i in range(0, int(xmax) + 1, step):
        x = sx(i)
        out.append(f'<line class="grid" x1="{x:.1f}" x2="{x:.1f}" y1="{top - 6}" y2="{h - 8}"/>')
        out.append(f'<text x="{x:.1f}" y="{top - 10}" text-anchor="middle">{i} ms</text>')
    for n, e in enumerate(rows):
        y = top + n * row_h + 6
        p = e.percentile
        b = e.baseline.pct(p) if e.baseline else None
        c = e.candidate.pct(p) if e.candidate else None
        cls, icon, label = _STATUS[e.status]
        change = "sin línea base" if e.change_pct is None else f"{e.change_pct:+.0f}%"
        tip = (f"<b>{escape(e.label)}</b><br>p{p} producción: {_fmt(b, ' ms')}<br>p{p} nueva: {_fmt(c, ' ms')}"
               f"<br>Cambio: {change}<br>Estado: {label}")
        out.append(f'<g class="row" data-tip="{escape(tip, quote=True)}">')
        out.append(f'<rect class="hit" x="0" y="{y - 4}" width="{w}" height="{row_h - 4}" rx="6"/>')
        out.append(f'<text class="lbl" x="8" y="{y + bar_h}">{escape(e.label[:34])}</text>')
        out.append(f'<text x="8" y="{y + bar_h + 16}">p{p}</text>')
        for k, (v, color) in enumerate(((b, "var(--s1)"), (c, "var(--s2)"))):
            yy = y + k * (bar_h + gap)
            if v is None:
                out.append(f'<text class="val" x="{label_w + 4}" y="{yy + bar_h - 3}">sin datos</text>')
                continue
            bw = max(sx(v) - label_w, 2)
            # Extremo de dato redondeado, anclado a la línea base (esquinas izquierdas rectas).
            r = min(4, bw / 2)
            out.append(
                f'<path d="M{label_w},{yy} h{bw - r:.1f} a{r},{r} 0 0 1 {r},{r} v{bar_h - 2 * r} '
                f'a{r},{r} 0 0 1 -{r},{r} h-{bw - r:.1f} z" fill="{color}"/>')
            out.append(f'<text class="val" x="{label_w + bw + 6:.1f}" y="{yy + bar_h - 3}">{_fmt(v)}</text>')
        out.append(f'<text class="lbl" x="{w - right_w + 16}" y="{y + bar_h}" style="fill:var(--{cls})">'
                   f'{icon} {escape(change)}</text>')
        out.append(f'<text x="{w - right_w + 16}" y="{y + bar_h + 16}">{label}</text>')
        out.append("</g>")
    out.append("</svg>")
    return "".join(out)


def _table(endpoints: list[EndpointResult]) -> str:
    head = ("<tr><th>Estado</th><th>Endpoint</th><th>Métrica</th><th>Producción</th><th>Nueva</th>"
            "<th>Cambio</th><th>Errores prod</th><th>Errores nueva</th><th>Muestras</th></tr>")
    body = []
    for e in endpoints:
        b, c, p = e.baseline, e.candidate, e.percentile
        body.append(
            f"<tr><td>{_badge(e.status)}</td><td>{escape(e.label)}</td><td>p{p}</td>"
            f"<td>{_fmt(b and b.pct(p), ' ms')}</td><td>{_fmt(c and c.pct(p), ' ms')}</td>"
            f"<td>{'—' if e.change_pct is None else f'{e.change_pct:+.0f}%'}</td>"
            f"<td>{f'{b.error_rate_pct:.2f}%' if b else '—'}</td><td>{f'{c.error_rate_pct:.2f}%' if c else '—'}</td>"
            f"<td>{b.count if b else '—'} / {c.count if c else '—'}</td></tr>")
    return f'<div class="scroll"><table>{head}{"".join(body)}</table></div>'


def to_html(r: GateResult) -> str:
    probs = sorted((f for f in r.findings if f.status in (Status.FAIL, Status.WARN)),
                   key=lambda f: f.status != Status.FAIL)
    notes = [f for f in r.findings if f.scope == "datos"]
    b, c = r.baseline, r.candidate

    def tile(k: str, bv: str, cv: str) -> str:
        return f'<div class="tile"><div class="k">{k}</div><div class="v">{cv}</div><div class="d">producción: {bv}</div></div>'

    findings = "".join(
        f'<li class="{_STATUS[f.status][0]}"><div><span class="scope">{escape(f.scope)}</span> — '
        f'{escape(f.message)}</div>' + (f'<div class="rec">Qué mejorar: {escape(f.recommendation)}</div>'
                                         if f.recommendation else "") + "</li>"
        for f in probs)
    title = f"Performance gate · {escape(r.candidate_name)}"
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{_CSS}</style></head>
<body><main>
<h1>{title}</h1>
<p class="sub">Versión nueva frente a producción, en estado estable (sin calentamiento).</p>
<div class="hero">{_badge(r.status)}<p>{escape(headline(r))}</p></div>
<div class="tiles">
{tile("Throughput", f"{b.throughput_rps:.1f} req/s", f"{c.throughput_rps:.1f} req/s")}
{tile("Tasa de error", f"{b.error_rate_pct:.2f}%", f"{c.error_rate_pct:.2f}%")}
{tile("Tiempo hasta estabilizarse", _fmt(b.stabilization_s, " s"), _fmt(c.stabilization_s, " s"))}
{tile("Peticiones analizadas", f"{b.steady_samples} de {b.total_samples}", f"{c.steady_samples} de {c.total_samples}")}
</div>
<h2>Endpoint por endpoint</h2>
<div class="card">
<div class="legend"><span><i style="background:var(--s1)"></i>Producción</span>
<span><i style="background:var(--s2)"></i>Versión nueva ({escape(r.candidate_name)})</span></div>
{_chart(r.endpoints)}
</div>
<h2>Detalle</h2>
<div class="card">{_table(r.endpoints)}</div>
<h2>Arranque (calentamiento)</h2>
<div class="card"><p class="sub" style="margin:0 0 10px">Los primeros {b.warmup_s:.0f} s se excluyen de la
comparación por endpoint para medir el rendimiento estable. Se miden aparte porque los usuarios los sufren en cada
despliegue; su degradación se evalúa con <code>stabilization_policy: {escape(r.stabilization_policy)}</code>
(info | warn | fail).</p>
<div class="scroll"><table><tr><th>Arranque</th><th></th><th>Producción</th><th>Nueva</th></tr>
{"".join(f"<tr><td>{escape(label)}</td><td></td><td>{escape(bv)}</td><td>{escape(cv)}</td></tr>"
         for label, bv, cv in warmup_rows(r))}
</table></div></div>
{f'<h2>Por qué y qué debería mejorar el equipo</h2><ul class="f">{findings}</ul>' if findings else ''}
{('<details><summary>Calidad de datos (cómo se manejó lo raro)</summary><ul>'
  + ''.join(f'<li>{escape(f.message)}</li>' for f in notes) + '</ul></details>') if notes else ''}
<footer>Generado por Performance Quality Gate · github.com/LeoR22/perf-quality-gate</footer>
</main><div class="tip" id="tip" role="tooltip"></div><script>{_JS}</script></body></html>
"""
