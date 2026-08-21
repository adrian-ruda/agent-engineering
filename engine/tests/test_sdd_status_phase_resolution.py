#!/usr/bin/env python3
"""
Tests for sdd_status.py mechanical fixes (2026-08-04):

  1. Phase presence resolution by pattern for multi-pass reports (apply-report-M0.md,
     apply-report-wu2.md, verify-report-tanda3.md, ...) instead of exact-name-only.
     UPDATED 2026-08-04 (round-3 redesign): the canonical filename no longer wins
     precedence alone — it is just one more candidate among the aggregate (see
     resolve_phase_candidates() docstring in sdd_status.py for why: three rounds
     of adversarial review found that "wins alone" precedence could hide a real
     blocker sitting in a suffixed sibling).
  2. Checkbox counting for heading-style tasks (### [x] T-1.1 — ...) in addition to
     list-style (- [x]), including mixed files and the "[—]"/"[-]" not-applicable
     marker (counted separately, not pending, not completed).

Fixtures live entirely under tmp_path — no real sdd/changes/* is touched.
"""
import importlib.util
from pathlib import Path

# sdd_status.py has a hyphen — can't use normal import
_spec = importlib.util.spec_from_file_location(
    "sdd_status_phase_resolution",
    Path(__file__).resolve().parent.parent / "sdd_status.py"
)
_sdd_status = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sdd_status)

resolve_phase_file = _sdd_status.resolve_phase_file
task_progress = _sdd_status.task_progress
compute = _sdd_status.compute


def _run_compute(tmp_path, change_name, artifacts):
    """Write artifacts into tmp_path, run compute() with BASE overridden."""
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


# ─── Layout de delta specs: adopción gentle-ai (2026-08-04) ─────────
#
# El delta vive en `specs/{capability}/spec.md` (walk recursivo, filename
# exacto), IGUAL que `findSpecFiles` del motor nativo. SIN fallback al
# `spec.md` plano en la raíz del change — adoptamos su layout, no lo
# mantenemos dual.

class TestSpecTreeLayout:

    def test_spec_in_capability_subdir_is_present(self, tmp_path):
        cd = tmp_path / "change-spec-tree"
        (cd / "specs" / "client-listings-engine").mkdir(parents=True)
        (cd / "specs" / "client-listings-engine" / "spec.md").write_text("# delta")
        assert resolve_phase_file(cd, "spec") is not None

    def test_spec_nested_deeper_is_present(self, tmp_path):
        """Walk recursivo: cualquier profundidad bajo specs/ cuenta."""
        cd = tmp_path / "change-deep"
        (cd / "specs" / "a" / "b").mkdir(parents=True)
        (cd / "specs" / "a" / "b" / "spec.md").write_text("# delta")
        assert resolve_phase_file(cd, "spec") is not None

    def test_all_capabilities_are_candidates(self, tmp_path):
        cd = tmp_path / "change-multi"
        for cap in ("cap-a", "cap-b"):
            (cd / "specs" / cap).mkdir(parents=True)
            (cd / "specs" / cap / "spec.md").write_text("# delta")
        got = _sdd_status.resolve_phase_candidates(cd, "spec")
        assert len(got) == 2

    def test_flat_spec_md_is_NOT_present(self, tmp_path):
        """Adopción sin compatibilidad dual: el layout viejo ya no cuenta."""
        cd = tmp_path / "change-flat"
        cd.mkdir()
        (cd / "spec.md").write_text("# delta plano (layout viejo)")
        assert resolve_phase_file(cd, "spec") is None

    def test_empty_spec_in_subdir_is_not_evidence(self, tmp_path):
        cd = tmp_path / "change-empty"
        (cd / "specs" / "cap").mkdir(parents=True)
        (cd / "specs" / "cap" / "spec.md").write_text("")
        assert resolve_phase_file(cd, "spec") is None

    def test_other_md_under_specs_is_not_spec_evidence(self, tmp_path):
        """Solo el filename exacto `spec.md` cuenta (igual que gentle)."""
        cd = tmp_path / "change-other"
        (cd / "specs" / "cap").mkdir(parents=True)
        (cd / "specs" / "cap" / "delta-spec.md").write_text("# no")
        (cd / "specs" / "cap" / "README.md").write_text("# no")
        assert resolve_phase_file(cd, "spec") is None

    def test_no_specs_dir_at_all(self, tmp_path):
        cd = tmp_path / "change-none"
        cd.mkdir()
        assert _sdd_status.resolve_phase_candidates(cd, "spec") == []


# ─── Defect 1: apply/verify presence by glob ────────────────────────

class TestResolvePhaseFileGlob:

    def test_apply_suffix_only_counts_as_present(self, tmp_path):
        """apply-report-M0.md + apply-report-wu2.md, no canonical apply-report.md
        → phase 'apply' must resolve as present (this is the exact multi-tenant
        bots scenario: 8 suffixed reports, engine said 'ready' i.e. absent)."""
        cd = tmp_path / "change-a"
        cd.mkdir()
        (cd / "apply-report-M0.md").write_text("apply pass 0 done")
        (cd / "apply-report-M1-tanda1.md").write_text("apply pass 1 tanda 1 done")
        found = resolve_phase_file(cd, "apply")
        assert found is not None
        assert found.name in ("apply-report-M0.md", "apply-report-M1-tanda1.md")

    def test_apply_no_file_at_all_is_absent(self, tmp_path):
        cd = tmp_path / "change-b"
        cd.mkdir()
        assert resolve_phase_file(cd, "apply") is None

    def test_apply_empty_suffixed_file_does_not_count(self, tmp_path):
        """An empty suffixed report must NOT count as evidence — same nonempty()
        rule that already applies to the canonical name."""
        cd = tmp_path / "change-c"
        cd.mkdir()
        (cd / "apply-report-wu1.md").write_text("")
        assert resolve_phase_file(cd, "apply") is None

    def test_canonical_and_suffixed_both_count_no_precedence(self, tmp_path):
        """Round-3 redesign (H-B): there is no more "canonical wins alone"
        precedence — when both the canonical file and suffixed files exist,
        BOTH are valid candidates (pure aggregation, see
        resolve_phase_candidates() docstring). resolve_phase_file() (single
        pick for presence-only checks) may return either, but the phase must
        resolve present either way."""
        cd = tmp_path / "change-d"
        cd.mkdir()
        (cd / "apply-report.md").write_text("canonical apply report")
        (cd / "apply-report-M0.md").write_text("old pass")
        found = resolve_phase_file(cd, "apply")
        assert found is not None
        assert found.name in ("apply-report.md", "apply-report-M0.md")

    def test_verify_suffix_only_counts_as_present(self, tmp_path):
        cd = tmp_path / "change-e"
        cd.mkdir()
        (cd / "verify-report-tanda3.md").write_text("verify pass 3 done")
        found = resolve_phase_file(cd, "verify")
        assert found is not None
        assert found.name == "verify-report-tanda3.md"

    def test_non_glob_phases_unaffected_by_suffix(self, tmp_path):
        """spec/design/tasks/proposal/archive are NOT in PHASE_GLOB_FALLBACK —
        a suffixed variant must NOT make them count as present."""
        cd = tmp_path / "change-f"
        cd.mkdir()
        (cd / "spec-v2.md").write_text("not the canonical spec")
        assert resolve_phase_file(cd, "spec") is None

    def test_compute_end_to_end_apply_suffix_only(self, tmp_path):
        """Full compute(): a change with only a suffixed apply-report must show
        dependencies.apply == 'all_done', not 'ready'."""
        result = _run_compute(tmp_path, "change-g", {
            "proposal.md": "p",
            "specs/core/spec.md": "s",
            "design.md": "d",
            "tasks.md": "- [x] t1\n",
            "apply-report-wu1.md": "apply wu1 done",
            "apply-report-wu2.md": "apply wu2 done",
        })
        assert result["dependencies"]["apply"] == "all_done", result["dependencies"]


# ─── FALSO VERDE: presencia del report ≠ fase terminada ─────────────
#
# Found 2026-08-04 against `client-site-v1` (a change in flight at the time):
# `dependencies` se calculaba SOLO por existencia de archivo, y `taskProgress`
# se computaba DESPUÉS sin alimentar nada. Resultado real, en el MISMO JSON:
#   "dependencies": { "apply": "all_done" }
#   "taskProgress": { "total": 88, "completed": 31, "pending": 57,
#                     "allComplete": false }
# Un change con 31 de 88 tareas hechas no puede reportar la fase apply como
# terminada, ni rutear a la fase siguiente. El motor nativo de referencia
# (gentle-ai) sobre el mismo change decía applyState "blocked".

class TestFalsoVerdeApplyVsTasks:

    _BASE = {
        "proposal.md": "p",
        "specs/core/spec.md": "s",
        "design.md": "d",
    }

    def test_apply_report_with_pending_tasks_is_not_all_done(self, tmp_path):
        """El bug exacto: apply-report.md presente + tareas pendientes."""
        result = _run_compute(tmp_path, "change-fv-a", {
            **self._BASE,
            "tasks.md": "- [x] t1\n- [ ] t2\n- [ ] t3\n",
            "apply-report.md": "apply parcial, 1 de 3",
        })
        assert result["taskProgress"]["pending"] == 2
        assert result["dependencies"]["apply"] == "in_progress", result["dependencies"]
        assert result["dependencies"]["apply"] != "all_done"

    def test_pending_tasks_route_back_to_apply_not_forward(self, tmp_path):
        """Coherencia del token de ruteo: si apply no está terminada,
        nextRecommended NO puede saltar a verify (era el falso verde vivo de
        `client-site-v1`: 57 pendientes y ruteaba a 'verify')."""
        result = _run_compute(tmp_path, "change-fv-b", {
            **self._BASE,
            "tasks.md": "- [x] t1\n- [ ] t2\n",
            "apply-report.md": "apply parcial",
        })
        assert result["nextRecommended"] == "apply", result["nextRecommended"]

    def test_verify_report_with_pending_tasks_is_not_all_done(self, tmp_path):
        """Punto 2: `verify` sufre el mismo patrón — un verify-report sobre
        trabajo inconcluso tampoco es una fase terminada."""
        result = _run_compute(tmp_path, "change-fv-c", {
            **self._BASE,
            "tasks.md": "- [x] t1\n- [ ] t2\n",
            "apply-report.md": "apply parcial",
            "verify-report.md": "# Verify\nStatus: success\n",
        })
        assert result["dependencies"]["verify"] == "in_progress", result["dependencies"]
        assert result["nextRecommended"] != "archive"

    def test_all_tasks_done_still_reports_all_done(self, tmp_path):
        """No-falso-rojo: con todas las tareas hechas, el caso bueno sigue
        intacto (all_done + ruteo hacia adelante)."""
        result = _run_compute(tmp_path, "change-fv-d", {
            **self._BASE,
            "tasks.md": "- [x] t1\n- [x] t2\n",
            "apply-report.md": "apply done",
        })
        assert result["dependencies"]["apply"] == "all_done", result["dependencies"]
        assert result["nextRecommended"] == "verify"

    def test_notapplicable_tasks_do_not_block_all_done(self, tmp_path):
        """Una tarea marcada `[—]` (no aplica) no es trabajo pendiente — no
        debe degradar la fase.

        ACTUALIZADO 2026-08-05 (B4): la fixture ahora lleva la justificación
        inline que el motor exige (`— N/A: <razón>`). El punto del test sigue
        siendo el mismo (N/A no degrada la fase); lo que cambió es que marcar
        N/A dejó de ser gratis — sin razón escrita el motor emite blocker y
        veta `archive`. Ver `test_change_without_parseable_tasks_is_not_degraded`
        y la clase TestNAGreenwashGate para el otro lado del contrato.
        """
        result = _run_compute(tmp_path, "change-fv-e", {
            **self._BASE,
            "tasks.md": "- [x] t1\n- [—] t2 — N/A: el módulo se borró en W20\n",
            "apply-report.md": "apply done",
        })
        assert result["taskProgress"]["pending"] == 0
        assert result["taskProgress"]["notApplicableUnjustified"] == 0
        assert result["dependencies"]["apply"] == "all_done", result["dependencies"]

    def test_change_with_unreadable_tasks_is_degraded_not_green(self, tmp_path):
        """CORREGIDO 2026-08-05 (B2, auditoría adversarial). Antes se llamaba
        `test_change_without_parseable_tasks_is_not_degraded` y usaba un
        tasks.md de prosa; el nombre y el docstring generalizaban de más
        ("sin checkboxes parseables → no degradar"), y bajo ese paraguas
        quedaba certificado que un tasks.md ILEGIBLE tampoco degradaba.

        Por qué cambió — NO es una regresión:
        `task_progress()` leía tasks.md a pelo. Un tasks.md que es un
        DIRECTORIO levanta IsADirectoryError; sin permisos, PermissionError.
        Eso reventaba el motor entero (exit 1) y — con el gate fallando en
        ABIERTO (B1) — terminaba APROBANDO el apply. Y aun sin crash, tratar
        `pending: 0` de un archivo que no se pudo leer como "no queda trabajo"
        es exactamente el falso verde que este motor existe para matar.

        Contrato correcto: ilegible ≠ vacío. `pending: 0` porque no se pudo
        contar NADA debe producir estado DEGRADADO EXPLÍCITO — `readError`
        poblado, blockedReason claro, fase gateada NO `all_done`, y
        `applyAllowed` en false.

        El caso legítimo (prosa sin checkboxes, archivo perfectamente legible)
        sigue cubierto SIN degradar en
        `test_change_with_prose_only_tasks_is_not_degraded`, abajo.
        """
        change_dir = tmp_path / "change-fv-f2"
        change_dir.mkdir(parents=True)
        for name, content in self._BASE.items():
            target = change_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        (change_dir / "apply-report.md").write_text("apply done")
        # tasks.md es un DIRECTORIO → IsADirectoryError al leerlo.
        (change_dir / "tasks.md").mkdir()

        original_base = _sdd_status.BASE
        _sdd_status.BASE = tmp_path
        try:
            result = _sdd_status.compute("change-fv-f2")
        finally:
            _sdd_status.BASE = original_base

        assert result["taskProgress"]["readError"] == "IsADirectoryError"
        assert result["dependencies"]["apply"] == "in_progress", result["dependencies"]
        assert result["applyAllowed"] is False
        assert result["nextRecommended"] != "archive"
        assert any("no se pudo leer" in r for r in result["blockedReasons"]), (
            result["blockedReasons"]
        )

    def test_change_with_prose_only_tasks_is_not_degraded(self, tmp_path):
        """No-falso-rojo (mitad legítima del test viejo): el gate es por
        `pending > 0`, NO por `allComplete`. Un tasks.md LEGIBLE pero sin
        checkboxes parseables da allComplete=False con pending=0 — ahí no hay
        evidencia de trabajo sin hacer y degradar la fase sería inventar un
        rojo. `readError` es None: el archivo se leyó perfectamente."""
        result = _run_compute(tmp_path, "change-fv-f", {
            **self._BASE,
            "tasks.md": "# Tasks\n\nSolo prosa, sin checkboxes.\n",
            "apply-report.md": "apply done",
        })
        assert result["taskProgress"]["pending"] == 0
        assert result["taskProgress"]["allComplete"] is False
        assert result["taskProgress"]["readError"] is None
        assert result["dependencies"]["apply"] == "all_done", result["dependencies"]

    def test_unmarked_heading_tasks_also_degrade_apply(self, tmp_path):
        """Cruce con el defecto 3: headings de tarea SIN corchete cuentan como
        pendientes y por lo tanto también impiden que apply sea all_done."""
        result = _run_compute(tmp_path, "change-fv-g", {
            **self._BASE,
            "tasks.md": (
                "### [x] T-1 — hecha\n"
                "### T-2 — sin corchete, trabajo real sin hacer\n"
            ),
            "apply-report.md": "apply done (mentira)",
        })
        assert result["taskProgress"]["pending"] == 1
        assert result["dependencies"]["apply"] == "in_progress", result["dependencies"]

    def test_field_contract_for_hooks_unchanged(self, tmp_path):
        """Punto 4: los campos que consumen los hooks siguen existiendo con el
        mismo nombre y tipo (applyAllowed bool, nextRecommended str,
        blockedReasons list) aun con la fase degradada.
        applyAllowed sigue en True: quedan tareas → hay que SEGUIR aplicando,
        no bloquear el apply."""
        result = _run_compute(tmp_path, "change-fv-h", {
            **self._BASE,
            "tasks.md": "- [x] t1\n- [ ] t2\n",
            "apply-report.md": "apply parcial",
        })
        assert isinstance(result["applyAllowed"], bool)
        assert result["applyAllowed"] is True
        assert isinstance(result["nextRecommended"], str)
        assert isinstance(result["blockedReasons"], list)
        assert set(result["dependencies"]) == set(_sdd_status.PHASES)


# ─── Defect 2: heading-style checkbox counting ──────────────────────

class TestTaskProgressHeadings:

    def test_heading_style_checkboxes_counted(self, tmp_path):
        """Exact hmac-edge-verification scenario: tasks written as
        '### [x] T-1.1 — ...' headings, not list items."""
        f = tmp_path / "tasks.md"
        f.write_text(
            "### [x] T-1.1 — Crear proyecto\n"
            "### [x] T-1.2 — Configurar entorno\n"
            "### [ ] T-1.3 — Deploy\n"
            "#### [ ] T-1.3.1 — Subtask\n"
        )
        progress = task_progress(f)
        assert progress["total"] == 4
        assert progress["completed"] == 2
        assert progress["pending"] == 2
        assert progress["allComplete"] is False

    def test_heading_style_all_complete(self, tmp_path):
        f = tmp_path / "tasks.md"
        f.write_text(
            "### [x] T-1 — one\n"
            "### [x] T-2 — two\n"
        )
        progress = task_progress(f)
        assert progress["total"] == 2
        assert progress["completed"] == 2
        assert progress["pending"] == 0
        assert progress["allComplete"] is True

    def test_mixed_headings_and_list_items_sum_correctly(self, tmp_path):
        """Some tasks written as list items, others as headings — both must be
        counted in the same taskProgress total."""
        f = tmp_path / "tasks.md"
        f.write_text(
            "- [x] list task done\n"
            "- [ ] list task pending\n"
            "### [x] T-1 — heading done\n"
            "### [ ] T-2 — heading pending\n"
            "### [ ] T-3 — heading pending 2\n"
        )
        progress = task_progress(f)
        assert progress["total"] == 5
        assert progress["completed"] == 2
        assert progress["pending"] == 3

    def test_not_applicable_marker_not_pending_not_completed(self, tmp_path):
        """'[—]' / '[-]' marks a task as not-applicable: it must not count as
        pending (would falsely block allComplete) nor as completed (would
        falsely inflate progress) — reported separately in notApplicable.

        CORREGIDO 2026-08-05 (B4, auditoría adversarial). Antes afirmaba
        `progress["total"] == 1  # total excludes notApplicable`.

        Por qué cambió — NO es una regresión, no lo revuelvan:
        excluir los N/A del total era el greenwash de un tecleo. Con 3 tareas
        reales, convertir `- [ ]` en `- [-]` llevaba el progreso de 3/6 a 3/3
        — el denominador se encogía junto con el trabajo descartado, así que
        el número que todos miran no delataba nada. El total ahora incluye los
        N/A: descartar trabajo sigue siendo legítimo (`allComplete` sigue
        dependiendo solo de `pending`), pero deja de ser INVISIBLE.
        """
        f = tmp_path / "tasks.md"
        f.write_text(
            "- [x] task done\n"
            "- [—] task not applicable (em dash)\n"
            "### [-] T-2 — not applicable (hyphen)\n"
        )
        progress = task_progress(f)
        assert progress["completed"] == 1
        assert progress["pending"] == 0
        assert progress["notApplicable"] == 2
        assert progress["total"] == 3  # total INCLUDES notApplicable (B4)
        assert progress["allComplete"] is True

    def test_no_checkboxes_at_all(self, tmp_path):
        f = tmp_path / "tasks.md"
        f.write_text("# Tasks\n\nJust prose, no checkboxes.\n")
        progress = task_progress(f)
        # ACTUALIZADO 2026-08-05 (B2 + B4): el schema de taskProgress suma dos
        # campos aditivos — `notApplicableUnjustified` (gate anti-greenwash) y
        # `readError` (estado degradado explícito cuando tasks.md es ilegible).
        assert progress == {
            "total": 0,
            "completed": 0,
            "pending": 0,
            "notApplicable": 0,
            "notApplicableUnjustified": 0,
            "allComplete": False,
            "readError": None,
        }

    def test_missing_file_returns_zeroed_progress(self, tmp_path):
        progress = task_progress(tmp_path / "does-not-exist.md")
        assert progress["total"] == 0
        assert progress["notApplicable"] == 0
        assert progress["allComplete"] is False

    def test_compute_end_to_end_heading_tasks(self, tmp_path):
        """Full compute(): a tasks.md written entirely in heading style must
        produce a non-zero taskProgress (regression guard for the
        hmac-edge-verification 0/32 bug)."""
        result = _run_compute(tmp_path, "change-h", {
            "proposal.md": "p",
            "specs/core/spec.md": "s",
            "design.md": "d",
            "tasks.md": (
                "### [x] T-1 — one\n"
                "### [x] T-2 — two\n"
                "### [ ] T-3 — three\n"
            ),
        })
        assert result["taskProgress"]["total"] == 3
        assert result["taskProgress"]["completed"] == 2
        assert result["taskProgress"]["pending"] == 1


# ─── Defect 3: heading task WITHOUT any checkbox bracket → pending ──
#
# Found 2026-08-04 against `hmac-edge-verification` (archived):
# 32 task headings, 19 with [x], 1 with [—], 12 with NO bracket at all.
# The engine dropped those 12 from the count entirely instead of treating
# them as pending — reporting allComplete: true with 12 real tasks undone.

class TestTaskProgressHeadingWithoutCheckbox:

    def test_mixed_marked_and_unmarked_headings_count_unmarked_as_pending(self, tmp_path):
        """Sibling headings of the same task-id pattern, some with brackets and
        some without, in a doc that DOES use the checkbox-in-heading pattern
        (>=1 bracketed heading exists) — unbracketed ones must count pending."""
        f = tmp_path / "tasks.md"
        f.write_text(
            "### [x] T-1.1 — done task\n"
            "### T-1.2 — no bracket at all, real work not done\n"
            "### [—] T-1.3 — not applicable\n"
            "### T-1.4 — also no bracket\n"
        )
        progress = task_progress(f)
        assert progress["completed"] == 1
        assert progress["notApplicable"] == 1
        assert progress["pending"] == 2  # T-1.2 + T-1.4
        # ACTUALIZADO 2026-08-05 (B4): total = completed + pending + NA.
        assert progress["total"] == 4
        assert progress["allComplete"] is False

    def test_real_whatsapp_hmac_cloudflare_shape_via_fixture(self, tmp_path):
        """Reproduce the exact reported shape (19 done / 1 NA / 12 unmarked)
        using a compact fixture — same task-id vocabulary, same absence of
        brackets on the pending ones."""
        f = tmp_path / "tasks.md"
        lines = []
        for i in range(1, 20):
            lines.append(f"### [x] T-{i} — done\n")
        lines.append("### [—] T-20 — not applicable\n")
        for i in range(21, 33):
            lines.append(f"### T-{i} — pending, no bracket\n")
        f.write_text("".join(lines))
        progress = task_progress(f)
        assert progress["completed"] == 19
        assert progress["notApplicable"] == 1
        assert progress["pending"] == 12
        # ACTUALIZADO 2026-08-05 (B4): total = 19 done + 12 pending + 1 NA.
        assert progress["total"] == 32
        assert progress["allComplete"] is False

    def test_doc_without_any_heading_checkbox_pattern_does_not_invent_tasks(self, tmp_path):
        """Guard 1: if the doc never uses '[x]'/'[ ]'/'[—]' in a heading, a
        heading that happens to look like a task id must NOT be invented as a
        pending task — the pattern was never established in this doc."""
        f = tmp_path / "tasks.md"
        f.write_text(
            "# Tasks\n"
            "### T-1 — this looks like a task id but doc has no checkbox pattern\n"
            "### T-2 — same here\n"
            "- [x] but this list item IS a real completed task\n"
        )
        progress = task_progress(f)
        # The list-item checkbox still counts (unaffected by guard 1).
        assert progress["completed"] == 1
        # The two bare headings must NOT be invented as pending — no heading
        # in this doc ever used a bracket, so the pattern was never
        # established.
        assert progress["pending"] == 0
        assert progress["total"] == 1

    def test_prose_headings_never_count_even_with_pattern_established(self, tmp_path):
        """Guard 2: headings that don't match the task-id vocabulary (Guards,
        Traceability, Sección N, Caso A, ...) must never count as pending
        tasks, even in a doc that DOES use the checkbox-in-heading pattern."""
        f = tmp_path / "tasks.md"
        f.write_text(
            "### [x] T-1.1 — real done task\n"
            "## Guards (hook pre-apply)\n"
            "## Traceability: Tasks → Spec Requirements\n"
            "## Sección 1 — Pre-conditions (owner: tech lead)\n"
            "### Caso A: Worker roto post-deploy\n"
            "### T-1.2 — real pending task, no bracket\n"
        )
        progress = task_progress(f)
        assert progress["completed"] == 1
        assert progress["pending"] == 1  # only T-1.2, none of the prose headings
        assert progress["total"] == 2

    def test_task_id_variants_ta_wu_m_prefixed(self, tmp_path):
        """The repo's task-id vocabulary includes TA-<n>, WU-<n>, M<n>-T<n> in
        addition to T-<n>[.<n>] — all must be recognized when unbracketed."""
        f = tmp_path / "tasks.md"
        f.write_text(
            "### [x] TA-01 — done\n"
            "### TA-02 — pending, no bracket\n"
            "### WU-3 — pending, no bracket\n"
            "### M1-T9 — pending, no bracket\n"
        )
        progress = task_progress(f)
        assert progress["completed"] == 1
        assert progress["pending"] == 3
        assert progress["total"] == 4

    def test_compute_end_to_end_real_case_shape(self, tmp_path):
        """Full compute(): the exact hmac-edge-verification shape must NOT
        report allComplete: true — that was the false-green this fix targets."""
        lines = []
        for i in range(1, 20):
            lines.append(f"### [x] T-{i} — done\n")
        lines.append("### [—] T-20 — not applicable\n")
        for i in range(21, 33):
            lines.append(f"### T-{i} — pending, no bracket\n")
        result = _run_compute(tmp_path, "change-i", {
            "proposal.md": "p",
            "specs/core/spec.md": "s",
            "design.md": "d",
            "tasks.md": "".join(lines),
        })
        assert result["taskProgress"]["allComplete"] is False
        assert result["taskProgress"]["pending"] == 12
        # ACTUALIZADO 2026-08-05 (B4): total = 19 done + 12 pending + 1 NA.
        assert result["taskProgress"]["total"] == 32
