#!/usr/bin/env bash
# sdd_apply_gate.sh — Hook bloqueante pre-apply para agentes de implementación.
#
# Consume el veredicto del status engine (sdd_status.py) y decide si un agente
# tiene permiso para tocar código. Mitiga el riesgo que le da sentido a todo el
# ciclo: que el equipo trate SDD como teatro y saltee fases bajo presión.
#
# Cómo se activa
# --------------
# Llamar desde el orquestador ANTES de spawnear cualquier agente cuyo trabajo
# modifique código o workflows productivos.
# Uso típico:
#   engine/sdd_apply_gate.sh <change-name> [bypass-reason]
#
# Configuración (env)
# -------------------
#   SDD_STATUS_BASE     raíz de los changes      (default: sdd/changes)
#   SDD_STATUS_ENGINE   path al status engine    (default: engine/sdd_status.py)
#   SDD_GATE_LOG        archivo de log           (default: .sdd-apply-gate.log)
#
# El gate y el engine LEEN LA MISMA variable de raíz a propósito: si cada uno
# resolviera la suya, el gate podría aprobar mirando un árbol de changes y el
# engine emitir su veredicto sobre otro.
#
# Exit codes
# ----------
#   0 = APROBADO (artifacts SDD presentes y completos)
#   1 = BLOQUEADO (falta artifact o tasks con riesgo no resuelto)
#   2 = BYPASS aceptado con razón documentada
#
# Bypass
# ------
# Razones canónicas para skip (segundo argumento):
#   - "smoke-test"        : fix de 1-3 líneas que no muta lógica
#   - "fix-1-line"        : corrección trivial
#   - "hot-fix-prod"      : incidente productivo, gate post-fix
#   - "refactor-rollout"  : durante un rollout de refactor autorizado
# Cualquier otra razón debe llevar firma humana en el log.
#
# Log
# ---
# Cada bloqueo y bypass se registra con timestamp + change-name + decisión +
# razón (si bypass).

set -euo pipefail

CHANGE_NAME="${1:-}"
BYPASS_REASON="${2:-}"
SDD_BASE="${SDD_STATUS_BASE:-sdd/changes}"
ENGINE_PY="${SDD_STATUS_ENGINE:-engine/sdd_status.py}"
LOG_FILE="${SDD_GATE_LOG:-.sdd-apply-gate.log}"

mkdir -p "$(dirname "$LOG_FILE")"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') | $1 | change=$CHANGE_NAME | reason=$BYPASS_REASON" >> "$LOG_FILE"
}

fail() {
  log "BLOCKED-$1"
  echo "❌ SDD GATE BLOQUEADO: $1"
  echo "   Para change '$CHANGE_NAME' en $SDD_BASE/$CHANGE_NAME/"
  echo ""
  echo "   Acciones para destrabar:"
  echo "   1. Completar artifacts SDD: specs/{capability}/spec.md · design.md · tasks.md"
  echo "   2. Resolver guard lines en tasks.md ('Decision needed: No', '400-line budget: Low|Medium')"
  echo "   3. Si edge case real → bypass con razón canónica:"
  echo "      $0 $CHANGE_NAME smoke-test|fix-1-line|hot-fix-prod|refactor-rollout"
  echo ""
  exit 1
}

# --- Bypass path ---
if [ -n "$BYPASS_REASON" ]; then
  case "$BYPASS_REASON" in
    smoke-test|fix-1-line|hot-fix-prod|refactor-rollout)
      log "BYPASS-ACCEPTED"
      echo "⚠️  SDD GATE BYPASS aceptado: $BYPASS_REASON"
      exit 2
      ;;
    *)
      log "BYPASS-REJECTED-unknown-reason"
      echo "❌ SDD GATE: razón de bypass desconocida ('$BYPASS_REASON')."
      echo "   Razones canónicas: smoke-test · fix-1-line · hot-fix-prod · refactor-rollout"
      exit 1
      ;;
  esac
fi

# --- Validaciones bloqueantes ---
if [ -z "$CHANGE_NAME" ]; then
  echo "Uso: $0 <change-name> [bypass-reason]"
  exit 1
fi

CHANGE_DIR="$SDD_BASE/$CHANGE_NAME"
[ -d "$CHANGE_DIR" ] || fail "no existe directorio $CHANGE_DIR"

# delta specs: layout gentle-ai `specs/{capability}/spec.md` (walk recursivo,
# filename exacto). Adoptado 2026-08-04 — el `spec.md` plano en la raíz del
# change YA NO cuenta. Al menos un spec.md no vacío bajo specs/.
if ! find "$CHANGE_DIR/specs" -type f -name 'spec.md' -size +0 2>/dev/null | grep -q .; then
  fail "$CHANGE_DIR/specs/{capability}/spec.md vacío o ausente (layout de delta specs)"
fi

# design.md no vacío
[ -s "$CHANGE_DIR/design.md" ] || fail "$CHANGE_DIR/design.md vacío o ausente"

# tasks.md con guards
[ -s "$CHANGE_DIR/tasks.md" ] || fail "$CHANGE_DIR/tasks.md vacío o ausente"

# Guard lines (case-insensitive). Aceptamos formato:
#   Decision needed before apply: No
#   400-line budget risk: Low | Medium  (rechaza High)
if ! grep -qi "Decision needed before apply: *No" "$CHANGE_DIR/tasks.md"; then
  fail "tasks.md no declara 'Decision needed before apply: No' (decisión pendiente)"
fi

if grep -qi "400-line budget risk: *High" "$CHANGE_DIR/tasks.md"; then
  fail "tasks.md declara '400-line budget risk: High' — revisar scope antes de apply"
fi

# --- SDD status engine (machine-readable routing) ---------------------------
# Además del regex de prosa de arriba, rutear por TOKEN-MÁQUINA: el engine
# ($ENGINE_PY) emite applyAllowed + blockedReasons estructurados.
# Regla de oro: el JSON del engine es autoritativo sobre la inferencia de prosa.
#
# FAIL-CLOSED (B1 — 2026-08-05, hallado en auditoría adversarial; el defecto
# había quedado 6 días como deuda anotada antes de que alguien lo mirara).
# ---------------------------------------------------------------------------
# Antes cada campo se pedía en su propia invocación con
# `2>/dev/null || echo "true"`. Eso hacía que el gate fallara en ABIERTO: si el
# engine crasheaba (SyntaxError, ImportError, python3 ausente, JSON corrupto),
# `applyAllowed` valía "true" por DEFAULT y el gate imprimía "✅ APROBADO"
# exit 0 — con el traceback tragado por /dev/null. Reproducido: mismo change,
# engine sano → BLOQUEADO exit 1; engine roto → APROBADO exit 0. Un bug de
# sintaxis en el oráculo se convertía en permiso de apply.
#
# Ahora: UNA sola invocación que captura JSON + stderr + exit code. Si el
# oráculo no responde, responde ≠0, devuelve vacío, o devuelve algo que no es
# el JSON esperado → BLOQUEA y muestra el stderr REAL. Un oráculo que no
# funciona no es un "sí": es un "no sé", y un "no sé" no autoriza tocar código
# productivo.
if [ -f "$ENGINE_PY" ]; then
  ENGINE_ERR_FILE="$(mktemp -t sdd-apply-gate-engine.XXXXXX)"
  ENGINE_JSON=""
  ENGINE_RC=0
  # `set -e` mataría el script en un exit≠0 dentro de la sustitución antes de
  # que podamos reportar el stderr; se desactiva solo para esta llamada.
  set +e
  ENGINE_JSON="$(SDD_STATUS_BASE="$SDD_BASE" python3 "$ENGINE_PY" "$CHANGE_NAME" 2>"$ENGINE_ERR_FILE")"
  ENGINE_RC=$?
  set -e
  ENGINE_ERR="$(cat "$ENGINE_ERR_FILE" 2>/dev/null || true)"
  rm -f "$ENGINE_ERR_FILE"

  engine_oracle_failed() {
    echo "   ── stderr del oráculo ($ENGINE_PY) ──"
    if [ -n "$ENGINE_ERR" ]; then
      printf '%s\n' "$ENGINE_ERR" | sed 's/^/   | /'
    else
      echo "   | (vacío)"
    fi
    echo "   ───────────────────────────────────────────────"
    fail "el oráculo de estado SDD no respondió ($1, exit=$ENGINE_RC) — FAIL-CLOSED: sin veredicto del engine NO se autoriza apply"
  }

  if [ "$ENGINE_RC" -ne 0 ]; then
    engine_oracle_failed "engine salió con error"
  fi
  if [ -z "$ENGINE_JSON" ]; then
    engine_oracle_failed "engine no emitió salida"
  fi

  # Extracción de campos en UNA pasada desde el JSON ya capturado. Si el parseo
  # falla (JSON corrupto/truncado, campos ausentes), también es fail-closed:
  # `ENGINE_FIELDS` queda vacío / rc≠0 y bloqueamos.
  set +e
  ENGINE_FIELDS="$(printf '%s' "$ENGINE_JSON" | python3 -c '
import json, sys
d = json.load(sys.stdin)
if not isinstance(d, dict) or "applyAllowed" not in d:
    raise SystemExit("payload sin applyAllowed — no es el schema agent-engineering.sdd-status")
print(json.dumps(d.get("applyAllowed")))
print(str(d.get("nextRecommended") or "?"))
print(json.dumps(d.get("blockedReasons", []), ensure_ascii=False))
' 2>"$ENGINE_ERR_FILE.parse")"
  PARSE_RC=$?
  set -e
  if [ "$PARSE_RC" -ne 0 ] || [ -z "$ENGINE_FIELDS" ]; then
    ENGINE_ERR="$(cat "$ENGINE_ERR_FILE.parse" 2>/dev/null || true)"
    ENGINE_RC=$PARSE_RC
    rm -f "$ENGINE_ERR_FILE.parse"
    engine_oracle_failed "salida del engine no parseable como agent-engineering.sdd-status"
  fi
  rm -f "$ENGINE_ERR_FILE.parse"

  APPLY_OK="$(printf '%s\n' "$ENGINE_FIELDS" | sed -n '1p')"
  NEXT="$(printf '%s\n' "$ENGINE_FIELDS" | sed -n '2p')"
  BLK="$(printf '%s\n' "$ENGINE_FIELDS" | sed -n '3p')"

  # applyAllowed viene como literal JSON: true | false.
  if [ "$APPLY_OK" != "true" ]; then
    echo "   SDD status engine: applyAllowed=$APPLY_OK · nextRecommended=$NEXT"
    echo "   blockedReasons: $BLK"
    fail "engine bloquea apply (nextRecommended=$NEXT). Resolvé blockers/artifacts antes de delegar a hoja APPLY"
  fi
  log "APPROVED"
fi

# Aprobado
echo "✅ SDD GATE APROBADO para '$CHANGE_NAME'"
exit 0
