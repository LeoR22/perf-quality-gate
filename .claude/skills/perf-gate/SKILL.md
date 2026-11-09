---
name: perf-gate
description: Ejecuta e interpreta el Performance Quality Gate (perf-quality-gate). Úsalo cuando el equipo pida correr el gate, entender por qué una versión no pasó a producción, investigar una regresión de performance, ajustar los límites de perf-gate.yml o documentar el defecto. Funciona con resultados de JMeter (CSV) y k6 (--out json / --out csv).
---

# Performance Quality Gate

El gate compara una prueba de carga de una **versión nueva** contra la de **producción** y decide
pasa / no pasa de forma **determinista**. Tu papel es ayudar al equipo a usarlo y a entender el resultado.
**Nunca reemplazas ni contradices el veredicto del gate.**

## 1. Correr el gate

```bash
pip install https://github.com/LeoR22/perf-quality-gate/archive/refs/tags/v1.zip
perf-gate --baseline <produccion.csv|json> --candidate <nueva.csv|json> \
  --config perf-gate.yml --name <version> --report-dir perf-gate-report
```

- Exit code: `0` pasa (PASS o WARN), `1` no pasa (FAIL), `2` entrada inválida (archivo o config).
- Salidas en `perf-gate-report/`: `report.json` (fuente de verdad), `report.md` y `report.html`.
- El formato se detecta por contenido. Para k6, las peticiones deben llevar `tags: { name: '...' }`
  con las mismas etiquetas que use producción.

## 2. Explicar el resultado

Lee `report.json` y no el texto de consola:

1. `status` y `headline`: el veredicto. Cítalo tal cual.
2. `endpoints[]`: para cada `FAIL`, compara `baseline` contra `candidate` (`p95`, `error_rate`, `count`) y
   lee `findings[].message` (por qué) y `findings[].recommendation` (qué mejorar).
3. `global_findings[]`: throughput, tiempo de estabilización y notas de calidad de datos (`scope: "datos"`).

Diagnóstico con la evidencia del reporte:

| Señal en el reporte | Interpretación |
|---|---|
| `avg_latency` (TTFB) sube en la misma proporción que `avg` | La demora está en el servidor: base de datos, servicios externos, locks, pools |
| `avg` sube pero `avg_latency` no | La demora está en transferir la respuesta: tamaño del payload (`avg_bytes`) |
| Sube la tasa de error con "aumento significativo al 99%" | Errores reales bajo carga: timeouts, pools agotados, excepciones |
| "Tarda Xs en estabilizarse" | Arranque en frío: cachés, JIT, conexiones. Afecta cada despliegue y el autoescalado |
| "Solo N muestras" | No se puede juzgar: hay que darle más peso en el plan de carga, no relajar el límite |
| Throughput más bajo con los mismos usuarios | Hay un cuello de botella: el sistema atiende menos peticiones |

Reporta así: **veredicto → endpoint y evidencia → hipótesis de causa → qué revisar primero**.
Distingue siempre lo que el reporte demuestra de lo que es una hipótesis.

## 3. Ajustar límites (perf-gate.yml)

Claves por endpoint (nombre exacto o patrón glob como `"POST /*"`): `percentile`, `max_increase_pct`,
`min_increase_ms`, `max_ms` (SLA), `max_error_rate_pct`, `max_error_rate_increase_pp`,
`error_confidence_pct`, `min_samples`, `ignore`. Claves globales: `warmup_seconds`,
`max_throughput_decrease_pct`, `max_stabilization_increase_s`, `stabilization_policy`,
`new_endpoint_policy`, `missing_endpoint_policy`, `fail_on_warnings`.

Reglas:
- **No relajes un límite para que una versión pase.** Proponlo solo si el equipo da una razón de negocio
  (por ejemplo, un SLA acordado o un endpoint batch) y deja esa razón como comentario en el YAML.
- Un endpoint nuevo sin línea base se valida con `max_ms`. Propón un SLA en vez de ignorarlo.
- Las claves mal escritas hacen fallar la config (exit 2). Valida corriendo el gate otra vez.

## 4. Documentar el defecto

Si el gate no pasa y el equipo pide el ticket, redacta:

```markdown
**Título:** [perf] <endpoint> <síntoma> en <versión> (p95 X ms vs Y ms en producción)
**Severidad:** Alta si bloquea el despliegue o afecta pagos/transferencias; media en otro caso
**Evidencia:** tabla del endpoint (producción vs nueva: p95, errores, muestras) + link a la ejecución
**Hipótesis:** según la tabla de diagnóstico (marcar como hipótesis)
**Criterio de aceptación:** el gate pasa con la misma configuración
```

En GitHub Actions, la action lo hace sola con `create-issue: true`: abre el issue, lo actualiza en cada
ejecución y lo cierra cuando la versión pasa.

## 5. Demo local

Con el repo [perf-gate-demo](https://github.com/LeoR22/perf-gate-demo), `docker compose up` levanta las
APIs simuladas, k6, el gate y Grafana en http://localhost:3000.
