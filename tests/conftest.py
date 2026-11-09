"""Aísla los tests del entorno de GitHub Actions.

En el CI existen GITHUB_ACTIONS, GITHUB_STEP_SUMMARY y GITHUB_OUTPUT. Sin este aislamiento, cada test
que ejecuta la CLI escribiría su reporte en el resumen del job de tests (decenas de reportes repetidos).
"""
import pytest


@pytest.fixture(autouse=True)
def _sin_entorno_de_github(monkeypatch):
    for var in ("GITHUB_ACTIONS", "GITHUB_STEP_SUMMARY", "GITHUB_OUTPUT"):
        monkeypatch.delenv(var, raising=False)
