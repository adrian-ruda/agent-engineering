#!/usr/bin/env python3
"""
Tests de la auditoría adversarial del oráculo SDD y su gate (2026-08-05).

Cuatro blockers, todos reproducidos con fixture antes de fixear:

  B1 — `sdd-apply-gate.sh` fallaba en ABIERTO. Cada campo se pedía con
       `2>/dev/null || echo "true"`: si el engine crasheaba, `applyAllowed`
       valía "true" por default y el gate imprimía "✅ APROBADO" exit 0, con
       el traceback tragado. Mismo change: engine sano → BLOQUEADO exit 1;
       engine con SyntaxError → APROBADO exit 0. Cualquier otro bug del
       motor se convertía, vía este default, en permiso de apply.

  B2 — `task_progress()` y `detect_blockers()` leían tasks.md a pelo. Un
       tasks.md que es DIRECTORIO (IsADirectoryError) o sin permisos
       (PermissionError) reventaba el motor entero → exit 1 → disparaba B1.

  B3 — DEADLOCK vivo en 2 de 12 changes. `_route_incomplete_phase` saltaba a
       la fase más avanzada PRESENTE sin verificar que las anteriores
       existieran: con verify-report y SIN apply-report ruteaba a `verify`
       para siempre, y las tasks pendientes solo se tildan aplicando.

  B4 — Greenwash de un tecleo: `[-]`/`[—]` salían del total y no producían
       ningún blocker, así que 3 tareas reales tildadas N/A daban
       `next=archive`, `applyAllowed=True`, `blockedReasons: []`.

Todo bajo tmp_path — ningún árbol de changes real se toca, y el mirror
del repo para probar el gate se construye entero en tmp.
"""
import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ENGINE_DIR = Path(__file__).resolve().parent.parent
HOOK = ENGINE_DIR / "sdd_apply_gate.sh"
ENGINE = ENGINE_DIR / "sdd_status.py"

# Layout que el mirror reproduce. Son los MISMOS defaults que el gate y el
# engine resuelven por su cuenta: si acá se escribieran otros, los tests E2E
# probarían un acoplamiento que en producción no existe.
MIRROR_ENGINE_REL = Path("engine") / "sdd_status.py"
MIRROR_CHANGES_REL = Path("sdd") / "changes"

_spec = importlib.util.spec_from_file_location("sdd_status_failclosed", ENGINE)
_sdd_status = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sdd_status)

task_progress = _sdd_status.task_progress


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


def _write_change(root: Path, change_name: str, artifacts: dict) -> Path:
    change_dir = root / change_name
    change_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in artifacts.items():
        target = change_dir / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return change_dir


def _run_compute(tmp_path, change_name, artifacts):
    _write_change(tmp_path, change_name, artifacts)
    original_base = _sdd_status.BASE
    _sdd_status.BASE = tmp_path
    try:
        return _sdd_status.compute(change_name)
    finally:
        _sdd_status.BASE = original_base


# ══════════════════════════════════════════════════════════════════════
# B1 — el gate debe fallar CERRADO cuando el oráculo no responde
# ══════════════════════════════════════════════════════════════════════

# tasks.md que pasa todas las guardas de prosa del hook, para que lo único que
# pueda cambiar el veredicto sea el estado del engine.
_GATE_OK_TASKS = """# Tasks

Decision needed before apply: No
400-line budget risk: Low

- [x] T-1 hecha
- [ ] T-2 por hacer
"""


def _build_mirror(tmp_path: Path, engine_source: str | None = None) -> Path:
    """Mirror MÍNIMO del repo en tmp — nunca se toca el árbol real.

    `engine_source=None` copia el engine REAL (control sano). Un string lo
    reemplaza por ese contenido (engine roto).
    """
    mirror = tmp_path / "mirror-repo"
    mirror_engine = mirror / MIRROR_ENGINE_REL
    mirror_engine.parent.mkdir(parents=True)
    changes = mirror / MIRROR_CHANGES_REL
    changes.mkdir(parents=True)

    if engine_source is None:
        shutil.copy2(ENGINE, mirror_engine)
    else:
        mirror_engine.write_text(engine_source)

    _write_change(changes, "gate-fixture", {
        **_BASE_ARTIFACTS,
        "tasks.md": _GATE_OK_TASKS,
    })
    return mirror


def _run_gate(mirror: Path, change: str = "gate-fixture"):
    return subprocess.run(
        ["bash", str(HOOK), change],
        cwd=str(mirror), capture_output=True, text=True,
    )


class TestGateFailsClosedOnEngineCrash:
    """B1. El gate no puede aprobar cuando no tiene veredicto del oráculo."""

    def test_healthy_engine_still_approves(self, tmp_path):
        """Control: con el engine sano y el change en regla, exit 0.

        Sin este control el test de abajo probaría muy poco — un gate que
        bloquea SIEMPRE también "falla cerrado", y sería inútil.
        """
        res = _run_gate(_build_mirror(tmp_path))
        assert res.returncode == 0, res.stdout + res.stderr
        assert "APROBADO" in res.stdout

    def test_engine_syntax_error_blocks_instead_of_approving(self, tmp_path):
        """LA reproducción de B1: engine con SyntaxError.

        Antes del fix esto imprimía "✅ SDD GATE APROBADO" y salía 0, porque
        `|| echo "true"` fabricaba el veredicto que el oráculo no pudo dar.
        Ahora bloquea. Un oráculo que no funciona no es un "sí" — es un "no
        sé", y un "no sé" no autoriza tocar código productivo.
        """
        mirror = _build_mirror(tmp_path, engine_source="def broken(:\n    pass\n")
        res = _run_gate(mirror)
        assert res.returncode == 1, (
            f"FAIL-OPEN: el gate salió {res.returncode} con el oráculo roto.\n"
            f"{res.stdout}"
        )
        assert "oráculo" in res.stdout
        assert "APROBADO" not in res.stdout

    def test_engine_traceback_is_visible_not_swallowed(self, tmp_path):
        """El `2>/dev/null` se comía el traceback: el operador veía un ✅ y
        ninguna pista de que el motor estaba roto. El stderr real tiene que
        llegar a la salida del gate."""
        mirror = _build_mirror(tmp_path, engine_source="def broken(:\n    pass\n")
        res = _run_gate(mirror)
        assert "SyntaxError" in res.stdout, res.stdout

    def test_engine_nonzero_exit_without_output_blocks(self, tmp_path):
        """Engine que muere en runtime (no en parseo) y no emite nada."""
        mirror = _build_mirror(
            tmp_path,
            engine_source="import sys\nsys.stderr.write('boom real\\n')\nsys.exit(3)\n",
        )
        res = _run_gate(mirror)
        assert res.returncode == 1
        assert "exit=3" in res.stdout
        assert "boom real" in res.stdout

    def test_engine_prints_garbage_blocks(self, tmp_path):
        """Exit 0 pero la salida no es JSON: tampoco hay veredicto."""
        mirror = _build_mirror(
            tmp_path, engine_source="print('no soy json, soy prosa')\n"
        )
        res = _run_gate(mirror)
        assert res.returncode == 1
        assert "no parseable" in res.stdout or "oráculo" in res.stdout

    def test_engine_json_without_apply_allowed_blocks(self, tmp_path):
        """JSON válido pero de otro schema: falta `applyAllowed` → fail-closed.
        Antes, cualquier salida no-"true"/"True" bloqueaba solo por accidente
        de comparación; ahora el schema se valida explícitamente."""
        mirror = _build_mirror(
            tmp_path, engine_source="print('{\"hola\": 1}')\n"
        )
        res = _run_gate(mirror)
        assert res.returncode == 1
        assert "APROBADO" not in res.stdout

    def test_engine_blocking_verdict_still_blocks_with_reasons(self, tmp_path):
        """No-regresión: el camino normal de bloqueo (applyAllowed=false) sigue
        mostrando nextRecommended + blockedReasons, no el mensaje de oráculo
        caído — hay que poder distinguir "el motor dice que no" de "el motor
        no dice nada"."""
        mirror = _build_mirror(
            tmp_path,
            engine_source=(
                'print(\'{"applyAllowed": false, "nextRecommended": "resolve-blockers",'
                ' "blockedReasons": ["razon de prueba"]}\')\n'
            ),
        )
        res = _run_gate(mirror)
        assert res.returncode == 1
        assert "razon de prueba" in res.stdout
        assert "nextRecommended=resolve-blockers" in res.stdout
        assert "oráculo" not in res.stdout


# ══════════════════════════════════════════════════════════════════════
# B2 — tasks.md ilegible: degradado explícito, nunca crash ni verde
# ══════════════════════════════════════════════════════════════════════

class TestUnreadableTasksFile:
    """B2. Se sigue el patrón `try/except OSError` que el módulo ya usa en
    `_is_valid_evidence_file` y en la lectura de verify de detect_blockers."""

    def test_task_progress_on_directory_does_not_raise(self, tmp_path):
        """tasks.md como DIRECTORIO → IsADirectoryError. Antes reventaba el
        motor entero; ahora devuelve estado degradado explícito."""
        tasks = tmp_path / "tasks.md"
        tasks.mkdir()
        progress = task_progress(tasks)
        assert progress["readError"] == "IsADirectoryError"
        assert progress["pending"] == 0
        assert progress["allComplete"] is False

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root ignora los permisos de archivo",
    )
    def test_task_progress_on_unreadable_file_does_not_raise(self, tmp_path):
        """tasks.md sin permisos de lectura → PermissionError."""
        tasks = tmp_path / "tasks.md"
        tasks.write_text("- [ ] T-1\n")
        tasks.chmod(0o000)
        try:
            progress = task_progress(tasks)
        finally:
            tasks.chmod(0o644)
        assert progress["readError"] == "PermissionError"
        assert progress["pending"] == 0

    def test_unreadable_tasks_produces_hard_blocker_not_green(self, tmp_path):
        """El contrato completo: ilegible ≠ vacío. `pending: 0` porque no se
        pudo CONTAR nada jamás puede leerse como "no queda trabajo"."""
        change_dir = _write_change(tmp_path, "unreadable-tasks", {
            **_BASE_ARTIFACTS,
            "apply-report.md": "apply done",
            "verify-report.md": _CLEAN_VERIFY,
        })
        (change_dir / "tasks.md").mkdir()

        original_base = _sdd_status.BASE
        _sdd_status.BASE = tmp_path
        try:
            result = _sdd_status.compute("unreadable-tasks")
        finally:
            _sdd_status.BASE = original_base

        assert result["applyAllowed"] is False
        assert result["nextRecommended"] == "resolve-blockers"
        assert result["dependencies"]["apply"] == "in_progress"
        assert any("no se pudo leer" in r for r in result["blockedReasons"]), (
            result["blockedReasons"]
        )

    def test_engine_cli_exits_zero_with_unreadable_tasks(self, tmp_path):
        """Cruce B2 × B1: el crash de B2 era el disparador de B1. El CLI del
        engine tiene que salir 0 con JSON degradado, no morir con traceback —
        si muriera, el gate (ahora fail-closed) bloquearía, pero el operador
        vería "oráculo caído" en vez del blocker real y accionable."""
        mirror = _build_mirror(tmp_path)
        change_dir = mirror / MIRROR_CHANGES_REL / "gate-fixture"
        (change_dir / "tasks.md").unlink()
        (change_dir / "tasks.md").mkdir()
        res = subprocess.run(
            ["python3", str(MIRROR_ENGINE_REL), "gate-fixture"],
            cwd=str(mirror), capture_output=True, text=True,
        )
        assert res.returncode == 0, res.stderr
        assert "IsADirectoryError" in res.stdout
        assert "Traceback" not in res.stderr

    def test_gate_blocks_change_with_unreadable_tasks(self, tmp_path):
        """E2E: el gate real, con el engine real, sobre un tasks.md ilegible.
        Bloquea — y por el motivo correcto."""
        mirror = _build_mirror(tmp_path)
        change_dir = mirror / MIRROR_CHANGES_REL / "gate-fixture"
        (change_dir / "tasks.md").unlink()
        (change_dir / "tasks.md").mkdir()
        res = _run_gate(mirror)
        assert res.returncode == 1
        assert "APROBADO" not in res.stdout


# ══════════════════════════════════════════════════════════════════════
# B3 — el ruteo no puede saltear una fase cuyo artefacto no existe
# ══════════════════════════════════════════════════════════════════════

class TestRoutingNeverSkipsAbsentPhase:
    """B3. Reproduce los 2 changes vivos atrapados en el deadlock."""

    def test_live_shape_save_unification(self, tmp_path):
        """Forma EXACTA de `app-save-unification` (15 pending) y de
        `app-privacy-first` (1 pending) al detectarlos:
        proposal + specs + design + tasks + verify-report, SIN apply-report.

        Antes: `verify` — para siempre, sin actor capaz de destrabarlo (las
        tasks solo se tildan aplicando). Ahora: `apply`.
        """
        result = _run_compute(tmp_path, "sin-apply-report", {
            **_BASE_ARTIFACTS,
            "tasks.md": "- [x] T-1\n" * 3 + "- [ ] T-2 pendiente\n",
            "verify-report.md": _CLEAN_VERIFY,
        })
        assert result["dependencies"]["apply"] == "ready"
        assert result["nextRecommended"] == "apply"

    def test_with_apply_report_still_routes_to_verify(self, tmp_path):
        """NO-REGRESIÓN del fix anterior del deadlock: CON apply-report en
        disco y solo criterios de verificación pendientes, el ruteo sigue
        llegando a `verify`. La guarda de B3 mira PRESENCIA, no `done`,
        justamente para no revertir esto (apply y verify se degradan juntas
        con el mismo `pending > 0`)."""
        result = _run_compute(tmp_path, "con-apply-report", {
            **_BASE_ARTIFACTS,
            "tasks.md": "- [x] T-1\n- [ ] VG-1 cipher + ojo humano\n",
            "apply-report.md": "apply done",
            "verify-report.md": _CLEAN_VERIFY,
        })
        assert result["dependencies"]["apply"] == "in_progress"
        assert result["nextRecommended"] == "verify"

    def test_suffixed_apply_report_counts_as_present(self, tmp_path):
        """El artefacto de apply puede ser multi-pasada (`apply-report-M0.md`).
        La guarda usa la misma resolución de candidatos que el resto del motor,
        así que un report sufijado cuenta y el ruteo llega a `verify`."""
        result = _run_compute(tmp_path, "apply-sufijado", {
            **_BASE_ARTIFACTS,
            "tasks.md": "- [x] T-1\n- [ ] VG-1 pendiente\n",
            "apply-report-M0.md": "apply pasada 0",
            "verify-report.md": _CLEAN_VERIFY,
        })
        assert result["nextRecommended"] == "verify"

    def test_missing_design_routes_back_to_design(self, tmp_path):
        """Generalización: la guarda no es sobre `apply` en particular — el
        salto hacia adelante nunca puede saltear NINGUNA fase ausente."""
        result = _run_compute(tmp_path, "sin-design", {
            "proposal.md": "p",
            "specs/core/spec.md": "s",
            "tasks.md": "- [x] T-1\n- [ ] T-2\n",
            "verify-report.md": _CLEAN_VERIFY,
        })
        assert result["nextRecommended"] == "design"

    def test_all_done_still_archives(self, tmp_path):
        """No-falso-rojo: sin tareas pendientes el gate no dispara y el ruteo
        sigue llegando a `archive` (forma de `client-listings-engine`, 23/23)."""
        result = _run_compute(tmp_path, "todo-hecho", {
            **_BASE_ARTIFACTS,
            "tasks.md": "- [x] T-1\n- [x] T-2\n",
            "apply-report.md": "apply done",
            "verify-report.md": _CLEAN_VERIFY,
        })
        assert result["nextRecommended"] == "archive"


# ══════════════════════════════════════════════════════════════════════
# B4 — N/A es una decisión trazable, no un escape barato
# ══════════════════════════════════════════════════════════════════════

_NA_ARCHIVABLE = {
    **_BASE_ARTIFACTS,
    "apply-report.md": "apply done",
    "verify-report.md": _CLEAN_VERIFY,
}


class TestNAGreenwashGate:
    """B4. Diseño: (a) el total incluye los N/A — el denominador no se encoge
    junto con el trabajo descartado; (b) cada N/A debe declarar POR QUÉ en la
    misma línea, y los que no lo hacen emiten blocker y vetan `archive`.

    Bloquea `archive` y NO `applyAllowed` a propósito: el premio del greenwash
    es declarar el change terminado, ahí es donde tiene que doler; y el actor
    que escribe la justificación en tasks.md es la propia hoja APPLY — un gate
    no puede bloquear su propia reparación.
    """

    def test_reported_fixture_no_longer_archives(self, tmp_path):
        """LA fixture del reporte: 3 tareas reales marcadas `[-]`.
        Antes: `next=archive`, `applyAllowed=True`, `blockedReasons: []`."""
        result = _run_compute(tmp_path, "greenwash-3na", {
            **_NA_ARCHIVABLE,
            "tasks.md": (
                "- [-] T-1 migrar el módulo\n"
                "- [-] T-2 escribir los tests\n"
                "- [-] T-3 smoke en prod\n"
            ),
        })
        assert result["taskProgress"]["notApplicable"] == 3
        assert result["taskProgress"]["notApplicableUnjustified"] == 3
        assert result["taskProgress"]["total"] == 3, "el total ya no esconde los N/A"
        assert result["nextRecommended"] != "archive"
        assert result["nextRecommended"] == "resolve-blockers"
        assert result["blockedReasons"], "el bloqueo no puede ser mudo"
        assert any("N/A" in r for r in result["blockedReasons"])

    def test_justified_na_can_archive(self, tmp_path):
        """La salida existe y es barata de tomar HONESTAMENTE: escribir la
        razón en la misma línea. Sin esta puerta el gate se bloquearía a sí
        mismo para siempre."""
        result = _run_compute(tmp_path, "na-justificado", {
            **_NA_ARCHIVABLE,
            "tasks.md": (
                "- [x] T-1 migrar el módulo\n"
                "- [-] T-2 escribir los tests — N/A: el módulo se eliminó en W20\n"
                "### [-] T-3 smoke en prod (no aplica: nunca se desplegó)\n"
            ),
        })
        assert result["taskProgress"]["notApplicable"] == 2
        assert result["taskProgress"]["notApplicableUnjustified"] == 0
        assert result["taskProgress"]["total"] == 3
        assert result["nextRecommended"] == "archive"

    def test_partially_justified_still_blocks(self, tmp_path):
        """Uno solo sin justificar alcanza — el gate cuenta tareas, no
        intenciones."""
        result = _run_compute(tmp_path, "na-parcial", {
            **_NA_ARCHIVABLE,
            "tasks.md": (
                "- [-] T-1 — N/A: fuera de scope desde el spec\n"
                "- [-] T-2 sin razón\n"
            ),
        })
        assert result["taskProgress"]["notApplicableUnjustified"] == 1
        assert result["nextRecommended"] != "archive"

    def test_na_does_not_block_apply(self, tmp_path):
        """`applyAllowed` sigue en True: la hoja APPLY es justamente quien
        puede ir a escribir la justificación o a hacer la tarea."""
        result = _run_compute(tmp_path, "na-apply-ok", {
            **_NA_ARCHIVABLE,
            "tasks.md": "- [-] T-1 sin razón\n",
        })
        assert result["applyAllowed"] is True

    def test_total_includes_na_so_progress_cannot_shrink(self, tmp_path):
        """El corazón de (a): tildar N/A no puede mejorar el ratio.
        3 hechas de 6 sigue siendo 3 de 6, aunque las otras 3 pasen a N/A."""
        honesto = tmp_path / "honesto.md"
        honesto.write_text("- [x] a\n- [x] b\n- [x] c\n- [ ] d\n- [ ] e\n- [ ] f\n")
        greenwash = tmp_path / "greenwash.md"
        greenwash.write_text("- [x] a\n- [x] b\n- [x] c\n- [-] d\n- [-] e\n- [-] f\n")
        assert task_progress(honesto)["total"] == 6
        assert task_progress(greenwash)["total"] == 6

    def test_blocked_reason_no_longer_advertises_the_shortcut(self, tmp_path):
        """El motor decía literal "o marcarlas [-] si no aplican" — le
        publicitaba el atajo al lector, en el momento de máxima presión y al
        actor que puede tomarlo. Ese texto no vuelve."""
        result = _run_compute(tmp_path, "sin-publicidad", {
            **_NA_ARCHIVABLE,
            "tasks.md": "- [x] T-1\n- [ ] T-2 pendiente\n",
        })
        joined = " ".join(result["blockedReasons"])
        assert "tasks pendientes" in joined
        assert "marcarlas [-]" not in joined
        assert "si no aplican" not in joined
