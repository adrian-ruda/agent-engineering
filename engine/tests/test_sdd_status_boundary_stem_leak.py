#!/usr/bin/env python3
"""
Regression test for the STEM-BOUNDARY LEAK found by adversarial review on
the ROUND-3 REDESIGN (2026-08-04) of `resolve_phase_candidates()`.

Defect (REJECT, reproduced): the filter used
`name_l.startswith(stem_l)` where `stem_l` is `"verify-report"` /
`"apply-report"`, WITHOUT requiring the next character to be the `-`
separator. Any file whose name merely starts with those 13 letters counted
as real phase evidence — e.g. `verify-reporting-notes.md` (loose notes,
Spanish repo, plausible without malice) was accepted as `verify` phase
evidence, and `apply-reporte-cliente.md` was accepted as `apply` phase
evidence. None of the 269 pre-existing tests caught this because they only
ever used the canonical name or `<stem>-<suffix>` names — never a name that
starts with the stem but is NOT followed by `-`.

Concrete reviewer repro: a change with tasks.md containing one done task
and one real pending task, plus ONLY `apply-reporting-ideas.md` and
`verify-reporting-notes.md` (loose notes, no real reports) produced
`applyAllowed: true` / `nextRecommended: "archive"` with a pending task —
the exact false-green class the previous three rounds were fixing.

Fix: a candidate counts only if `name_l == canonical_l` (exact canonical,
case-insensitive — unchanged) OR `name_l.startswith(stem_l + "-")`
(canonical stem followed by the `-` separator, e.g.
`verify-report-tanda3.md`). Nothing else in `resolve_phase_candidates()` or
the rest of the round-3 redesign (pure aggregation, no discard-by-name, no
canonical-wins-alone precedence, `_is_valid_evidence_file()`) changed.

Fixtures live entirely under tmp_path — no real sdd/changes/* is
touched.
"""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "sdd_status_boundary_stem_leak",
    Path(__file__).resolve().parent.parent / "sdd_status.py"
)
_sdd_status = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sdd_status)

resolve_phase_candidates = _sdd_status.resolve_phase_candidates
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


BASE_ARTIFACTS_NO_APPLY_NO_VERIFY = {
    "proposal.md": "p",
    "specs/core/spec.md": "s",
    "design.md": "d",
}

CLEAN_VERIFY = (
    "# Verify\nStatus: success\n"
    "- **CRITICAL:** 0\n"
    "**4R Verdict:** PASSED\n"
    "**Bloqueantes 4R:** []\n"
)


class TestReviewerExactRepro:
    """The exact end-to-end repro from the adversarial reviewer: a change
    with one real pending task and ONLY loose "-ing" notes files (no real
    apply-report.md / verify-report.md) must never reach `archive`."""

    def test_reviewer_repro_never_archives_with_pending_task(self, tmp_path):
        result = _run_compute(tmp_path, "change-reviewer-repro", {
            **BASE_ARTIFACTS_NO_APPLY_NO_VERIFY,
            "tasks.md": "- [x] T-1 done\n- [ ] T-2 pendiente de verdad\n",
            "apply-reporting-ideas.md": "notas sueltas, no es un report real",
            "verify-reporting-notes.md": "notas sueltas, no es un report real",
        })
        assert result["taskProgress"]["pending"] == 1
        assert result["taskProgress"]["allComplete"] is False
        assert result["nextRecommended"] != "archive"


class TestLooseNotesFileNotCountedAsEvidence:
    """A single `<stem>ing-...` file with no `-` boundary after the stem
    must NOT be treated as phase evidence, standalone."""

    def test_verify_reporting_notes_alone_is_not_verify_evidence(self, tmp_path):
        cd = tmp_path / "change-verify-notes-only"
        cd.mkdir()
        (cd / "verify-reporting-notes.md").write_text("notas sueltas")
        candidates = resolve_phase_candidates(cd, "verify")
        assert candidates == []

    def test_verify_reporting_notes_alone_dependencies_not_all_done(self, tmp_path):
        result = _run_compute(tmp_path, "change-verify-notes-deps", {
            **BASE_ARTIFACTS_NO_APPLY_NO_VERIFY,
            "tasks.md": "- [x] T-1 done\n",
            "apply-report.md": "apply done",
            "verify-reporting-notes.md": "notas sueltas",
        })
        assert result["dependencies"]["verify"] != "all_done"

    def test_apply_reporte_cliente_alone_is_not_apply_evidence(self, tmp_path):
        cd = tmp_path / "change-apply-notes-only"
        cd.mkdir()
        (cd / "apply-reporte-cliente.md").write_text("reporte para el cliente, no es evidencia")
        candidates = resolve_phase_candidates(cd, "apply")
        assert candidates == []

    def test_apply_reporte_cliente_alone_dependencies_not_all_done(self, tmp_path):
        result = _run_compute(tmp_path, "change-apply-notes-deps", {
            **BASE_ARTIFACTS_NO_APPLY_NO_VERIFY,
            "tasks.md": "- [x] T-1 done\n",
            "apply-reporte-cliente.md": "reporte para el cliente, no es evidencia",
        })
        assert result["dependencies"]["apply"] != "all_done"


class TestNoRegressionRealEvidenceStillCounts:
    """Every previously-accepted evidence shape must still be accepted:
    canonical exact name, `<stem>-<suffix>` suffixed name (any suffix),
    case-insensitive canonical, and a suffixed blocked report still blocks.
    """

    def test_canonical_verify_report_still_counts(self, tmp_path):
        cd = tmp_path / "change-canonical"
        cd.mkdir()
        (cd / "verify-report.md").write_text(CLEAN_VERIFY)
        candidates = resolve_phase_candidates(cd, "verify")
        assert [c.name for c in candidates] == ["verify-report.md"]

    def test_suffixed_verify_report_tanda3_still_counts(self, tmp_path):
        cd = tmp_path / "change-suffixed"
        cd.mkdir()
        (cd / "verify-report-tanda3.md").write_text(CLEAN_VERIFY)
        candidates = resolve_phase_candidates(cd, "verify")
        assert [c.name for c in candidates] == ["verify-report-tanda3.md"]

    def test_uppercase_canonical_verify_report_still_counts(self, tmp_path):
        cd = tmp_path / "change-uppercase"
        cd.mkdir()
        (cd / "VERIFY-REPORT.MD").write_text(CLEAN_VERIFY)
        candidates = resolve_phase_candidates(cd, "verify")
        assert [c.name for c in candidates] == ["VERIFY-REPORT.MD"]

    def test_verify_report_old_real_still_counts_and_blocks(self, tmp_path):
        result = _run_compute(tmp_path, "change-old-real", {
            **BASE_ARTIFACTS_NO_APPLY_NO_VERIFY,
            "tasks.md": "- [x] T-1 done\n",
            "apply-report.md": "apply done",
            "verify-report-old-real.md": "# Verify\nStatus: blocked\n",
        })
        assert result["blockedReasons"] != [], result["blockedReasons"]
        assert any("verify-report-old-real.md" in b for b in result["blockedReasons"])
        assert result["nextRecommended"] != "archive"
