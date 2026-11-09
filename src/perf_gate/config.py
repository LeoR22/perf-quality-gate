"""Configuración de límites. Cada equipo la ajusta con un archivo YAML/JSON propio."""
from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

POLICIES = ("fail", "warn", "info", "ignore")   # info e ignore: se informa, no bloquea ni advierte


@dataclass(frozen=True)
class GlobalRules:
    warmup_seconds: float = 60            # fase de calentamiento: se mide aparte y no entra en la comparación
    max_throughput_decrease_pct: float = 15
    # Degradación de ARRANQUE (cold start), evaluada con stabilization_policy:
    max_stabilization_increase_s: float = 20      # tarda más en estabilizarse que producción
    max_warmup_p95_increase_pct: float = 50       # p95 durante el calentamiento vs el de producción
    stabilization_policy: str = "warn"    # info | warn | fail  (fail: el cold start bloquea el despliegue)
    new_endpoint_policy: str = "warn"     # endpoint que no existe en producción
    missing_endpoint_policy: str = "warn"  # endpoint de producción que no se probó
    fail_on_warnings: bool = False


@dataclass(frozen=True)
class EndpointRules:
    percentile: int = 95                  # percentil que se compara
    max_increase_pct: float = 20          # cuánto puede empeorar vs producción
    min_increase_ms: float = 15           # ignora "regresiones" menores (ruido)
    max_ms: float | None = None           # SLA absoluto opcional (para el percentil)
    max_error_rate_pct: float = 1.0       # tasa de error absoluta máxima
    max_error_rate_increase_pp: float = 0.5   # puntos porcentuales sobre producción...
    error_confidence_pct: float = 99      # ...y solo si el aumento es estadísticamente significativo (0 = sin prueba)
    min_samples: int = 30                 # por debajo no es estadísticamente confiable
    ignore: bool = False


@dataclass(frozen=True)
class GateConfig:
    global_rules: GlobalRules
    defaults: EndpointRules
    endpoints: dict[str, dict[str, Any]]  # etiqueta o patrón glob -> overrides

    def rules_for(self, label: str) -> EndpointRules:
        """Exacto primero; si no, el primer patrón glob que coincida (p. ej. 'POST /*')."""
        overrides = self.endpoints.get(label)
        if overrides is None:
            overrides = next(
                (v for k, v in self.endpoints.items() if fnmatch.fnmatchcase(label, k)), {}
            )
        return replace(self.defaults, **overrides)


def _check_keys(section: str, data: dict, cls_or_keys) -> dict:
    if not isinstance(data, dict):
        raise ValueError(f"Config '{section}' debe ser un objeto/mapa")
    allowed = (
        set(cls_or_keys) if isinstance(cls_or_keys, (set, tuple, list))
        else {f.name for f in fields(cls_or_keys)}
    )
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(
            f"Config '{section}': claves desconocidas {sorted(unknown)}. Válidas: {sorted(allowed)}"
        )
    if not isinstance(cls_or_keys, (set, tuple, list)):
        _check_types(section, data, cls_or_keys)
    return data


def _check_types(section: str, data: dict, cls) -> None:
    """El tipo esperado sale del valor por defecto: un número mal escrito no puede llegar a la evaluación."""
    defaults = cls()
    for key, value in data.items():
        default = getattr(defaults, key)
        where = f"Config '{section}.{key}'"
        if isinstance(default, bool):
            if not isinstance(value, bool):
                raise ValueError(f"{where} debe ser true o false y es {value!r}")
        elif isinstance(default, (int, float)) or default is None:  # max_ms es opcional (None)
            if value is None and default is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"{where} debe ser un número >= 0 y es {value!r}")
        elif isinstance(default, str) and not isinstance(value, str):
            raise ValueError(f"{where} debe ser texto y es {value!r}")


def _read_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8-sig")
    try:
        if path.suffix.lower() == ".json":
            data = json.loads(text) or {}
        else:
            import yaml  # solo se necesita para YAML
            data = yaml.safe_load(text) or {}
    except Exception as exc:  # yaml.YAMLError / json.JSONDecodeError
        raise ValueError(f"{path}: no es un YAML/JSON válido: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path}: la configuración debe ser un mapa (global / defaults / endpoints)")
    return data


def load_config(path: str | Path | None = None) -> GateConfig:
    raw: dict = {}
    if path:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"No existe el archivo de configuración: {p}")
        raw = _read_file(p)

    _check_keys("raíz", raw, ("global", "defaults", "endpoints"))
    g = GlobalRules(**_check_keys("global", raw.get("global") or {}, GlobalRules))
    d = EndpointRules(**_check_keys("defaults", raw.get("defaults") or {}, EndpointRules))
    eps = raw.get("endpoints") or {}
    for label, ov in eps.items():
        _check_keys(f"endpoints.{label}", ov or {}, EndpointRules)
    eps = {k: (v or {}) for k, v in eps.items()}

    for name in ("stabilization_policy", "new_endpoint_policy", "missing_endpoint_policy"):
        if getattr(g, name) not in POLICIES:
            raise ValueError(f"global.{name} debe ser uno de {POLICIES}")
    for label in ["defaults", *eps]:
        r = d if label == "defaults" else replace(d, **eps[label])
        if r.percentile not in (50, 90, 95, 99):
            raise ValueError(f"{label}: percentile debe ser 50, 90, 95 o 99")
    return GateConfig(global_rules=g, defaults=d, endpoints=eps)
