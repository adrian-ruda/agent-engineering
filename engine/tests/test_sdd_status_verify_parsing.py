#!/usr/bin/env python3
"""
Tests for sdd_status.py verify-report parsing logic (Paso 1 recovery patch).

Covers:
  - Structured success (CRITICAL: 0, "partial" in prose, 4R PASSED) → NO block
  - Structured failed (Status: failed, 4R FAILED, CRITICAL > 0) → BLOCK
  - Legacy fallback conservador → blocks only on anchored signals
  - Edge cases: empty, malformed frontmatter, mixed signals
"""
import pytest
import importlib.util
from pathlib import Path

# sdd_status.py has a hyphen — can't use normal import
_spec = importlib.util.spec_from_file_location(
    "sdd_status",
    Path(__file__).resolve().parent.parent / "sdd_status.py"
)
_sdd_status = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sdd_status)

_parse_verify_status = _sdd_status._parse_verify_status
_verify_blocked = _sdd_status._verify_blocked


# ─── _parse_verify_status ──────────────────────────────────────────

class TestParseVerifyStatusSuccess:
    """Golden: structured success report with historical words in prose."""

    def test_success_with_critical_zero_and_partial_prose(self):
        text = """# Verify Report — test
Date: 2026-07-12
Status: success

## Findings
- **CRITICAL:** 0
- **WARNING:** 0
- **SUGGESTION:** 0

## Seccion B — 4R Code Quality Gate
**4R Verdict:** PASSED
**Bloqueantes 4R:** []

Some historical note: this was previously in a partial state.
We blocked the adversarial vectors correctly.
The fail-closed design worked.
"""
        result = _parse_verify_status(text)
        assert result["is_structured"] is True
        assert result["frontmatter_status"] == "success"
        assert result["four_r_verdict"] == "PASSED"
        assert result["four_r_blockers"] == []
        assert result["critical_count"] == 0
        assert result["warning_count"] == 0

    def test_success_with_mixed_case_status(self):
        text = "Status: Success\n\nSome text with fail-closed and BLOCKED words."
        result = _parse_verify_status(text)
        assert result["frontmatter_status"] == "success"
        assert result["is_structured"] is True

    def test_no_frontmatter_still_parses_counts(self):
        text = """# Verify — legacy
Just a document with findings.

- **CRITICAL:** 3
- **WARNING:** 1
"""
        result = _parse_verify_status(text)
        assert result["is_structured"] is False
        assert result["frontmatter_status"] is None
        assert result["critical_count"] == 3
        assert result["warning_count"] == 1

    def test_empty_report(self):
        assert _parse_verify_status("")["is_structured"] is False
        assert _parse_verify_status("")["frontmatter_status"] is None

    def test_status_failed_parsed_correctly(self):
        text = 'Status: failed\n\n- **CRITICAL:** 2\n**4R Verdict:** FAILED\n**Bloqueantes 4R:** ["R1-001"]'
        result = _parse_verify_status(text)
        assert result["frontmatter_status"] == "failed"
        assert result["critical_count"] == 2
        assert result["four_r_verdict"] == "FAILED"
        assert result["four_r_blockers"] == ["R1-001"]

    def test_status_blocked_parsed_correctly(self):
        text = "Status: blocked"
        result = _parse_verify_status(text)
        assert result["frontmatter_status"] == "blocked"

    def test_status_partial_parsed_correctly(self):
        text = "Status: partial"
        result = _parse_verify_status(text)
        assert result["frontmatter_status"] == "partial"


class TestParseVerifyStatusNegative:
    """False positives from historical/keyword prose must NOT trigger structured signals."""

    def test_non_frontmatter_status_ignored(self):
        """'Status: success' inside body prose (not on its own line) is ignored."""
        text = """# Verify
The status: success was reported earlier.
But really the Status: partial came later.
"""
        result = _parse_verify_status(text)
        assert result["frontmatter_status"] is None
        assert result["is_structured"] is False

    def test_blockers_only_match_4r_bold_format(self):
        """Random 'blocked' words don't produce 4R blockers."""
        text = """# Verify
Status: success
**4R Verdict:** PASSED
**Bloqueantes 4R:** []
All vectors were blocked. Blocked is good here.
"""
        result = _parse_verify_status(text)
        assert result["four_r_blockers"] == []
        assert result["four_r_verdict"] == "PASSED"

    def test_adversarial_table_not_parsed_as_failure(self):
        """The adversarial table full of 'Blocked' entries is NOT a signal."""
        text = """# Verify Report
Date: 2026-07-12
Status: success

## Adversarial attempts
| Vector | Result |
|---|---|
| same-role | Blocked |
| target drift | Blocked |
| stale revision | Blocked |
| blocking finding | Blocked |
| malformed receipt | Blocked |

- **CRITICAL:** 0
**4R Verdict:** PASSED
**Bloqueantes 4R:** []
"""
        result = _parse_verify_status(text)
        assert result["frontmatter_status"] == "success"
        assert result["critical_count"] == 0
        assert result["four_r_verdict"] == "PASSED"
        assert result["four_r_blockers"] == []


# ─── _verify_blocked ───────────────────────────────────────────────

class TestVerifyBlockedStructured:
    """Structured reports: only blocked when frontmatter/4R/counts say so."""

    def test_success_clean_not_blocked(self):
        parsed = {
            "frontmatter_status": "success",
            "four_r_verdict": "PASSED",
            "four_r_blockers": [],
            "critical_count": 0,
            "warning_count": 0,
            "is_structured": True,
        }
        assert _verify_blocked(parsed) is None

    def test_success_with_historical_words_not_blocked(self):
        """CRITICAL: 0 + 4R PASSED + Status: success — NOT blocked even with prose containing 'partial', 'fail', 'blocked'."""
        parsed = {
            "frontmatter_status": "success",
            "four_r_verdict": "PASSED",
            "four_r_blockers": [],
            "critical_count": 0,
            "warning_count": 5,  # warnings alone don't block
            "is_structured": True,
        }
        assert _verify_blocked(parsed) is None

    def test_frontmatter_failed_blocked(self):
        parsed = {
            "frontmatter_status": "failed",
            "four_r_verdict": None,
            "four_r_blockers": None,
            "critical_count": None,
            "warning_count": None,
            "is_structured": True,
        }
        assert _verify_blocked(parsed) == "verify-report.md Status=failed — no archivar"

    def test_frontmatter_blocked(self):
        parsed = {"frontmatter_status": "blocked", "is_structured": True}
        assert _verify_blocked(parsed) == "verify-report.md Status=blocked — no archivar"

    def test_frontmatter_partial_blocked(self):
        parsed = {"frontmatter_status": "partial", "is_structured": True}
        assert _verify_blocked(parsed) == "verify-report.md Status=partial — no archivar"

    def test_four_r_failed_blocked(self):
        parsed = {
            "frontmatter_status": "success",
            "four_r_verdict": "FAILED",
            "four_r_blockers": [],
            "critical_count": 0,
            "is_structured": True,
        }
        assert _verify_blocked(parsed) == "verify-report.md 4R Verdict=FAILED — no archivar"

    def test_four_r_blockers_non_empty_blocked(self):
        parsed = {
            "frontmatter_status": "success",
            "four_r_verdict": "PASSED",
            "four_r_blockers": ["R1-001"],
            "critical_count": 0,
            "is_structured": True,
        }
        assert _verify_blocked(parsed) is not None
        assert "Bloqueantes 4R" in _verify_blocked(parsed)

    def test_critical_gt_zero_blocked(self):
        parsed = {
            "frontmatter_status": "success",
            "four_r_verdict": "PASSED",
            "four_r_blockers": [],
            "critical_count": 3,
            "is_structured": True,
        }
        assert _verify_blocked(parsed) == "verify-report.md tiene 3 hallazgos CRITICAL — no archivar"


class TestVerifyBlockedLegacy:
    """Legacy reports (no structured frontmatter): conservative fallback."""

    def test_legacy_no_signals_not_blocked(self):
        """Legacy report with no frontmatter, no CRITICAL count — NOT blocked."""
        parsed = {
            "frontmatter_status": None,
            "four_r_verdict": None,
            "four_r_blockers": None,
            "critical_count": None,
            "warning_count": None,
            "is_structured": False,
        }
        assert _verify_blocked(parsed) is None

    def test_legacy_critical_gt_zero_blocked(self):
        parsed = {"frontmatter_status": None, "critical_count": 2, "is_structured": False}
        assert _verify_blocked(parsed) == "verify-report.md tiene 2 hallazgos CRITICAL — no archivar"

    def test_legacy_explicit_failed_blocked(self):
        parsed = {"frontmatter_status": "failed", "is_structured": False}
        assert _verify_blocked(parsed) == "verify-report.md Status=failed — no archivar"

    def test_legacy_clean_not_blocked(self):
        """Legacy report with some parsed counts but all clean."""
        parsed = {"frontmatter_status": None, "critical_count": 0, "is_structured": False}
        assert _verify_blocked(parsed) is None


# ─── End-to-end via compute() with temp dir ─────────────────────────

class TestEndToEndGolden:
    """Integration: run the full compute() on a temp change dir."""

    @staticmethod
    def _run_compute(tmp_path, change_name, artifacts):
        """Write artifacts into tmp_path, run compute with BASE overridden."""
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

    def test_success_with_historical_words_not_blocked(self, tmp_path):
        """Golden: our exact recovery scenario — success + historical words = NOT blocked.

        FIXTURE CORREGIDA (2026-08-04, fix del falso verde): este test tenía
        `- [ ] task 2` PENDIENTE y aun así exigía `nextRecommended == "archive"`
        — es decir, codificaba el bug que este trabajo elimina (archivar un
        change con trabajo sin hacer). Lo que el test realmente quiere probar
        es lo que dice su nombre: que las palabras históricas ("Blocked",
        "partial", "CRITICAL") dentro de un verify-report `Status: success` NO
        generan blockers. Ese eje queda intacto; solo se completa la tarea 2
        para que el fixture represente un change efectivamente terminado.
        La cobertura del caso con tareas pendientes vive ahora en
        test_sdd_status_phase_resolution.py::TestFalsoVerdeApplyVsTasks.
        """
        result = self._run_compute(tmp_path, "test-change", {
            "proposal.md": "p",
            "specs/core/spec.md": "s",
            "design.md": "d",
            "tasks.md": "- [x] task 1\n- [x] task 2\n",
            "apply-report.md": "apply done",
            "verify-report.md": """# Verify Report — test
Date: 2026-07-12
Status: success

## Seccion A
The fail-closed design is excellent. All adversarial vectors are Blocked.
This was previously in a partial state.
No CRITICAL findings remain.

## Findings
- **CRITICAL:** 0
- **WARNING:** 0

## Seccion B — 4R Code Quality Gate
**4R Verdict:** PASSED
**Bloqueantes 4R:** []
""",
        })
        assert result["blockedReasons"] == [], f"Got blockers: {result['blockedReasons']}"
        assert result["nextRecommended"] == "archive"

    def test_actual_failed_still_blocked(self, tmp_path):
        """An actual failed verify-report MUST still block."""
        result = self._run_compute(tmp_path, "test-failed-change", {
            "proposal.md": "p",
            "specs/core/spec.md": "s",
            "design.md": "d",
            "tasks.md": "- [x] all\n",
            "apply-report.md": "apply",
            "verify-report.md": """# Verify Report
Status: failed

- **CRITICAL:** 3
**4R Verdict:** FAILED
**Bloqueantes 4R:** ["R1-007"]
""",
        })
        assert len(result["blockedReasons"]) > 0
        assert result["nextRecommended"] == "resolve-blockers"

    def test_four_r_failed_but_frontmatter_success_still_blocked(self, tmp_path):
        """4R FAILED must block even if frontmatter says success."""
        result = self._run_compute(tmp_path, "test-4r-fail", {
            "proposal.md": "p",
            "specs/core/spec.md": "s",
            "design.md": "d",
            "tasks.md": "- [x] done\n",
            "apply-report.md": "apply",
            "verify-report.md": """# Verify
Status: success
**4R Verdict:** FAILED
**Bloqueantes 4R:** ["R3-001"]
- **CRITICAL:** 0
""",
        })
        assert len(result["blockedReasons"]) > 0
        assert "4R Verdict=FAILED" in result["blockedReasons"][0]

    def test_legacy_clean_not_blocked(self, tmp_path):
        """Legacy verify with no frontmatter and no CRITICAL > 0 — not blocked."""
        result = self._run_compute(tmp_path, "test-legacy-clean", {
            "proposal.md": "p",
            "specs/core/spec.md": "s",
            "design.md": "d",
            "tasks.md": "- [x] done\n",
            "apply-report.md": "apply",
            "verify-report.md": """# Legacy Verify
All tests passed. No issues found.
Some words: failed test was rerun, partial rollout was reverted.
""",
        })
        assert result["blockedReasons"] == [], f"Legacy clean blocked: {result['blockedReasons']}"


# ─── Routing E2E tests ─────────────────────────────────────────────

class TestRoutingAuthorityAware:
    """E2E: _compute_next_recommended phase routing, exercised through compute().

    Covers:
      1) archive ONLY when every build phase is done + verify success + 4R passed
      2) blocked verify / 4R failed / declared blockers → resolve-blockers
      3) incomplete phases → phase token
      4) applyAllowed coherent with the emitted routing token
    """

    @staticmethod
    def _run_compute(tmp_path, change_name, artifacts):
        """Write artifacts and run compute() over them."""
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

    # ─── all phases done → archive ──────────────────────────────────

    def test_missing_authority_in_shadow_with_all_phases_routes_archive(self, tmp_path):
        """All phases present + clean verify → archive."""
        result = self._run_compute(tmp_path, "routing-missing-shadow", {
            "proposal.md": "p",
            "specs/core/spec.md": "s",
            "design.md": "d",
            "tasks.md": "- [x] done\n",
            "apply-report.md": "apply",
            "verify-report.md": """# Verify
Status: success
- **CRITICAL:** 0
**4R Verdict:** PASSED
**Bloqueantes 4R:** []
""",
        })
        assert result["blockedReasons"] == []
        assert result["nextRecommended"] == "archive"

    def test_missing_authority_with_incomplete_phases_routes_next_phase(self, tmp_path):
        """Missing verify → routes verify."""
        result = self._run_compute(tmp_path, "routing-missing-shadow-inc", {
            "proposal.md": "p",
            "specs/core/spec.md": "s",
            "design.md": "d",
            "tasks.md": "- [x] done\n",
            "apply-report.md": "apply",
        })  # No verify
        assert result["nextRecommended"] == "verify"

    # ─── blocked verify → resolve-blockers ──────────────────────────

    def test_blocked_verify_with_reviewing_authority_stays_resolve_blockers(self, tmp_path):
        """Blocked verify → resolve-blockers."""
        result = self._run_compute(tmp_path, "routing-blocked-verify", {
            "proposal.md": "p",
            "specs/core/spec.md": "s",
            "design.md": "d",
            "tasks.md": "- [x] done\n",
            "apply-report.md": "apply",
            "verify-report.md": """# Verify
Status: failed
- **CRITICAL:** 5
**4R Verdict:** FAILED
**Bloqueantes 4R:** ["R1-001"]
""",
        })
        assert len(result["blockedReasons"]) > 0
        assert result["nextRecommended"] == "resolve-blockers"

    # ─── 4R failed → resolve-blockers ───────────────────────────────

    def test_four_r_failed_with_allow_authority_routes_resolve_blockers(self, tmp_path):
        """4R FAILED → resolve-blockers, never archive."""
        result = self._run_compute(tmp_path, "routing-4r-fail", {
            "proposal.md": "p",
            "specs/core/spec.md": "s",
            "design.md": "d",
            "tasks.md": "- [x] done\n",
            "apply-report.md": "apply",
            "verify-report.md": """# Verify
Status: success
**4R Verdict:** FAILED
**Bloqueantes 4R:** ["R4-002"]
- **CRITICAL:** 0
""",
        })
        assert len(result["blockedReasons"]) > 0
        assert result["nextRecommended"] == "resolve-blockers"
        # Blockers gate first — archive is NOT recommended
        assert result["nextRecommended"] != "archive"

    # ─── incomplete phases → phase token ────────────────────────────

    def test_reviewing_authority_but_no_verify_yet_routes_verify(self, tmp_path):
        """Verify artifact missing → routes verify."""
        result = self._run_compute(tmp_path, "routing-no-verify", {
            "proposal.md": "p",
            "specs/core/spec.md": "s",
            "design.md": "d",
            "tasks.md": "- [x] done\n",
            "apply-report.md": "apply",
        })
        assert result["nextRecommended"] == "verify"

    # ─── I3 adversarial: routing/applyAllowed coherence ─────────────

    def test_authority_none_shadow_with_manifests_routes_archive(self, tmp_path):
        """I3: all phases present + clean verify → archive."""
        result = self._run_compute(tmp_path, "i3-none", {
            "proposal.md": "p", "specs/core/spec.md": "s", "design.md": "d",
            "tasks.md": "- [x] done\n", "apply-report.md": "apply",
            "verify-report.md": "# Verify\nStatus: success\n- **CRITICAL:** 0\n**4R Verdict:** PASSED\n**Bloqueantes 4R:** []",
        })
        assert result["nextRecommended"] == "archive"
        assert result["applyAllowed"] is True

    def test_apply_allowed_shadow_legacy_no_manifests(self, tmp_path):
        """I3: spec/design/tasks on disk → applyAllowed=True."""
        result = self._run_compute(tmp_path, "i3-legacy", {
            "proposal.md": "p", "specs/core/spec.md": "s", "design.md": "d",
            "tasks.md": "- [x] done\n",
        })
        assert result["nextRecommended"] == "apply"
        assert result["applyAllowed"] is True

    # ─── I3 adversarial: archive-report presence ────────────────────

    def test_archive_report_present_with_allow_routes_archive(self, tmp_path):
        """I3: archive-report.md present + clean → archive."""
        result = self._run_compute(tmp_path, "i3-archive-present", {
            "proposal.md": "p", "specs/core/spec.md": "s", "design.md": "d",
            "tasks.md": "- [x] done\n", "apply-report.md": "apply",
            "verify-report.md": "# Verify\nStatus: success\n- **CRITICAL:** 0\n**4R Verdict:** PASSED\n**Bloqueantes 4R:** []",
            "archive-report.md": "archived",
        })
        assert result["nextRecommended"] == "archive"

    # ─── I3 adversarial: verify blockers precedence ─────────────────

    def test_verify_blockers_precede_allow_authority(self, tmp_path):
        """I3: verify Status: failed must block."""
        result = self._run_compute(tmp_path, "i3-verify-precedes", {
            "proposal.md": "p", "specs/core/spec.md": "s", "design.md": "d",
            "tasks.md": "- [x] done\n", "apply-report.md": "apply",
            "verify-report.md": "# Verify\nStatus: failed\n- **CRITICAL:** 3\n**4R Verdict:** FAILED",
        })
        assert result["nextRecommended"] == "resolve-blockers"
        # Blockers ALWAYS take priority
        assert len(result["blockedReasons"]) > 0
