# Performance Quality Gate

GitHub Action que compara los resultados de una prueba de carga de una **versión nueva** contra los de
**producción**. Dice si la versión **pasa o no**, **por qué** y **qué debería mejorar el equipo**, y deja el
pipeline **en rojo** cuando no pasa. Cada equipo ajusta sus límites, incluso uno distinto por endpoint.

> 📘 **[Manual paso a paso con capturas](docs/manual/MANUAL.md)**: cómo funciona, cómo leer los resultados,
> cómo usarla en un pipeline y cómo ejecutarla en tu computador.

| Pipeline | Qué hace |
|---|---|
| [`CI - Build & Test`](.github/workflows/ci.yml) | Construye la herramienta: tests (Python 3.10 y 3.12), verificación del README, integración del consumidor y release inmutable `vX.Y.Z` |
| [`Integración del consumidor`](.github/workflows/integration.yml) | Ejecuta la action como un equipo y verifica el contrato: A da FAIL, B pasa y un dato inválido bloquea con error |
| [`Promover tag mayor`](.github/workflows/promote.yml) | **El único paso manual**: vuelve a validar una release y solo entonces mueve `v1` |
| [`Performance Gate - Versiones A y B`](.github/workflows/performance-gate.yml) | **Automático** en cada push y PR: evalúa A y B con la action de este commit. A en rojo (bloquea) y B en verde |

**Dos repositorios:** este tiene la herramienta. [`perf-gate-demo`](https://github.com/LeoR22/perf-gate-demo)
es el repo de un equipo que la consume como `LeoR22/perf-quality-gate@v1` y solo despliega si el gate pasa.

---

## ¿Cuál versión reemplaza a producción?

### ✅ La versión **B** reemplaza a producción. La versión **A** no pasa.

<!-- resultados:inicio -->
<!-- Generado por scripts/readme_results.py: no editar a mano. -->

Fase estable (sin los primeros 60 s). Formato de cada celda: percentil en ms (cambio frente a producción) · tasa de error · muestras.

| Endpoint | Producción | Versión A | Versión B |
|---|---|---|---|
| `Consultar saldo, cuenta ahorros` | p95 206 · 0.43% · n=1641 | ✅ p95 206 (+0%) · 0.25% · n=1196 | ✅ p95 207 (+0%) · 0.25% · n=1196 |
| `GET /clientes` | p95 162 · 0.00% · n=1652 | ✅ p95 162 (+0%) · 0.24% · n=1258 | ✅ p95 162 (+0%) · 0.08% · n=1178 |
| `GET /reportes` | p90 818 · 0.00% · n=3 | ⚠️ p90 813 (-1%) · 0.00% · n=3 | ⚠️ p90 2242 (+174%) · 0.00% · n=3 |
| `POST /pagos` | p95 353 · 0.25% · n=1573 | ❌ p95 767 (+117%) · 3.33% · n=1170 | ✅ p95 353 (+0%) · 0.08% · n=1257 |
| `POST /transferencias` | — (no existe) | ✅ p95 266 (sin línea base) · 0.25% · n=1201 | ✅ p95 265 (sin línea base) · 0.50% · n=1211 |

| Global | Producción | Versión A | Versión B |
|---|---:|---:|---:|
| Throughput (fase estable) | 20.3 req/s | 20.1 req/s | 20.2 req/s |
| Tasa de error (fase estable) | 0.23% | 0.99% | 0.23% |
| Peticiones analizadas / totales | 4869 / 6083 | 4828 / 6047 | 4845 / 6059 |
| Tiempo hasta estabilizarse | 20 s | 20 s | 50 s |
| p95 durante el calentamiento | 659 ms | 1343 ms | 1166 ms |

**Veredicto de la herramienta:**

- `version-a` → **FAIL** (exit 1): POST /pagos — p95 de 767 ms vs 353 ms en producción (+117%, 2.2x); límite +10%. (y 2 problema(s) más)
- `version-b` → **WARN** (exit 0): no es más lenta que producción, pero revisa las advertencias.

Sin excluir el calentamiento, `Consultar saldo, cuenta ahorros` en B tendría p90 de 582 ms contra 207 ms en producción: B *parecería* peor por su arranque.

Fuente: `examples/resultados/*.csv` con `perf-gate.yml`. Para reproducirlo: `python scripts/readme_results.py`.
<!-- resultados:fin -->

**Versión A: NO PASA.** `POST /pagos` tarda más del doble que en producción y su tasa de error sube de
forma estadísticamente significativa. El tiempo al primer byte sube en la misma proporción, así que la
demora está en el procesamiento del servidor, no en la red ni en el tamaño de la respuesta. **Qué mejorar:**
perfilar `POST /pagos` bajo carga (base de datos, servicios externos, locks, pools de conexiones) y revisar
en los logs el origen de los HTTP 500.

**Versión B: PASA, con advertencias que el equipo debería atender:**
1. **Arranque en frío:** tarda más que producción en estabilizarse y su p95 durante el calentamiento es
   claramente mayor. Una vez estable, rinde igual que producción. Ese arranque no entra en la comparación
   por endpoint, pero sigue medido y visible porque afecta cada despliegue y cada instancia nueva del
   autoescalado. Si el servicio escala seguido o tiene un SLA estricto, se configura
   `stabilization_policy: fail` y el cold start bloquea el despliegue.
2. **`GET /reportes`** tiene 4 peticiones por prueba: una cae dentro del calentamiento y 3 en la fase
   estable (las `n=3` de la tabla). No alcanza para juzgarlo, así que no decide el resultado. Hay que darle
   más peso en el plan de carga.

**Limitación:** hay una sola ejecución por versión. La herramienta compensa parte de la variabilidad
(percentiles, ruido mínimo de 15 ms, significancia estadística en errores), pero para una decisión definitiva
conviene repetir cada prueba al menos 3 veces y confirmar que el resultado se mantiene.

### Ejecuciones del pipeline

En cada job, la pestaña **Summary** muestra el reporte visual endpoint por endpoint.

| Dónde | Versión A | Versión B |
|---|---|---|
| Repo del equipo [`perf-gate-demo`](https://github.com/LeoR22/perf-gate-demo) (usa `@v1` = v1.6.0) | [❌ gate en rojo, despliegue bloqueado](https://github.com/LeoR22/perf-gate-demo/actions/runs/42729780645/job/129544585059) | [✅ gate en verde, despliegue ejecutado](https://github.com/LeoR22/perf-gate-demo/actions/runs/42729780645/job/129544585407) |
| Prueba de carga real con k6 (APIs simuladas) | [❌ k6 version-a](https://github.com/LeoR22/perf-gate-demo/actions/runs/42729780485/job/129544581394) | [✅ k6 version-b](https://github.com/LeoR22/perf-gate-demo/actions/runs/42729780485/job/129544581080) |
| Build y contrato de la herramienta | [✅ CI + integración del consumidor](https://github.com/LeoR22/perf-quality-gate/actions/runs/42728585540) | [✅ Release v1.6.0](https://github.com/LeoR22/perf-quality-gate/releases/tag/v1.6.0) · [✅ promoción de v1](https://github.com/LeoR22/perf-quality-gate/actions/runs/42728987324) |

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
| `create-issue` | no | `false` | Documenta el defecto en GitHub Issues (requiere `permissions: issues: write`) |
| `github-token` | no | `github.token` | Token para gestionar los issues |

**Outputs:** `result` (`PASS`/`WARN`/`FAIL`), `passed` (`true`/`false`), `headline`, `report-json`, `name`.

### Versiones de la action

| Referencia | Qué recibe el equipo | Cuándo usarla |
|---|---|---|
| `@v1.6.0` | Exactamente esa release. **Las releases son inmutables**: el CI se niega a publicar dos veces el mismo tag | Pipelines que necesitan reproducibilidad |
| `@<sha del commit>` | Exactamente ese código, aunque alguien mueva un tag | Máxima reproducibilidad y seguridad de la cadena de suministro |
| `@v1` | La última release `v1.x.y` **promovida** | Recibir correcciones compatibles sin tocar el pipeline |

`v1` no se mueve solo al publicar una versión. Se mueve con el workflow manual **Promover tag mayor**,
que vuelve a correr la integración del consumidor sobre esa release (A da FAIL, B pasa, un dato inválido
bloquea) antes de moverlo. Se recomienda además proteger los tags `v*.*.*` con un *ruleset* del
repositorio para que no se puedan reescribir.

### Estados y códigos de salida

| Estado | Significado | Exit | ¿Bloquea el pipeline? |
|---|---|---:|---|
| `PASS` | Cumple todas las reglas | 0 | No |
| `WARN` | Hay advertencias no bloqueantes (endpoint nuevo, pocas muestras, arranque más lento, aumento de errores no significativo) | 0 | No, salvo con `fail_on_warnings: true` o `fail-on-warnings: "true"`, que devuelve 1 |
| `FAIL` | Una o más reglas obligatorias no se cumplen | 1 | **Sí** |
| Error de ejecución | Datos inválidos (por ejemplo `success` distinto de true/false), configuración inválida (YAML roto, tipo o clave incorrecta) o error interno | 2 | **Sí**, y nunca se reporta como PASS: no se genera el reporte |

Los cuatro casos están cubiertos por tests (`test_exit_code_contract`, `test_invalid_config_is_exit_2_never_pass`,
`test_invalid_data_never_ends_in_pass`, `test_unexpected_internal_error_is_exit_2`).

**Qué produce en cada ejecución:**
- **Resumen visual** en la página de la ejecución (Job Summary): tabla endpoint por endpoint frente a
  producción, qué falló y qué mejorar.
- **Anotaciones** de error y advertencia, visibles en la lista de la ejecución.
- **`report.html`**: reporte visual autocontenido (sin dependencias externas, modo claro y oscuro) con
  gráfico de barras producción vs versión nueva por endpoint, detalle y recomendaciones. Se puede
  publicar en GitHub Pages (ver [perf-gate-demo](https://github.com/LeoR22/perf-gate-demo)).
- `report.json` y `report.md` como artifact. El JSON incluye `rows`: una fila plana por endpoint
  (versión, endpoint, estado, ms de producción y de la versión nueva, cambio, errores, motivo), lista
  para graficar en Excel o cualquier herramienta de BI.

### Defecto automático (opcional)

Con `create-issue: "true"`, cuando una versión no pasa el gate abre el issue `[perf-gate] <versión> no pasa`
con el reporte y el link a la ejecución. En las siguientes ejecuciones lo comenta, sin duplicarlo, y lo
**cierra solo** cuando la versión pasa. Corre aunque el gate falle y nunca cambia su resultado.

```yaml
permissions:
  contents: read
  issues: write                     # para create-issue
steps:
  - uses: LeoR22/perf-quality-gate@v1
    with:
      baseline: resultados/produccion.csv
      candidate: resultados/version-nueva.csv
      create-issue: "true"
```

**¿Por qué no hay IA?** Se evaluó y se descartó. Un quality gate tiene que ser determinista, reproducible y
auditable: con los mismos datos, siempre la misma decisión y la misma explicación. Las recomendaciones
("el tiempo al primer byte también subió, así que la demora está en el servidor") salen de reglas sobre los
datos, no de un modelo. Además, en banca enviar datos de producción a un servicio externo es un tema de
gobierno de datos.

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
  warmup_seconds: 60                # se mide aparte y no entra en la comparación por endpoint
  max_throughput_decrease_pct: 15
  max_stabilization_increase_s: 20  # degradación de arranque si tarda +20 s en estabilizarse...
  max_warmup_p95_increase_pct: 50   # ...o su p95 de calentamiento sube +50 %
  stabilization_policy: warn        # info | warn | fail
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
`max_error_rate_pct`, `max_error_rate_increase_pp`, `error_confidence_pct`, `min_samples` e `ignore`.

**Precedencia (de mayor a menor), por cada clave:**
1. `endpoints."<nombre exacto>"`
2. `endpoints."<patrón glob>"`: el **primero** que coincida, en el orden del archivo
3. `defaults` del archivo
4. Valores de fábrica de la herramienta (los de esta tabla)

Un override solo cambia las claves que define; las demás se heredan del nivel siguiente. Las claves mal
escritas, los tipos incorrectos (por ejemplo `max_increase_pct: veinte`) y las políticas desconocidas se
rechazan con exit 2, para que un error de tipeo no desactive un límite sin que nadie se dé cuenta.

### Métricas y estadística

| Métrica | Cómo se calcula | Unidad |
|---|---|---|
| Fase estable | Peticiones con `timeStamp >= inicio + warmup_seconds`. Solo esta fase se compara por endpoint | — |
| Percentil (p50/p90/p95/p99) | Interpolación lineal sobre `elapsed` ordenado (mismo criterio que numpy por defecto). Incluye peticiones exitosas y fallidas, como el agregado de JMeter | ms |
| Tasa de error | `success = false` / peticiones, por endpoint y en la fase estable | % |
| Throughput | Peticiones de la fase estable / (fin de la última petición − inicio de la fase estable) | req/s |
| Tiempo de estabilización | Inicio del primer bloque de 10 s desde el cual todos los bloques tienen un promedio ≤ 1,25 × el promedio estable | s |
| Calentamiento | Mismas métricas (p95, error) sobre las peticiones excluidas, para evaluar el arranque | ms, % |
| TTFB (`Latency`) y `bytes` | Promedios que se usan solo para el diagnóstico (servidor frente a transferencia) | ms, bytes |

**Aumento de la tasa de error.** Se evalúan dos reglas independientes:
- **Límite absoluto** (`max_error_rate_pct`): se aplica siempre, sin ninguna prueba estadística. Una
  versión no aprueba solo porque su diferencia con producción no sea significativa.
- **Aumento frente a producción** (`max_error_rate_increase_pp`): bloquea solo si además es
  estadísticamente significativo.
  - Hipótesis: H0 dice que la tasa de la versión nueva es menor o igual que la de producción; H1, que es mayor (una cola).
  - α = 1 − `error_confidence_pct`/100 (0,01 por defecto).
  - Método: **prueba exacta de Fisher** si alguna frecuencia esperada de la tabla 2×2 es menor que 5;
    **prueba z de dos proporciones** si no, porque la aproximación normal no es confiable con conteos bajos.
  - El reporte indica el método y el valor p.
  - Si el aumento supera el umbral pero no es significativo, queda como **WARN** y no se oculta.
  - Limitaciones: supone peticiones independientes y muestra asociación, **no causalidad**.
  - `error_confidence_pct: 0` desactiva la prueba.

### Por qué estos límites por defecto

| Regla | Default | Razón |
|---|---|---|
| Comparar **p95**, no el promedio | p95 | El promedio esconde las colas; el p95 refleja lo que sufre el 5 % de usuarios más lentos |
| Máximo empeoramiento | +20 % | Margen para la variación normal entre corridas sin dejar pasar regresiones reales |
| Diferencia mínima | 15 ms | En endpoints muy rápidos, +30 % pueden ser 3 ms: eso es ruido, no regresión |
| Tasa de error | ≤ 1 % y ≤ +0,5 pp | Ser rápido no sirve si se falla más |
| Significancia del aumento de errores | 99 % | 3 errores sueltos en 400 peticiones no bloquean; un salto real (0,25 % → 3,3 % en 1.170 peticiones) sí. Ver *Métricas y estadística* |
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
| 4 | `GET /reportes` tiene solo 4 peticiones por prueba (1 dentro del calentamiento y 3 en la fase estable), todas al final del archivo y con el timestamp fuera de orden (parecen agregadas después) | Se ordena por timestamp. Con menos de `min_samples` muestras el endpoint no decide el resultado: se advierte |
| 5 | `POST /transferencias` no existe en producción | No tiene línea base: se valida contra límites absolutos (error, `max_ms`) y se advierte si no tiene SLA |
| 6 | A y B reparten la carga entre 5 endpoints y producción entre 4, por eso cada endpoint tiene menos peticiones | El throughput se compara a nivel global, no por endpoint |
| 7 | Duración real de 300 s, 50 hilos, `allThreads = 50` constante, sin duplicados ni valores negativos | Revisado: sin acciones. Las líneas inválidas se descartarían y se contarían en el reporte |

Todo esto aparece en la sección **"Calidad de datos"** de cada reporte.

---

## Diseño

### Núcleo y extensiones

El núcleo (el camino crítico) no depende de servicios externos. Su única dependencia es PyYAML:

```text
CSV/JSON baseline + candidata + YAML
        │
        ▼
Validación de entradas ──(inválido)──► exit 2
        │
        ▼
Lector (JMeter | k6) → modelo neutral → métricas (fase estable + calentamiento)
        │
        ▼
Motor de reglas por endpoint y globales ──► PASS / WARN / FAIL ──► exit 0 / 1  +  report.html/.md/.json
```

Las extensiones consumen el resultado **después** de la evaluación. Si fallan, el veredicto no cambia:

| Extensión | Estado | Por defecto | Si falla o no está configurada |
|---|---|---|---|
| Lector k6 | Estable (validado con k6 2.2.0 real) | Se detecta por contenido | No afecta al caso JMeter |
| Issues automáticos | Opcional | **Desactivado** (`create-issue: false`) | Aviso y se omite; exit del gate intacto |

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
- La prueba de carga real con k6 corre en GitHub Actions, en
  [perf-gate-demo](https://github.com/LeoR22/perf-gate-demo#prueba-de-carga-real-con-k6-en-github-actions).

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
