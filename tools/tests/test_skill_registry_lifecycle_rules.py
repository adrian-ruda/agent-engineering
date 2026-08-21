"""
The lifecycle rules: what promotes a capability, what retires it, and — the
part that took the longest to get right — what must never do either.

The rules under test:

    PROMOTE   state == trial  and  uses >= 5  and  success_rate >= 0.70
    RETIRE    uses >= 3  and  success_rate < 0.30
    UNSCORED  uses >= 5  and  no success_rate at all

`success_rate` is not a usage counter. It is the running mean of an adversarial
close-out verdict on each recorded run — clean 1.0, partial 0.5, failed 0.0 —
so a capability that ran five times and failed five times scores 0.00 with
uses 5, while a capability nobody ever graded scores nothing at all.

The refusal is the interesting half of this file. There is no staleness rule,
no age input and no last-used field, and several tests below exist only to
prove those absences hold under conditions that would tempt an implementation
to add them back.
"""


class TestPromotion:
    def test_promotes_at_exactly_both_thresholds(self, tree, flags_of):
        tree.add("retry-budget-review", tier="trial", state="trial", uses=5, success_rate=0.70)
        assert "PROMOTE" in flags_of(tree, "retry-budget-review")

    def test_promotes_comfortably_past_both_thresholds(self, tree, flags_of):
        tree.add("schema-drift-check", tier="trial", state="trial", uses=12, success_rate=0.91)
        assert "PROMOTE" in flags_of(tree, "schema-drift-check")

    def test_one_use_short_does_not_promote(self, tree, flags_of):
        tree.add("schema-drift-check", tier="trial", state="trial", uses=4, success_rate=1.0)
        assert flags_of(tree, "schema-drift-check") == []

    def test_one_hundredth_below_the_rate_does_not_promote(self, tree, flags_of):
        tree.add("schema-drift-check", tier="trial", state="trial", uses=20, success_rate=0.69)
        assert flags_of(tree, "schema-drift-check") == []

    def test_already_graduated_is_not_offered_for_promotion(self, tree, flags_of):
        tree.add("schema-drift-check", tier="core", state="graduated", uses=40, success_rate=0.98)
        assert flags_of(tree, "schema-drift-check") == []

    def test_promotion_thresholds_come_from_the_policy_file(self, tree, registry, collected):
        tree.add("schema-drift-check", tier="trial", state="trial", uses=2, success_rate=0.55)
        strict = registry.default_policy()
        assert registry.flags_for(collected(tree)["schema-drift-check"], strict) == []

        lenient = registry.load_policy(
            tree.write_policy({"promote": {"uses": 2, "success_rate": 0.50}})
        )
        assert "PROMOTE" in registry.flags_for(collected(tree)["schema-drift-check"], lenient)


class TestRetirement:
    def test_retires_on_sustained_measured_failure(self, tree, flags_of):
        tree.add("flaky-test-triage", tier="trial", state="trial", uses=3, success_rate=0.29)
        assert "RETIRE" in flags_of(tree, "flaky-test-triage")

    def test_a_measured_zero_is_a_measurement(self, tree, flags_of):
        tree.add("flaky-test-triage", tier="trial", state="trial", uses=4, success_rate=0.0)
        assert "RETIRE" in flags_of(tree, "flaky-test-triage")

    def test_two_bad_runs_are_not_enough_evidence(self, tree, flags_of):
        tree.add("flaky-test-triage", tier="trial", state="trial", uses=2, success_rate=0.0)
        assert flags_of(tree, "flaky-test-triage") == []

    def test_exactly_at_the_floor_is_not_below_it(self, tree, flags_of):
        tree.add("flaky-test-triage", tier="trial", state="trial", uses=9, success_rate=0.30)
        assert "RETIRE" not in flags_of(tree, "flaky-test-triage")

    def test_a_graduated_capability_can_also_be_retired(self, tree, flags_of):
        """Graduating is not tenure. The failure rule reads the record, not the
        state — otherwise promotion would be a one-way door."""
        tree.add("flaky-test-triage", tier="core", state="graduated", uses=6, success_rate=0.10)
        assert "RETIRE" in flags_of(tree, "flaky-test-triage")


class TestTheRuleWeRefusedToWrite:
    """No capability is retired for not having been used.

    The obvious metric for a directory of instructions is staleness: unused for
    N days, delete. It is not implemented here, and these tests are what keeps
    it from being reintroduced by someone who assumes it was an oversight.

    A capability with no recorded use has not failed. It has not been given the
    chance to fail, which is a fact about the calendar and about what work came
    in — never a fact about the capability.
    """

    def test_a_capability_that_was_never_used_is_never_flagged(self, tree, flags_of):
        tree.add("cold-start-profiler", tier="trial", state="trial")
        assert flags_of(tree, "cold-start-profiler") == []

    def test_zero_uses_with_no_rate_is_not_a_failure(self, tree, flags_of):
        tree.add("cold-start-profiler", tier="trial", state="trial", uses=0)
        assert flags_of(tree, "cold-start-profiler") == []

    def test_an_unused_capability_still_appears_in_the_registry(self, tree):
        tree.add("cold-start-profiler", tier="trial", state="trial", uses=0)
        assert tree.run().returncode == 0
        assert "cold-start-profiler" in tree.document

    def test_an_ancient_last_used_date_changes_nothing(self, tree, flags_of):
        """Even when the frontmatter volunteers the date, it is not read. The
        field can sit there; it has no path into a verdict."""
        tree.add("cold-start-profiler", tier="trial", state="trial", uses=1,
                 success_rate=1.0, extra={"last_used": "2019-01-04"})
        tree.add("cold-start-profiler-b", tier="trial", state="trial", uses=1,
                 success_rate=1.0)
        assert flags_of(tree, "cold-start-profiler") == flags_of(tree, "cold-start-profiler-b")

    def test_an_ancient_date_does_not_retire_next_to_a_real_failure(self, tree, flags_of):
        """Side by side, so the difference is visible: one is old and untouched,
        the other is fresh and failing. Only the second one moves."""
        tree.add("dormant-since-forever", tier="trial", state="trial", uses=0,
                 extra={"last_used": "2018-06-01"})
        tree.add("measured-failure", tier="trial", state="trial", uses=5, success_rate=0.20)
        assert flags_of(tree, "dormant-since-forever") == []
        assert "RETIRE" in flags_of(tree, "measured-failure")

    def test_the_generated_registry_states_the_refusal(self, tree):
        tree.add("cold-start-profiler", tier="trial", state="trial")
        assert tree.run().returncode == 0
        assert "Time is not an input" in tree.document


class TestUsedButNeverGraded:
    """Invocation is not evidence. A capability can be reached for repeatedly
    and never judged, and the registry says exactly that rather than reading
    the invocations as a good result."""

    def test_many_uses_without_a_rate_is_flagged_unscored(self, tree, flags_of):
        tree.add("contract-diff", tier="trial", state="trial", uses=7)
        assert flags_of(tree, "contract-diff") == ["UNSCORED"]

    def test_unscored_never_promotes(self, tree, flags_of):
        tree.add("contract-diff", tier="trial", state="trial", uses=99)
        assert "PROMOTE" not in flags_of(tree, "contract-diff")

    def test_unscored_never_retires(self, tree, flags_of):
        tree.add("contract-diff", tier="trial", state="trial", uses=99)
        assert "RETIRE" not in flags_of(tree, "contract-diff")

    def test_an_explicit_null_rate_reads_as_ungraded_not_as_zero(self, tree, flags_of):
        tree.add("contract-diff", tier="trial", state="trial", uses=4, success_rate="null")
        assert flags_of(tree, "contract-diff") == []

    def test_a_few_uses_without_a_rate_are_left_alone(self, tree, flags_of):
        tree.add("contract-diff", tier="trial", state="trial", uses=2)
        assert flags_of(tree, "contract-diff") == []


class TestSummaryCounts:
    """The header numbers are the ones a reader acts on, so they are asserted
    against a tree whose composition is known exactly."""

    def _populate(self, tree):
        tree.add("graduated-one", tier="core", state="graduated", uses=9, success_rate=0.88)
        tree.add("graduated-two", tier="support", state="graduated", uses=0)
        tree.add("ready-to-promote", tier="trial", state="trial", uses=6, success_rate=0.83)
        tree.add("failing", tier="trial", state="trial", uses=5, success_rate=0.10)
        tree.add("never-used", tier="trial", state="trial")

    def test_counts_split_graduated_and_trial(self, tree):
        self._populate(tree)
        assert tree.run().returncode == 0
        assert "**5 capabilities** · 2 graduated · 3 on trial" in tree.document

    def test_counts_separate_recorded_use_from_recorded_judgement(self, tree):
        self._populate(tree)
        assert tree.run().returncode == 0
        assert "3 with `uses > 0` · 3 carrying a success rate · 2 never graded" in tree.document

    def test_counts_report_one_promotion_and_one_retirement_candidate(self, tree):
        self._populate(tree)
        assert tree.run().returncode == 0
        assert "1 to promote" in tree.document
        assert "1 to retire" in tree.document

    def test_entries_carry_their_flag_in_the_document(self, tree):
        self._populate(tree)
        assert tree.run().returncode == 0
        promote_line = next(
            line for line in tree.document.splitlines() if "**ready-to-promote**" in line
        )
        assert "PROMOTE" in promote_line
        assert "`6·0.83`" in promote_line
