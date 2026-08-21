#!/usr/bin/env python3
"""
Tests del DEADLOCK del gate por tareas (2026-08-04, review adversarial).

CONTEXTO — qué se rompió y por qué estos tests existen:

El fix del FALSO VERDE agregó `TASK_GATED_PHASES = {"apply", "verify"}`:
cualquier `pending > 0` degrada apply Y verify a `in_progress`. Correcto en
sí mismo, pero el ruteo seguía siendo "primera fase no-done" → devolvía
SIEMPRE `apply`, aunque el `verify-report.md` ya estuviera escrito y limpio.
`verify` no se recomendaba NUNCA, y nadie podía romper el bucle:

  · `sdd-verify` tiene tools Read/Grep/Glob/Bash — no puede tildar sus
    propios checkboxes;
  · `sdd-apply` sí puede tildarlos, pero lo que queda pendiente son criterios
    de verificación (VG-*, SC-*, "cipher + ojo humano") — no son suyos;
  · `sdd-archive` (Task Completion Gate) hard-blockea cualquier `- [ ]`.

Encima el bloqueo era MUDO: `blockedReasons: []` mientras `dependencies`
decía `in_progress`/`blocked`, así que `cierre-gate.sh` imprimía
"· change -> apply" sin explicar por qué.

Los tests cubren las dos mitades del fix (ruteo + observabilidad) y las tres
NO-REGRESIONES que el fix no puede romper (falso verde, caso limpio,
contrato de campos de los hooks).

Fixtures 100% bajo tmp_path — no se toca ningún sdd/changes/* real.
"""
import importlib.util
from pathlib import Path

# el engine se carga por path, no por nombre de módulo
_spec = importlib.util.spec_from_file_location(
    "sdd_status_task_gate_deadlock",
    Path(__file__).resolve().parent.parent / "sdd_status.py"
)
_sdd_status = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sdd_status)


def _run_compute(tmp_path, change_name, artifacts):
    """Escribe artifacts en tmp_path y corre compute() con BASE override."""
    change_dir = tmp_path / change_name
    change_dir.mkdir(parents=True)
    for filename, content in artifacts.items():
        target = change_dir / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    original_base = _sdd_status.BASE
    _sdd_status.BASE = tmp_path
    try:
        return _sdd_status.compute(change_name)
    finally:
        _sdd_status.BASE = original_base


# verify-report limpio y estructurado: Status success, 4R PASSED, CRITICAL 0.
# Nada acá puede producir un blocker "de verdad" — si el change no cierra es
# exclusivamente por las tareas pendientes.
_CLEAN_VERIFY = """# Verify Report

Status: success

**4R Verdict:** PASSED
**Bloqueantes 4R:** []

## Findings
- **CRITICAL:** 0
- **WARNING:** 0
"""

_BASE_ARTIFACTS = {
    "proposal.md": "p",
    "specs/core/spec.md": "s",
    "design.md": "d",
}

# Fixture EXACTA del reporte de deadlock: 2 tareas de código hechas, 2 tareas
# de verificación pendientes (suite E2E + cipher/ojo humano), verify-report
# escrito y limpio.
_DEADLOCK_TASKS = (
    "- [x] T1 código\n"
    "- [x] T2 código\n"
    "- [ ] VG-1 suite E2E\n"
    "- [ ] VG-2 cipher + ojo humano\n"
)


class TestTaskGateDeadlockRouting:
    """Mitad 1 del fix: el ruteo tiene que poder llegar a `verify`."""

    def test_deadlock_fixture_routes_to_verify_not_apply(self, tmp_path):
        """LA fixture del deadlock: con verify-report limpio en disco y solo
        tareas de verificación pendientes, el motor DEBE rutear a `verify`.

        Sin el fix esto devuelve `apply` — para siempre, en cada corrida, sin
        que exista ningún actor capaz de cambiar el estado.
        """
        result = _run_compute(tmp_path, "deadlock-fixture", {
            **_BASE_ARTIFACTS,
            "tasks.md": _DEADLOCK_TASKS,
            "apply-report.md": "apply hecho",
            "verify-report.md": _CLEAN_VERIFY,
        })
        assert result["nextRecommended"] == "verify", (
            f"DEADLOCK: ruteo devolvió {result['nextRecommended']!r}; con "
            f"verify-report presente y limpio tiene que ser 'verify'"
        )
        # El diagnóstico de fases NO cambia — apply/verify siguen degradadas
        # por el gate de tareas (el fix del falso verde no se toca).
        assert result["dependencies"]["apply"] == "in_progress"
        assert result["dependencies"]["verify"] == "in_progress"
        assert result["taskProgress"]["pending"] == 2

    def test_verify_present_without_apply_report_routes_to_apply(self, tmp_path):
        """CORREGIDO 2026-08-05 (B3, auditoría adversarial). Antes se llamaba
        `test_verify_present_without_apply_report_routes_to_verify` y afirmaba
        `nextRecommended == "verify"`. Eso NO era la especificación: era el bug
        codificado como test.

        Por qué cambió — NO es una regresión, no lo revuelvan:
        el ruteo saltaba a la fase más avanzada PRESENTE sin mirar si alguna
        fase ANTERIOR tenía su artefacto ausente. Con `verify-report.md` en
        disco y `apply-report.md` inexistente, mandaba a `verify` aunque
        `dependencies` dijera `apply: ready` — violando el principio que el
        propio motor declara en compute() ("el ruteo nunca debe saltear una
        fase que `dependencies` reporta no-terminada").

        El efecto en vivo era un DEADLOCK, no una molestia: las tasks
        pendientes solo se tildan APLICANDO, así que un change ruteado
        permanentemente a `verify` no podía avanzar nunca. Al detectarlo
        estaban atrapados 2 de 12 changes vivos — `app-save-unification`
        (15 pending) y `app-privacy-first` (1 pending): ambos con
        verify-report, ninguno con apply-report.

        Comportamiento CORRECTO: sin `apply-report.md` la fase apply nunca
        ocurrió; no hay nada que saltear → `apply`. (El fix original del
        deadlock sigue vivo y cubierto por
        `test_deadlock_fixture_routes_to_verify_not_apply`: CON apply-report en
        disco, el ruteo sí llega a `verify`.)
        """
        result = _run_compute(tmp_path, "verify-sin-apply", {
            **_BASE_ARTIFACTS,
            "tasks.md": "- [x] T-1 impl\n- [ ] T-VERIFY ejecutar suite final\n",
            "verify-report.md": _CLEAN_VERIFY,
        })
        assert result["nextRecommended"] == "apply", (
            f"B3: sin apply-report en disco el ruteo debe ser 'apply', no "
            f"{result['nextRecommended']!r} — ese es el deadlock vivo"
        )
        assert result["dependencies"]["apply"] == "ready"
        assert result["dependencies"]["verify"] == "in_progress"

    def test_all_tasks_done_still_routes_archive(self, tmp_path):
        """No-regresión (caso `client-listings-engine`, 23/23): sin tareas
        pendientes el gate no dispara y el ruteo sigue llegando a `archive`."""
        result = _run_compute(tmp_path, "todo-hecho", {
            **_BASE_ARTIFACTS,
            "tasks.md": "- [x] T1\n- [x] T2\n",
            "apply-report.md": "apply hecho",
            "verify-report.md": _CLEAN_VERIFY,
        })
        assert result["nextRecommended"] == "archive"
        assert result["blockedReasons"] == []

    def test_no_verify_report_with_pending_tasks_still_routes_apply(self, tmp_path):
        """NO-REGRESIÓN CRÍTICA del fix del FALSO VERDE
        (`client-site-v1`, 31/88 con apply-report parcial y sin
        verify-report): sin evidencia de verify en disco, tareas pendientes
        mandan a `apply`. El fix del deadlock NO puede revertir esto."""
        result = _run_compute(tmp_path, "falso-verde", {
            **_BASE_ARTIFACTS,
            "tasks.md": "- [x] T1\n- [ ] T2\n- [ ] T3\n",
            "apply-report.md": "apply parcial",
        })
        assert result["nextRecommended"] == "apply"
        assert result["dependencies"]["apply"] == "in_progress"
        assert result["dependencies"]["verify"] == "blocked"

    def test_early_phase_missing_routes_to_that_phase(self, tmp_path):
        """Flujo normal temprano intacto: sin design.md el ruteo va a `design`,
        el scan por artefacto presente no lo altera."""
        result = _run_compute(tmp_path, "temprano", {
            "proposal.md": "p",
            "specs/core/spec.md": "s",
        })
        assert result["nextRecommended"] == "design"

    def test_verify_present_but_blocked_still_wins_resolve_blockers(self, tmp_path):
        """Precedencia intacta: un verify-report que declara falla produce
        `resolve-blockers`, no `verify`. El ruteo por artefacto presente NO
        puede pisar a los blockers reales."""
        result = _run_compute(tmp_path, "verify-fallado", {
            **_BASE_ARTIFACTS,
            "tasks.md": _DEADLOCK_TASKS,
            "apply-report.md": "apply hecho",
            "verify-report.md": "Status: blocked\n\n**4R Verdict:** FAILED\n",
        })
        assert result["nextRecommended"] == "resolve-blockers"


class TestTaskGateBlockedReasonObservability:
    """Mitad 2 del fix: el bloqueo dejó de ser mudo."""

    def test_deadlock_fixture_emits_explicit_blocked_reason(self, tmp_path):
        """La fixture del deadlock tiene que traer un `blockedReason` que diga
        POR QUÉ no cierra. Sin el fix la lista queda `[]` y `cierre-gate.sh`
        imprime "· change -> apply" sin explicación."""
        result = _run_compute(tmp_path, "deadlock-reason", {
            **_BASE_ARTIFACTS,
            "tasks.md": _DEADLOCK_TASKS,
            "apply-report.md": "apply hecho",
            "verify-report.md": _CLEAN_VERIFY,
        })
        assert result["blockedReasons"], (
            "bloqueo MUDO: hay tareas pendientes degradando apply/verify y "
            "blockedReasons quedó vacío"
        )
        reason = result["blockedReasons"][0]
        assert "2" in reason and "pendientes" in reason
        assert "tasks.md" in reason

    def test_blocked_reason_names_both_gated_phases(self, tmp_path):
        """Con apply-report Y verify-report presentes, la razón nombra las dos
        fases que el gate está frenando."""
        result = _run_compute(tmp_path, "reason-dos-fases", {
            **_BASE_ARTIFACTS,
            "tasks.md": _DEADLOCK_TASKS,
            "apply-report.md": "apply hecho",
            "verify-report.md": _CLEAN_VERIFY,
        })
        reason = result["blockedReasons"][0]
        assert "apply/verify" in reason, reason

    def test_blocked_reason_names_only_the_present_phase(self, tmp_path):
        """Sin verify-report, la razón habla solo de `apply` — no inventa un
        bloqueo sobre una fase cuyo artefacto ni existe."""
        result = _run_compute(tmp_path, "reason-una-fase", {
            **_BASE_ARTIFACTS,
            "tasks.md": "- [x] T1\n- [ ] T2\n",
            "apply-report.md": "apply parcial",
        })
        reason = result["blockedReasons"][0]
        assert "apply" in reason and "verify" not in reason, reason

    def test_no_report_yet_emits_no_task_gate_reason(self, tmp_path):
        """Tareas pendientes SIN ningún report escrito no son un bloqueo: son
        trabajo por hacer. El ruteo ya dice `apply` y no hay nada que explicar
        → `blockedReasons` sigue vacío (cero ruido)."""
        result = _run_compute(tmp_path, "sin-reports", {
            **_BASE_ARTIFACTS,
            "tasks.md": "- [ ] T1\n- [ ] T2\n",
        })
        assert result["blockedReasons"] == []
        assert result["nextRecommended"] == "apply"

    def test_hard_blockers_come_first_and_task_reason_is_appended(self, tmp_path):
        """Un blocker REAL (verify fallado) sigue primero en la lista; la razón
        informativa del gate de tareas se agrega después, sin desplazarlo."""
        result = _run_compute(tmp_path, "mixto", {
            **_BASE_ARTIFACTS,
            "tasks.md": _DEADLOCK_TASKS,
            "apply-report.md": "apply hecho",
            "verify-report.md": "Status: blocked\n",
        })
        assert len(result["blockedReasons"]) == 2
        assert "Status=blocked" in result["blockedReasons"][0]
        assert "pendientes" in result["blockedReasons"][1]


class TestTaskGateReasonDoesNotBlockApply:
    """Contrato vigente de los hooks: la razón informativa NO puede vetar el
    trabajo. Si entrara en los blockers duros, `_compute_next_recommended`
    devolvería `resolve-blockers` y `applyAllowed` daría False — dejando al
    change sin poder aplicar Y sin poder verificar (deadlock peor)."""

    def test_apply_allowed_stays_true_with_task_gate_reason(self, tmp_path):
        result = _run_compute(tmp_path, "apply-sigue-permitido", {
            **_BASE_ARTIFACTS,
            "tasks.md": _DEADLOCK_TASKS,
            "apply-report.md": "apply hecho",
            "verify-report.md": _CLEAN_VERIFY,
        })
        assert result["blockedReasons"], "precondición: la razón debe estar emitida"
        assert result["applyAllowed"] is True, (
            "la razón informativa del gate de tareas bloqueó el apply — rompe "
            "el contrato 'quedan tareas → hay que SEGUIR aplicando'"
        )
        assert result["nextRecommended"] != "resolve-blockers"

    def test_field_contract_types_unchanged_with_task_gate_reason(self, tmp_path):
        """Nombres y tipos de los campos que consumen los hooks, intactos."""
        result = _run_compute(tmp_path, "contrato-campos", {
            **_BASE_ARTIFACTS,
            "tasks.md": _DEADLOCK_TASKS,
            "apply-report.md": "apply hecho",
            "verify-report.md": _CLEAN_VERIFY,
        })
        assert isinstance(result["applyAllowed"], bool)
        assert isinstance(result["nextRecommended"], str)
        assert isinstance(result["blockedReasons"], list)
        assert all(isinstance(r, str) for r in result["blockedReasons"])
        assert set(result["dependencies"]) == set(_sdd_status.PHASES)


class TestRouteIncompletePhaseUnit:
    """Unit del helper de ruteo, aislado de compute()."""

    def test_present_none_preserves_legacy_first_not_done(self):
        """Sin `present` (default), comportamiento previo exacto: primera fase
        no-done. Garantiza que cualquier caller viejo no cambia de semántica."""
        done = {ph: False for ph in _sdd_status.PHASES}
        done["proposal"] = True
        assert _sdd_status._route_incomplete_phase(done) == "spec"

    def test_advances_to_furthest_present_incomplete_phase(self):
        done = {ph: True for ph in _sdd_status.PHASES}
        done["apply"] = done["verify"] = done["archive"] = False
        present = {ph: True for ph in _sdd_status.PHASES}
        present["archive"] = False
        assert _sdd_status._route_incomplete_phase(done, present) == "verify"

    def test_does_not_advance_past_absent_artifact(self):
        done = {ph: True for ph in _sdd_status.PHASES}
        done["apply"] = done["verify"] = done["archive"] = False
        present = {ph: True for ph in _sdd_status.PHASES}
        present["verify"] = present["archive"] = False
        assert _sdd_status._route_incomplete_phase(done, present) == "apply"

    def test_all_done_returns_archive(self):
        done = {ph: True for ph in _sdd_status.PHASES}
        present = {ph: True for ph in _sdd_status.PHASES}
        assert _sdd_status._route_incomplete_phase(done, present) == "archive"
