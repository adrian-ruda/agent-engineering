#!/usr/bin/env python3
"""
Tests for the 3 findings from the adversarial fresh-context reviewer pass
(2026-08-04) on the sdd_status.py multi-defect fix (apply/verify glob
resolution + heading checkbox counting):

  R1-001 (BLOCKER) — detect_blockers() read `verify-report.md` by hardcoded
    name while `present["verify"]` resolved by glob via resolve_phase_file().
    A change with ONLY a suffixed verify-report-<x>.md whose content signals
    FAILED/blocked/CRITICAL was invisible to detect_blockers(): present=True,
    blockedReasons=[] → nextRecommended could reach "archive" with a real
    verify failure hidden. Fix: detect_blockers() now resolves verify-report
    through the SAME resolve_phase_file() used for presence.

  R1-002 (CRITICAL) — _CHECKBOX_RE's marker class `[xX \\-—]` didn't include
    real repo markers like `[~]` (ROLLED BACK, see
    archive/2026-05-21-security-hardening/tasks.md). Because `[~]` HAS a
    bracket, the defect-3 "no bracket" guard also excluded it — the task
    vanished from the count entirely, reporting allComplete:true over
    reverted/unfinished work. Fix: inverted the logic — ANY bracket counts;
    only "x"/"X" → completed and "-"/"—" → notApplicable are recognized as
    non-pending; everything else (blank or unknown marker) → pending.

  R1-003 (PREFER, REMOVED 2026-08-04 round-3) — resolve_phase_file()'s glob
    fallback validated only prefix + non-empty, so a discarded note
    (`apply-report-old.md`, `-bak`, `-borrador`, `-draft`, `-wip`) would
    count as phase evidence. Original fix excluded candidates carrying an
    evident discard marker — but that same exclusion is what LATER caused
    H-A/H-D (a legitimately blocked `-old-real.md` report becoming
    invisible). The round-3 redesign removed the discard-by-name heuristic
    entirely: filenames no longer decide what counts as evidence, only
    `_is_valid_evidence_file()` (real, non-empty, readable file) does.
    `TestR1003DiscardSuffixExcluded` was deleted below; see
    resolve_phase_candidates() docstring in sdd_status.py and
    test_sdd_status_round3_redesign.py for the new behavior.

Fixtures live entirely under tmp_path — no real sdd/changes/* is
touched.
"""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "sdd_status_reviewer_findings",
    Path(__file__).resolve().parent.parent / "sdd_status.py"
)
_sdd_status = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sdd_status)

resolve_phase_file = _sdd_status.resolve_phase_file
detect_blockers = _sdd_status.detect_blockers
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


# ─── R1-001: presence and content-parsing must resolve the SAME file ────

class TestR1001VerifyContentMatchesPresence:

    def test_suffixed_verify_report_status_blocked_is_not_archived(self, tmp_path):
        """Mandatory reviewer test: a change whose ONLY verify evidence is a
        suffixed verify-report-tanda1.md with Status: blocked must NOT be
        routed to archive with empty blockedReasons — that would be the
        greenwash path R1-001 flags."""
        result = _run_compute(tmp_path, "change-r1001-a", {
            "proposal.md": "p",
            "specs/core/spec.md": "s",
            "design.md": "d",
            "tasks.md": "- [x] t1\n",
            "apply-report.md": "apply done",
            "verify-report-tanda1.md": "# Verify\nStatus: blocked\n",
        })
        assert result["dependencies"]["verify"] == "all_done", result["dependencies"]
        assert result["blockedReasons"] != [], "verify FAILED content was not detected"
        assert result["nextRecommended"] != "archive"

    def test_suffixed_verify_report_4r_failed_is_not_archived(self, tmp_path):
        result = _run_compute(tmp_path, "change-r1001-b", {
            "proposal.md": "p",
            "specs/core/spec.md": "s",
            "design.md": "d",
            "tasks.md": "- [x] t1\n",
            "apply-report.md": "apply done",
            "verify-report-wu2.md": (
                "# Verify\nStatus: success\n"
                "**4R Verdict:** FAILED\n"
                "**Bloqueantes 4R:** [\"R1-002\"]\n"
            ),
        })
        assert result["blockedReasons"] != []
        assert result["nextRecommended"] != "archive"

    def test_suffixed_verify_report_critical_findings_is_not_archived(self, tmp_path):
        result = _run_compute(tmp_path, "change-r1001-c", {
            "proposal.md": "p",
            "specs/core/spec.md": "s",
            "design.md": "d",
            "tasks.md": "- [x] t1\n",
            "apply-report.md": "apply done",
            "verify-report-tanda3.md": (
                "# Verify\n- **CRITICAL:** 3\n"
            ),
        })
        assert result["blockedReasons"] != []
        assert result["nextRecommended"] != "archive"

    def test_suffixed_verify_report_clean_content_still_archives(self, tmp_path):
        """Sanity: a suffixed-only verify-report that IS clean must still
        allow archive — R1-001's fix must not become a new false blocker."""
        result = _run_compute(tmp_path, "change-r1001-clean", {
            "proposal.md": "p",
            "specs/core/spec.md": "s",
            "design.md": "d",
            "tasks.md": "- [x] t1\n",
            "apply-report.md": "apply done",
            "verify-report-tanda1.md": (
                "# Verify\nStatus: success\n"
                "- **CRITICAL:** 0\n"
                "**4R Verdict:** PASSED\n"
                "**Bloqueantes 4R:** []\n"
            ),
        })
        assert result["blockedReasons"] == []
        assert result["nextRecommended"] == "archive"

    def test_detect_blockers_reads_same_file_as_resolve_phase_file(self, tmp_path):
        """Unit-level invariant: whatever resolve_phase_file() returns for
        'verify' is the exact file detect_blockers() parses for content."""
        cd = tmp_path / "change-invariant"
        cd.mkdir()
        (cd / "verify-report-onlyone.md").write_text("Status: failed\n")
        resolved = resolve_phase_file(cd, "verify")
        assert resolved is not None
        assert resolved.name == "verify-report-onlyone.md"
        blockers = detect_blockers(cd)
        assert any("Status=failed" in b for b in blockers)


# ─── R1-002: unknown/blank bracket markers are always pending ───────────

class TestR1002UnknownMarkerIsPending:

    def test_tilde_marker_rolled_back_counts_as_pending(self, tmp_path):
        """Exact repro from the reviewer: [~] must not vanish nor count done."""
        f = tmp_path / "tasks.md"
        f.write_text(
            "### [x] T-1 — done\n"
            "### [~] T-2 — rolled back\n"
        )
        progress = task_progress(f)
        assert progress["completed"] == 1
        assert progress["pending"] == 1
        assert progress["total"] == 2
        assert progress["allComplete"] is False

    def test_other_unknown_markers_all_pending(self, tmp_path):
        f = tmp_path / "tasks.md"
        f.write_text(
            "- [x] done\n"
            "- [!] urgent, unmarked\n"
            "- [?] uncertain\n"
            "- [/] in progress\n"
        )
        progress = task_progress(f)
        assert progress["completed"] == 1
        assert progress["pending"] == 3
        assert progress["notApplicable"] == 0

    def test_blank_bracket_still_pending(self, tmp_path):
        f = tmp_path / "tasks.md"
        f.write_text("- [ ] blank\n- [] empty\n")
        progress = task_progress(f)
        assert progress["pending"] == 2
        assert progress["completed"] == 0

    def test_dash_and_emdash_still_not_applicable(self, tmp_path):
        """R1-002 must not regress the notApplicable classification."""
        f = tmp_path / "tasks.md"
        f.write_text("- [x] done\n- [-] na hyphen\n- [—] na emdash\n")
        progress = task_progress(f)
        assert progress["completed"] == 1
        assert progress["notApplicable"] == 2
        assert progress["pending"] == 0

    def test_real_security_hardening_shape(self, tmp_path):
        """Reproduce the real precedent shape: 5 headings marked [~] ROLLED
        BACK among otherwise-done tasks — must all count as pending."""
        f = tmp_path / "tasks.md"
        f.write_text(
            "### [x] T-1.1 — done\n"
            "### [x] T-2.1 — done\n"
            "### [~] T-2.2 — ... ROLLED BACK\n"
            "### [~] T-2.3 — ... ROLLED BACK\n"
            "### [~] T-2.4 — ... ROLLED BACK\n"
            "### [~] T-2.5 — ... ROLLED BACK\n"
            "### [x] T-5.1 — done\n"
            "### [~] T-5.2 — ... ROLLED BACK\n"
        )
        progress = task_progress(f)
        assert progress["completed"] == 3
        assert progress["pending"] == 5
        assert progress["allComplete"] is False


# ─── R1-003 (REMOVED — round-3 redesign, 2026-08-04) ────────────────────
#
# `TestR1003DiscardSuffixExcluded` (4 tests) asserted that `-old`/`-bak`/
# `-borrador`/`-draft`/`-wip` suffixed files were excluded from evidence.
# That exclusion is exactly the mechanism that later caused H-A/H-D (a real
# `Status: blocked` report going invisible because it happened to be named
# `-old-real.md`). Removed along with `_is_discarded_candidate()`. See
# equivalent coverage (now asserting the OPPOSITE: these filenames DO count)
# in test_sdd_status_round3_redesign.py.
