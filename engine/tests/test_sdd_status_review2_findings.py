#!/usr/bin/env python3
"""
Tests for the 3 findings from the SECOND adversarial fresh-context reviewer
pass (2026-08-04) on the sdd_status.py glob-resolution + heading-checkbox fix:

  H1 (BLOCKER) — resolve_phase_file() picked ONE candidate via alphabetical
    sorted() when several suffixed reports existed and no canonical was
    present. `"verify-report-tanda10.md" < "verify-report-tanda2.md"` as
    STRINGS meant a clean tanda10 could win while a blocked tanda2 was never
    read — blockedReasons=[] with real blocked work hidden.
    Fix: fail-closed aggregation via resolve_phase_candidates() — EVERY
    valid candidate is parsed by detect_blockers(), and any one signaling
    failure blocks, naming the concrete culprit file. No mtime tiebreaker
    (mtime is reset by git checkout/cp -R/clone — verified fragile).
    UPDATED 2026-08-04 (round-3 redesign): the "canonical wins ALONE"
    precedence mentioned above was ITSELF later found unsafe (H-B: a
    case-insensitive-filesystem match on the canonical name could hide a
    real blocked sibling) and was removed — the canonical filename is now
    just one more candidate in the aggregate, same as any suffixed one. The
    two tests below that asserted "canonical wins alone" were rewritten to
    assert the opposite (aggregation, no precedence).

  H2 (PREFER) — a HEADING with a bracket that is NOT task-shaped
    (`## [DRAFT] Notas de reunion`) counted as a pending task purely because
    it had *a* bracket. Fix: for headings only (list items `- [x]` keep the
    R1-002 behavior, unambiguous standard markdown checklist syntax),
    require the bracket to be immediately followed by task-id vocabulary
    (_TASK_ID_PATTERN) to count as a task marker at all.

  H3 (PREFER, REMOVED 2026-08-04 round-3) — used to add a `discardNotices`
    field when the only report for a phase was discarded by filename
    (R1-003). Both R1-003's discard heuristic and this field were removed
    in the round-3 redesign (see resolve_phase_candidates() docstring in
    sdd_status.py) — `TestH3DiscardVisibility` was deleted below; there is
    nothing left to be informational about.

Fixtures live entirely under tmp_path — no real sdd/changes/* is
touched.
"""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "sdd_status_review2_findings",
    Path(__file__).resolve().parent.parent / "sdd_status.py"
)
_sdd_status = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sdd_status)

resolve_phase_file = _sdd_status.resolve_phase_file
resolve_phase_candidates = _sdd_status.resolve_phase_candidates
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


# ─── H1: fail-closed aggregation over suffixed candidates ──────────────

class TestH1FailClosedAggregation:

    def test_alphabetically_later_suffix_blocked_is_not_hidden(self, tmp_path):
        """Exact reviewer repro: tanda2 (blocked) + tanda10 (success), no
        canonical. Alphabetical sort put tanda10 first — must NOT hide
        tanda2's blocked status."""
        result = _run_compute(tmp_path, "change-h1-a", {
            "proposal.md": "p",
            "specs/core/spec.md": "s",
            "design.md": "d",
            "tasks.md": "- [x] t1\n",
            "apply-report.md": "apply done",
            "verify-report-tanda2.md": "# Verify\nStatus: blocked\n",
            "verify-report-tanda10.md": "# Verify\nStatus: success\n",
        })
        assert result["blockedReasons"] != [], "tanda2 blocked was hidden"
        assert any("tanda2" in b for b in result["blockedReasons"]), (
            f"blocker must name the concrete culprit file: {result['blockedReasons']}"
        )
        assert result["nextRecommended"] != "archive"

    def test_canonical_present_does_not_hide_blocked_sibling(self, tmp_path):
        """Round-3 redesign (H-B, supersedes the old H1-precedencia test):
        there is no more "canonical wins alone" precedence. A clean
        verify-report.md sitting alongside a blocked verify-report-tanda2.md
        must NOT reach archive — the blocked sibling is still real evidence
        and must still block, naming itself."""
        result = _run_compute(tmp_path, "change-h1-b", {
            "proposal.md": "p",
            "specs/core/spec.md": "s",
            "design.md": "d",
            "tasks.md": "- [x] t1\n",
            "apply-report.md": "apply done",
            "verify-report-tanda2.md": "# Verify\nStatus: blocked\n",
            "verify-report-tanda10.md": "# Verify\nStatus: success\n",
            "verify-report.md": (
                "# Verify\nStatus: success\n"
                "- **CRITICAL:** 0\n"
                "**4R Verdict:** PASSED\n"
                "**Bloqueantes 4R:** []\n"
            ),
        })
        assert result["blockedReasons"] != [], result["blockedReasons"]
        assert any("tanda2" in b for b in result["blockedReasons"])
        assert result["nextRecommended"] != "archive"

    def test_reverse_order_also_blocks(self, tmp_path):
        """H1-orden inverso: tanda2 success + tanda10 blocked (order flipped
        vs the first test) must ALSO block — proves the fix does not depend
        on which name sorts first/last."""
        result = _run_compute(tmp_path, "change-h1-c", {
            "proposal.md": "p",
            "specs/core/spec.md": "s",
            "design.md": "d",
            "tasks.md": "- [x] t1\n",
            "apply-report.md": "apply done",
            "verify-report-tanda2.md": "# Verify\nStatus: success\n",
            "verify-report-tanda10.md": "# Verify\nStatus: blocked\n",
        })
        assert result["blockedReasons"] != []
        assert any("tanda10" in b for b in result["blockedReasons"])
        assert result["nextRecommended"] != "archive"

    def test_resolve_phase_candidates_returns_all_valid_suffixed(self, tmp_path):
        """Unit-level: resolve_phase_candidates() must return EVERY valid
        suffixed candidate when there is no canonical, not just one."""
        cd = tmp_path / "change-h1-d"
        cd.mkdir()
        (cd / "verify-report-tanda2.md").write_text("Status: blocked\n")
        (cd / "verify-report-tanda10.md").write_text("Status: success\n")
        candidates = resolve_phase_candidates(cd, "verify")
        names = {c.name for c in candidates}
        assert names == {"verify-report-tanda2.md", "verify-report-tanda10.md"}

    def test_resolve_phase_candidates_includes_canonical_and_siblings(self, tmp_path):
        """Unit-level (round-3 redesign): canonical present → it is still
        ONE of the candidates, aggregated together with valid siblings — no
        more "wins alone" exclusion. `-old-pass` is no longer a discard
        marker either (H-A/H-D): it must be aggregated too."""
        cd = tmp_path / "change-h1-e"
        cd.mkdir()
        (cd / "verify-report.md").write_text("Status: success\n")
        (cd / "verify-report-old-pass.md").write_text("Status: blocked\n")
        candidates = resolve_phase_candidates(cd, "verify")
        names = {c.name for c in candidates}
        assert names == {"verify-report.md", "verify-report-old-pass.md"}

    def test_multiple_blocked_candidates_all_reported(self, tmp_path):
        """Three blocked suffixed candidates, no canonical — all three must
        surface as distinct blockers, each naming its own file."""
        result = _run_compute(tmp_path, "change-h1-f", {
            "proposal.md": "p",
            "specs/core/spec.md": "s",
            "design.md": "d",
            "tasks.md": "- [x] t1\n",
            "apply-report.md": "apply done",
            "verify-report-tanda1.md": "Status: blocked\n",
            "verify-report-tanda2.md": "Status: failed\n",
            "verify-report-tanda3.md": "- **CRITICAL:** 2\n",
        })
        assert len(result["blockedReasons"]) == 3
        joined = " ".join(result["blockedReasons"])
        assert "tanda1" in joined and "tanda2" in joined and "tanda3" in joined

    def test_resolve_phase_file_still_returns_single_path_for_presence(self, tmp_path):
        """resolve_phase_file() (used only for presence checks) must keep
        returning a single Path — presence semantics unaffected by H1."""
        cd = tmp_path / "change-h1-g"
        cd.mkdir()
        (cd / "verify-report-tanda2.md").write_text("Status: blocked\n")
        (cd / "verify-report-tanda10.md").write_text("Status: success\n")
        found = resolve_phase_file(cd, "verify")
        assert found is not None
        assert found.name in ("verify-report-tanda2.md", "verify-report-tanda10.md")


# ─── H2: heading bracket requires task-id vocabulary to count ──────────

class TestH2HeadingBracketRequiresTaskId:

    def test_non_task_heading_bracket_not_counted(self, tmp_path):
        """Exact reviewer repro: '## [DRAFT] Notas de reunion' heading has a
        bracket but is a section label, not a task — must not count."""
        f = tmp_path / "tasks.md"
        f.write_text(
            "## [DRAFT] Notas de reunion\n"
            "### [x] T-1.1 Tarea real\n"
        )
        progress = task_progress(f)
        assert progress["total"] == 1
        assert progress["completed"] == 1
        assert progress["pending"] == 0

    def test_multiple_non_task_bracket_headings_ignored(self, tmp_path):
        f = tmp_path / "tasks.md"
        f.write_text(
            "## [DRAFT] Notas de reunion\n"
            "## [WIP] Ideas sueltas\n"
            "### [x] T-1.1 — Tarea real\n"
            "### [ ] T-1.2 — Otra tarea real\n"
        )
        progress = task_progress(f)
        assert progress["total"] == 2
        assert progress["completed"] == 1
        assert progress["pending"] == 1

    def test_tilde_marker_in_heading_still_pending_no_regression(self, tmp_path):
        """R1-002 non-regression: '### [~] T-2.2' (bracket + task-id) must
        still count as pending, unaffected by the H2 tightening."""
        f = tmp_path / "tasks.md"
        f.write_text(
            "### [x] T-1.1 — done\n"
            "### [~] T-2.2 — ROLLED BACK\n"
        )
        progress = task_progress(f)
        assert progress["completed"] == 1
        assert progress["pending"] == 1
        assert progress["total"] == 2

    def test_compute_end_to_end_draft_heading_not_inflated(self, tmp_path):
        result = _run_compute(tmp_path, "change-h2-a", {
            "proposal.md": "p",
            "specs/core/spec.md": "s",
            "design.md": "d",
            "tasks.md": (
                "## [DRAFT] Notas de reunion\n"
                "### [x] T-1.1 Tarea real\n"
            ),
        })
        assert result["taskProgress"]["total"] == 1
        assert result["taskProgress"]["completed"] == 1
        assert result["taskProgress"]["pending"] == 0

    def test_draft_heading_does_not_trigger_defect3_guard_on_its_own(self, tmp_path):
        """A doc whose ONLY bracketed heading is a non-task '[DRAFT]' label
        (no bracket+task-id heading anywhere) must NOT satisfy defect-3's
        guard-1 — no bare task heading should be invented as pending."""
        f = tmp_path / "tasks.md"
        f.write_text(
            "## [DRAFT] Notas de reunion\n"
            "### T-1 — bare task heading, no bracket anywhere in doc\n"
        )
        progress = task_progress(f)
        assert progress["total"] == 0
        assert progress["pending"] == 0


# ─── H3 (REMOVED — round-3 redesign, 2026-08-04) ────────────────────────
#
# `TestH3DiscardVisibility` (5 tests) tested `discardNotices`, a field that
# announced files excluded by filename-discard heuristics (-old/-bak/
# -borrador/-draft/-wip). Both the discard heuristic AND the `discardNotices`
# field were removed in the round-3 redesign: there is no more discard-by-
# name, so there is nothing to announce. See resolve_phase_candidates()
# docstring in sdd_status.py for the full rationale (H-A/H-B/H-D).
# `-wip`/`-borrador`/`-old` filenames are now ordinary evidence — see the
# equivalent coverage in test_sdd_status_round3_redesign.py (H-A, H-B).
