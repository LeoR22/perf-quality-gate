#!/usr/bin/env bash
# Documenta el defecto en GitHub Issues según el veredicto del gate (sin IA, determinista):
#   FAIL       -> abre un issue "[perf-gate] <versión> no pasa", o comenta el existente
#   PASS/WARN  -> si había un issue abierto para esa versión, lo cierra con el enlace a la ejecución
# Variables: GH_TOKEN, RESULT, NAME, REPORT_DIR, GITHUB_REPOSITORY, GITHUB_SERVER_URL, GITHUB_RUN_ID
set -euo pipefail

LABEL="perf-gate"
TITLE="[perf-gate] ${NAME} no pasa"
RUN_URL="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
REPO="${GITHUB_REPOSITORY}"

existing=$(gh issue list --repo "$REPO" --state open --label "$LABEL" --limit 100 \
  --json number,title --jq "map(select(.title == \"${TITLE}\")) | .[0].number // empty" 2>/dev/null || true)

if [ "$RESULT" != "FAIL" ]; then
  if [ -n "$existing" ]; then
    gh issue close "$existing" --repo "$REPO" \
      --comment "✅ Resuelto: \`${NAME}\` ya pasa el gate (${RESULT}) en [esta ejecución](${RUN_URL})."
    echo "Issue #${existing} cerrado: la versión ya pasa."
  else
    echo "La versión pasa (${RESULT}); no hay issue que abrir ni cerrar."
  fi
  exit 0
fi

body=$(mktemp)
{
  echo "**Ejecución:** ${RUN_URL}"
  echo
  [ -f "${REPORT_DIR}/report.md" ] && cat "${REPORT_DIR}/report.md"
  if [ -f "${REPORT_DIR}/ai-summary.md" ]; then
    echo
    cat "${REPORT_DIR}/ai-summary.md"
  fi
  echo
  echo "---"
  echo "_Issue gestionado por [Performance Quality Gate](https://github.com/LeoR22/perf-quality-gate): se cierra solo cuando la versión pase._"
} > "$body"

if [ -n "$existing" ]; then
  gh issue comment "$existing" --repo "$REPO" --body-file "$body"
  echo "Issue #${existing} actualizado con la nueva ejecución."
else
  gh label create "$LABEL" --repo "$REPO" --color D93F0B \
    --description "Regresión de performance detectada por el gate" 2>/dev/null || true
  url=$(gh issue create --repo "$REPO" --title "$TITLE" --label "$LABEL" --body-file "$body")
  echo "Issue creado: ${url}"
  echo "### 🐞 Defecto documentado: ${url}" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
fi
