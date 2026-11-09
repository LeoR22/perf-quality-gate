# Performance Quality Gate

GitHub Action que compara los resultados de una prueba de carga de una **versión nueva** contra los de
**producción**. Dice si la versión **pasa o no**, **por qué** y **qué debería mejorar el equipo**, y deja el
pipeline **en rojo** cuando no pasa. Cada equipo ajusta sus límites, incluso uno distinto por endpoint.

| Pipeline | Qué hace |
|---|---|
| [`CI - Build & Test`](.github/workflows/ci.yml) | Construye la herramienta: tests unitarios (Python 3.10 y 3.12), smoke test de la action y release con tag `v1` |
| [`Performance Gate - Versiones A y B`](.github/workflows/performance-gate.yml) | Usa la herramienta: evalúa `version-a` y `version-b` contra producción |

**Dos repositorios:** este tiene la herramienta. [`perf-gate-demo`](https://github.com/LeoR22/perf-gate-demo)
es el repo de un equipo que la consume como `LeoR22/perf-quality-gate@v1` y solo despliega si el gate pasa.

---

## ¿Cuál versión reemplaza a producción?

### ✅ La versión **B** reemplaza a producción. La versión **A** no pasa.

Comparación en estado estable (sin el primer minuto de calentamiento), p95 en ms:

| Endpoint | Producción | Versión A | Versión B |
|---|---:|---:|---:|
| Consultar saldo, cuenta ahorros | 206 | 206 (+0 %) | 207 (+0 %) |
| GET /clientes | 162 | 162 (+0 %) | 162 (+0 %) |
| **POST /pagos** | 353 | **767 (+117 %, 2,2x)** ❌ | 353 (+0 %) ✅ |
| Errores en POST /pagos | 0,25 % | **3,33 %** ❌ | 0,08 % ✅ |
| POST /transferencias (nuevo) | — | 266 | 265 |
| Throughput total | 20,3 req/s | 20,1 req/s | 20,2 req/s |
| Tiempo hasta estabilizarse | 20 s | 20 s | **50 s** ⚠️ |

**Versión A — NO PASA.** El endpoint de pagos tarda más del doble que en producción (p95 767 ms vs
353 ms) y su tasa de error sube de 0,25 % a 3,33 % (39 HTTP 500). También subió el tiempo al primer
byte (239 → 518 ms), así que la demora está en el procesamiento del servidor y no en la red ni en el
tamaño de la respuesta. **Qué mejorar:** perfilar `POST /pagos` bajo carga (consultas a base de datos,
llamadas a servicios externos, locks, pools de conexiones) y revisar en los logs el origen de los 500.

**Versión B — PASA, con dos advertencias que el equipo debería atender:**
1. **Arranque en frío:** tarda unos 50 s en estabilizarse, contra 20 s en producción. Una vez estable,
   rinde igual que producción. Ese arranque afecta cada despliegue y cada instancia nueva del
   autoescalado. Se recomienda precalentar cachés, pools de conexiones y JIT.
2. **`GET /reportes`** tiene solo 4 peticiones en toda la prueba (una de ellas de 2600 ms). No alcanza
   para juzgarlo; hay que darle más peso en el plan de carga.

> ⚠️ **Por qué es importante excluir el calentamiento:** si se compara el archivo completo, B *parece* peor
> que producción (p90 de "Consultar saldo" 582 ms vs 207 ms), porque su primer minuto es muy lento. Ese
> mal resultado viene de su arranque, no de su rendimiento real, y por eso se reporta aparte como advertencia.

### Ejecuciones del pipeline

En cada job, la pestaña **Summary** muestra el reporte visual endpoint por endpoint.

| Dónde | Versión A | Versión B |
|---|---|---|
| Repo del equipo [`perf-gate-demo`](https://github.com/LeoR22/perf-gate-demo) (usa `@v1`) | [❌ gate en rojo, despliegue bloqueado](https://github.com/LeoR22/perf-gate-demo/actions/runs/42702445734/job/129450405621) | [✅ gate en verde, despliegue ejecutado](https://github.com/LeoR22/perf-gate-demo/actions/runs/42702445734/job/129450406297) |
| Este repo (usa `./`) | [❌ Gate version-a](https://github.com/LeoR22/perf-quality-gate/actions/runs/42701821223/job/129448200812) | [✅ Gate version-b](https://github.com/LeoR22/perf-quality-gate/actions/runs/42701821223/job/129448200456) |
| Build de la herramienta | [✅ CI - Build & Test](https://github.com/LeoR22/perf-quality-gate/actions/runs/42701821572) | [✅ Release v1.0.0](https://github.com/LeoR22/perf-quality-gate/releases/tag/v1.0.0) |

---

## Cómo usar la action

```yaml
# .github/workflows/performance.yml en el repo del equipo
jobs:
  performance:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      # ... aquí el equipo corre su prueba de carga (JMeter) y genera resultados/version-nueva.csv
      - uses: LeoR22/perf-quality-gate@v1
        with:
          baseline: resultados/produccion-actual.csv
          candidate: resultados/version-nueva.csv
          config: perf-gate.yml          # opcional
          name: mi-version               # opcional
          fail-on-warnings: "false"      # opcional
```

| Input | Obligatorio | Default | Descripción |
|---|---|---|---|
| `baseline` | sí | | Resultados de producción |
| `candidate` | sí | | Resultados de la versión nueva |
| `config` | no | (límites por defecto) | YAML/JSON con los límites del equipo |
| `name` | no | nombre del archivo | Nombre en el reporte |
| `format` | no | `auto` | `auto` (por contenido), `jmeter-csv` o `k6` (JSON o CSV) |
| `fail-on-warnings` | no | `false` | Las advertencias también bloquean |
| `report-dir` | no | `perf-gate-report` | Dónde quedan `report.md` y `report.json` |
| `upload-report` | no | `true` | Sube el reporte como artifact |

**Outputs:** `result` (`PASS`/`WARN`/`FAIL`), `passed` (`true`/`false`), `headline`, `report-json`.

**Qué produce en cada ejecución:**
- Exit code `1` (pipeline en rojo) si no pasa; `2` si los archivos o la configuración son inválidos.
- **Resumen visual** en la página de la ejecución (Job Summary): tabla endpoint por endpoint frente a
  producción, qué falló y qué mejorar.
- **Anotaciones** de error y advertencia, visibles en la lista de la ejecución.
- **`report.html`**: reporte visual autocontenido (sin dependencias externas, modo claro y oscuro) con
  gráfico de barras producción vs versión nueva por endpoint, detalle y recomendaciones. Se puede
  publicar en GitHub Pages (ver [perf-gate-demo](https://github.com/LeoR22/perf-gate-demo)).
- `report.json` y `report.md` como artifact. El JSON incluye `rows`: una fila plana por endpoint
  (versión, endpoint, estado, ms de producción y de la versión nueva, cambio, errores, motivo), lista
  para graficar en **Grafana**, Excel o cualquier herramienta de BI.

### Uso local (sin GitHub)

```bash
pip install -r requirements.txt
PYTHONPATH=src python -m perf_gate --baseline examples/resultados/produccion-actual.csv \
  --candidate examples/resultados/version-a.csv --config perf-gate.yml
```

---

## Configuración de límites

Todo es opcional. Ejemplo completo: [`perf-gate.yml`](perf-gate.yml).

```yaml
global:
  warmup_seconds: 60                # se descarta el arranque de la prueba
  max_throughput_decrease_pct: 15
  max_stabilization_increase_s: 20
  stabilization_policy: warn        # warn | fail | ignore
  new_endpoint_policy: warn         # endpoint sin línea base en producción
  missing_endpoint_policy: warn     # endpoint de producción que no se probó
  fail_on_warnings: false

defaults:                           # aplica a todos los endpoints
  percentile: 95
  max_increase_pct: 20
  min_increase_ms: 15
  max_error_rate_pct: 1.0
  max_error_rate_increase_pp: 0.5
  error_confidence_pct: 99         # el aumento de errores debe ser estadísticamente significativo
  min_samples: 30

endpoints:                          # nombre exacto o patrón glob ("POST /*")
  "POST /pagos":
    max_increase_pct: 10
    max_ms: 600                     # SLA absoluto
  "GET /reportes":
    ignore: true
```

Por endpoint se puede sobrescribir: `percentile`, `max_increase_pct`, `min_increase_ms`, `max_ms`,
`max_error_rate_pct`, `max_error_rate_increase_pp`, `error_confidence_pct`, `min_samples` e `ignore`. Primero se busca el nombre
exacto y luego el primer patrón glob que coincida. Las claves mal escritas se rechazan con un error claro,
para que un error de tipeo no desactive un límite sin que nadie se dé cuenta.

### Por qué estos límites por defecto

| Regla | Default | Razón |
|---|---|---|
| Comparar **p95**, no el promedio | p95 | El promedio esconde las colas; el p95 refleja lo que sufre el 5 % de usuarios más lentos |
| Máximo empeoramiento | +20 % | Margen para la variación normal entre corridas sin dejar pasar regresiones reales |
| Diferencia mínima | 15 ms | En endpoints muy rápidos, +30 % pueden ser 3 ms: eso es ruido, no regresión |
| Tasa de error | ≤ 1 % y ≤ +0,5 pp | Ser rápido no sirve si se falla más |
| Significancia del aumento de errores | 99 % | Prueba z de dos proporciones: 3 errores sueltos en 400 peticiones no bloquean; un salto real (0,25 % → 3,3 % en 1.170 peticiones) sí. Se descubrió corriendo la demo real con k6 |
| Muestras mínimas | 30 | Con menos, un percentil no es estadísticamente confiable |
| Calentamiento | 60 s | En las tres pruebas, los primeros 10 s son entre 2 y 3,6 veces más lentos; B tarda 50 s en estabilizarse |
| Throughput | −15 % | Con los mismos usuarios, atender menos peticiones indica un cuello de botella |

---

## Análisis de los datos: lo raro y cómo se manejó

| # | Hallazgo | Cómo se manejó |
|---|---|---|
| 1 | `produccion-actual.csv` usa **`;`** como separador; A y B usan **`,`** | El separador se detecta a partir del encabezado |
| 2 | La etiqueta `"Consultar saldo, cuenta ahorros"` tiene una coma adentro (entre comillas) | Se usa un parser CSV real que respeta comillas, no `split(',')` |
| 3 | **Calentamiento:** el primer minuto es más lento en las tres pruebas. Producción y A se estabilizan en unos 20 s; B en unos 50 s | Se excluyen los primeros `warmup_seconds`, se mide el tiempo hasta estabilizarse y se advierte si empeora |
| 4 | `GET /reportes` tiene solo 4 peticiones por prueba, todas al final del archivo y con el timestamp fuera de orden (parecen agregadas después) | Se ordena por timestamp. Con menos de `min_samples` muestras el endpoint no decide el resultado: se advierte |
| 5 | `POST /transferencias` no existe en producción | No tiene línea base: se valida contra límites absolutos (error, `max_ms`) y se advierte si no tiene SLA |
| 6 | A y B reparten la carga entre 5 endpoints y producción entre 4, por eso cada endpoint tiene menos peticiones | El throughput se compara a nivel global, no por endpoint |
| 7 | Duración real de 300 s, 50 hilos, `allThreads = 50` constante, sin duplicados ni valores negativos | Revisado: sin acciones. Las líneas inválidas se descartarían y se contarían en el reporte |

Todo esto aparece en la sección **"Calidad de datos"** de cada reporte.

---

## Diseño

```
src/perf_gate/
├── readers/          # Formato de entrada → modelo neutral (Sample)
│   ├── jmeter_csv.py #   JMeter CSV (detecta separador, comillas, orden, filas malas)
│   ├── k6.py         #   k6 --out json / --out csv (une duración, TTFB y fallos por petición)
│   └── __init__.py   #   Registro de lectores + detección de formato por contenido
├── models.py         # Sample / LoadTestResult: no dependen de la herramienta de carga
├── stats.py          # Estado estable, percentiles, throughput, tiempo de estabilización
├── config.py         # Límites globales, por defecto y por endpoint (YAML/JSON, validados)
├── compare.py        # Reglas → Findings (PASS/WARN/FAIL + por qué + qué mejorar)
├── report.py         # Consola, Markdown (Job Summary), JSON, anotaciones de GitHub
├── html_report.py    # Reporte visual HTML autocontenido
└── cli.py            # Punto de entrada y exit code
```

- **Lógica genérica:** no hay nada específico de este caso en el código; los endpoints y límites salen
  de los datos y de la configuración, así que sirve para cualquier equipo.
- **Sin dependencias pesadas:** Python estándar + PyYAML. No usa pandas, para que la action arranque rápido.
- **Recomendaciones con diagnóstico:** se usa `Latency` (tiempo al primer byte) frente a `elapsed`, y
  `bytes`, para indicar si la demora está en el servidor o en transferir la respuesta.

### Soporte de k6

El comparador y los reportes trabajan sobre `LoadTestResult`, que no depende de la herramienta. Agregar
k6 fue escribir [`readers/k6.py`](src/perf_gate/readers/k6.py) y registrarlo; no se tocó nada más.

```bash
k6 run --out json=resultados.json script.js   # o --out csv=resultados.csv
```

- **Etiqueta:** el tag `name` de cada petición (`tags: { name: 'POST /pagos' }`). Usar los mismos nombres
  que en JMeter permite comparar **producción medida con JMeter contra una versión medida con k6**.
  Sin `name`, se arma `MÉTODO /ruta` a partir de la URL.
- **Por petición:** `http_req_duration` (tiempo total), `http_req_waiting` (TTFB, como `Latency` de JMeter)
  y `http_req_failed` (error), unidos por timestamp y tags.
- **Validado con salida real de k6 2.2.0** ([fixtures](tests/fixtures)). En la prueba aparecieron dos
  detalles: k6 2.x copia `name` en el tag `url` y el CSV trae el tiempo en segundos (se avisa en el reporte).
- El resumen agregado (`--summary-export`) se rechaza con un error claro: no trae peticiones individuales.
- La demo completa con Docker (APIs, k6, gate y Grafana) está en
  [perf-gate-demo](https://github.com/LeoR22/perf-gate-demo#demo-local-con-docker-k6--grafana).

También se instala como paquete: `pip install https://github.com/LeoR22/perf-quality-gate/archive/refs/tags/v1.zip`
y queda el comando `perf-gate`.

### Alcance recortado (por tiempo)

- La línea base se pasa como archivo. En un pipeline real se podría descargar automáticamente desde el
  último artifact de producción.

---

## Desarrollo

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -v
```

Los tests cubren la lectura (separador `;`, comillas, desorden, filas inválidas), cada regla (latencia,
errores, ruido, calentamiento, endpoints nuevos o con pocas muestras, overrides y globs, SLA, config
inválida) y las tres pruebas reales del kit (A falla, B pasa).
