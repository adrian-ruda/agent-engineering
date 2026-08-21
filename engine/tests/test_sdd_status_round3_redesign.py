#!/usr/bin/env python3
"""
Tests for the ROUND-3 REDESIGN of sdd_status.py's phase-evidence resolution
(2026-08-04), replacing the name-heuristic engine (discard-by-suffix +
canonical-wins-alone precedence) with pure, total aggregation.

Three rounds of adversarial review found a NEW hole in the SAME function
every time, always rooted in inferring "is this evidence?" from the
FILENAME instead of the file's actual content/type:

  H-A (REJECT) — a real `Status: blocked` report named with an "-old"/
    "-real" segment (`verify-report-old-real.md`) went invisible if another
    suffixed report was clean — the discard-by-name heuristic could hide a
    genuine blocker.
  H-B (REJECT) — on a case-insensitive filesystem (macOS/APFS, the real
    environment), `VERIFY-REPORT.MD` was treated as "the canonical" by
    `Path.exists()`, and "canonical wins alone" discarded a blocked
    suffixed sibling.
  H-C (REQUIRE) — no candidate checked `is_file()`. A directory homonymous
    with an expected report name (`verify-report-tanda3.md/`) crashed the
    engine with an uncaught IsADirectoryError.
  H-D (PREFER) — the segment-based discard exclusion is the root cause that
    enabled H-A: any legitimate report whose name happened to contain a
    banned segment ("old", "bak", ...) was silently thrown away.

Fix: no discard-by-name, no canonical-wins-alone precedence. Every file
matching the phase's pattern that is a real, non-empty, readable FILE
(`_is_valid_evidence_file`) is evidence. `detect_blockers()` parses ALL of
them; any one signaling failure blocks, naming the REAL file on disk.

CONSEQUENCE ACCEPTED (deliberate, see resolve_phase_candidates() docstring
in sdd_status.py): a stale/draft report that says `blocked` WILL block the
phase. The fix for that is to update or delete the file, not for the
engine to guess which report to ignore by name.

Fixtures live entirely under tmp_path — no real sdd/changes/* is
touched.
"""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "sdd_status_round3_redesign",
    Path(__file__).resolve().parent.parent / "sdd_status.py"
)
_sdd_status = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sdd_status)

resolve_phase_candidates = _sdd_status.resolve_phase_candidates
detect_blockers = _sdd_status.detect_blockers
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


BASE_ARTIFACTS = {
    "proposal.md": "p",
    "specs/core/spec.md": "s",
    "design.md": "d",
    "tasks.md": "- [x] t1\n",
    "apply-report.md": "apply done",
}

CLEAN_VERIFY = (
    "# Verify\nStatus: success\n"
    "- **CRITICAL:** 0\n"
    "**4R Verdict:** PASSED\n"
    "**Bloqueantes 4R:** []\n"
)


class TestHADiscardByNameNoLongerHidesBlockers:
    """H-A: a report with an '-old'/'-real' segment saying Status: blocked
    must still block, even if another suffixed report is clean."""

    def test_old_real_blocked_not_hidden_by_clean_sibling(self, tmp_path):
        result = _run_compute(tmp_path, "change-ha", {
            **BASE_ARTIFACTS,
            "verify-report-old-real.md": "# Verify\nStatus: blocked\n",
            "verify-report-tanda3.md": "# Verify\nStatus: success\n",
        })
        assert result["blockedReasons"] != [], result["blockedReasons"]
        assert any("verify-report-old-real.md" in b for b in result["blockedReasons"])
        assert result["nextRecommended"] != "archive"

    def test_wip_borrador_draft_bak_no_longer_excluded(self, tmp_path):
        """Every ex-discard-marker name is ordinary evidence now (H-D)."""
        cd = tmp_path / "change-hd"
        cd.mkdir()
        for name in (
            "apply-report-bak.md",
            "apply-report-borrador.md",
            "apply-report-draft.md",
            "apply-report-wip.md",
            "apply-report-old.md",
        ):
            (cd / name).write_text("x")
        candidates = resolve_phase_candidates(cd, "apply")
        names = {c.name for c in candidates}
        assert names == {
            "apply-report-bak.md",
            "apply-report-borrador.md",
            "apply-report-draft.md",
            "apply-report-wip.md",
            "apply-report-old.md",
        }


class TestHBNoCanonicalPrecedence:
    """H-B: canonical filename (any casing) is just one more candidate —
    a blocked suffixed sibling is never hidden by it."""

    def test_blocked_suffixed_not_hidden_by_success_canonical_variant(self, tmp_path):
        result = _run_compute(tmp_path, "change-hb", {
            **BASE_ARTIFACTS,
            "verify-report-tanda2.md": "# Verify\nStatus: blocked\n",
            "VERIFY-REPORT.MD": "# Verify\nStatus: success\n",
        })
        assert result["blockedReasons"] != [], result["blockedReasons"]
        assert any("tanda2" in b for b in result["blockedReasons"])
        assert result["nextRecommended"] != "archive"


class TestHCDirectoryHomonymDoesNotCrash:
    """H-C: a directory sharing a report's expected name must not crash
    the engine, and must not count as clean evidence."""

    def test_directory_homonym_does_not_crash_and_is_not_evidence(self, tmp_path):
        cd = tmp_path / "change-hc"
        cd.mkdir()
        for filename, content in BASE_ARTIFACTS.items():
            target = cd / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        (cd / "verify-report-tanda3.md").mkdir()  # DIRECTORY, not a file

        original_base = _sdd_status.BASE
        _sdd_status.BASE = tmp_path
        try:
            result = _sdd_status.compute("change-hc")
        finally:
            _sdd_status.BASE = original_base

        # Must return valid JSON-able dict, no exception raised above.
        assert "error" not in result
        # The directory must not count as verify evidence.
        assert result["dependencies"]["verify"] != "all_done"
        candidates = resolve_phase_candidates(cd, "verify")
        assert all(c.name != "verify-report-tanda3.md" for c in candidates) or \
            all(c.is_file() for c in candidates)

    def test_directory_homonym_unit_level_excluded(self, tmp_path):
        cd = tmp_path / "change-hc-unit"
        cd.mkdir()
        (cd / "verify-report-tanda3.md").mkdir()
        candidates = resolve_phase_candidates(cd, "verify")
        assert candidates == []


class TestOrderIndependence:
    """Blockers must be found regardless of which suffix sorts first."""

    def test_tanda2_blocked_tanda10_success(self, tmp_path):
        result = _run_compute(tmp_path, "change-order-a", {
            **BASE_ARTIFACTS,
            "verify-report-tanda2.md": "# Verify\nStatus: blocked\n",
            "verify-report-tanda10.md": "# Verify\nStatus: success\n",
        })
        assert result["blockedReasons"] != []
        assert any("tanda2" in b for b in result["blockedReasons"])
        assert result["nextRecommended"] != "archive"

    def test_tanda10_blocked_tanda2_success_inverse_order(self, tmp_path):
        result = _run_compute(tmp_path, "change-order-b", {
            **BASE_ARTIFACTS,
            "verify-report-tanda2.md": "# Verify\nStatus: success\n",
            "verify-report-tanda10.md": "# Verify\nStatus: blocked\n",
        })
        assert result["blockedReasons"] != []
        assert any("tanda10" in b for b in result["blockedReasons"])
        assert result["nextRecommended"] != "archive"


class TestAllCleanRoutesArchive:
    """Sanity: all-clean reports (canonical + suffixed) must still reach
    archive — the redesign must not become a new false blocker."""

    def test_canonical_and_suffixed_all_success_archives(self, tmp_path):
        result = _run_compute(tmp_path, "change-all-clean", {
            **BASE_ARTIFACTS,
            "verify-report.md": CLEAN_VERIFY,
            "verify-report-tanda1.md": CLEAN_VERIFY,
            "verify-report-tanda2.md": CLEAN_VERIFY,
        })
        assert result["blockedReasons"] == [], result["blockedReasons"]
        assert result["nextRecommended"] == "archive"


class TestNoDiscardNoticesField:
    """The discardNotices field must be gone entirely from the JSON."""

    def test_discard_notices_not_in_output(self, tmp_path):
        result = _run_compute(tmp_path, "change-no-notices", {
            **BASE_ARTIFACTS,
            "verify-report.md": CLEAN_VERIFY,
        })
        assert "discardNotices" not in result


class TestUnreadableFileFailsClosed:
    """A file that raises OSError on read must become a blocker, never be
    silently skipped as clean."""

    def test_permission_denied_verify_report_blocks(self, tmp_path, monkeypatch):
        cd = tmp_path / "change-unreadable"
        cd.mkdir()
        for filename, content in BASE_ARTIFACTS.items():
            target = cd / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        target = cd / "verify-report-tanda9.md"
        target.write_text("Status: success\n")

        from pathlib import Path as _Path
        original_read_text = _Path.read_text

        def _boom(self, *args, **kwargs):
            if self.name == "verify-report-tanda9.md":
                raise PermissionError("denied")
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(_Path, "read_text", _boom)

        original_base = _sdd_status.BASE
        _sdd_status.BASE = tmp_path
        try:
            result = _sdd_status.compute("change-unreadable")
        finally:
            _sdd_status.BASE = original_base

        assert "error" not in result
        assert result["blockedReasons"] != []
        assert any("tanda9" in b for b in result["blockedReasons"])
