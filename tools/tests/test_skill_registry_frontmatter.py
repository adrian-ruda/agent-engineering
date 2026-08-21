"""
Reading the frontmatter, and disagreeing with it when it disagrees with itself.

Two fields describe a capability's standing, and they are separate on purpose:

    tier    what the capability is trusted with — core, extended, support, trial
    state   where it sits in the lifecycle — graduated or trial

Promotion flips both. A file where only one of them was flipped is not a small
cosmetic problem: it is a capability claiming a level of trust its record does
not support, or the reverse. The registry reports the contradiction instead of
picking whichever field it happened to read first.

The other half of this file is about unreadable values. A field that cannot be
parsed is never rounded to a convenient default — `uses: many` does not become
five, and `success_rate: high` does not become zero. Both become "unknown",
which cannot promote and cannot retire.
"""


class TestTierAndStateAgreement:
    def test_core_and_graduated_agree(self, tree, collected):
        tree.add("api-contract-check", tier="core", state="graduated")
        assert collected(tree)["api-contract-check"]["problems"] == []

    def test_trial_and_trial_agree(self, tree, collected):
        tree.add("api-contract-check", tier="trial", state="trial")
        assert collected(tree)["api-contract-check"]["problems"] == []

    def test_graduated_tier_with_trial_state_is_reported(self, tree, collected):
        tree.add("api-contract-check", tier="core", state="trial")
        problems = collected(tree)["api-contract-check"]["problems"]
        assert any("graduated tier but state is 'trial'" in problem for problem in problems)

    def test_every_graduated_tier_conflicts_with_a_trial_state(self, tree, collected):
        for slug, tier in [("one", "core"), ("two", "extended"), ("three", "support")]:
            tree.add(slug, tier=tier, state="trial")
        parsed = collected(tree)
        assert all(parsed[slug]["problems"] for slug in ("one", "two", "three"))

    def test_trial_tier_with_graduated_state_is_reported(self, tree, collected):
        tree.add("api-contract-check", tier="trial", state="graduated")
        assert collected(tree)["api-contract-check"]["problems"] == [
            "tier 'trial' but state is 'graduated'"
        ]

    def test_missing_tier_is_reported(self, tree, collected):
        tree.add("api-contract-check", tier=None, state="graduated")
        assert collected(tree)["api-contract-check"]["problems"] == ["missing tier"]

    def test_missing_state_is_reported(self, tree, collected):
        tree.add("api-contract-check", tier="core", state=None)
        assert collected(tree)["api-contract-check"]["problems"] == ["missing state"]

    def test_unknown_state_is_reported_and_not_guessed(self, tree, collected):
        tree.add("api-contract-check", tier="core", state="retired")
        capability = collected(tree)["api-contract-check"]
        assert any("unknown state" in problem for problem in capability["problems"])
        assert capability["state"] == "?"

    def test_contradictions_reach_the_document(self, tree):
        tree.add("api-contract-check", tier="core", state="trial")
        assert tree.run().returncode == 0
        document = tree.document
        assert "1 with inconsistent frontmatter" in document
        assert "## Inconsistent frontmatter" in document
        assert "FRONTMATTER" in document

    def test_a_clean_tree_has_no_inconsistency_section(self, tree):
        tree.add("api-contract-check", tier="core", state="graduated")
        assert tree.run().returncode == 0
        assert "## Inconsistent frontmatter" not in tree.document


class TestUnreadableValues:
    def test_non_numeric_uses_is_reported_and_treated_as_unknown(self, tree, collected):
        tree.add("api-contract-check", uses="many")
        capability = collected(tree)["api-contract-check"]
        assert capability["uses"] == 0
        assert any("uses is not an integer" in problem for problem in capability["problems"])

    def test_negative_uses_is_reported_and_treated_as_unknown(self, tree, collected):
        tree.add("api-contract-check", uses=-3)
        capability = collected(tree)["api-contract-check"]
        assert capability["uses"] == 0
        assert any("uses is negative" in problem for problem in capability["problems"])

    def test_non_numeric_success_rate_is_reported_and_treated_as_ungraded(self, tree, collected):
        tree.add("api-contract-check", uses=8, success_rate="high")
        capability = collected(tree)["api-contract-check"]
        assert capability["success_rate"] is None
        assert any("success_rate is not a number" in p for p in capability["problems"])

    def test_success_rate_above_one_is_rejected_not_clamped(self, tree, collected):
        tree.add("api-contract-check", uses=8, success_rate=1.5)
        capability = collected(tree)["api-contract-check"]
        assert capability["success_rate"] is None
        assert any("outside 0.0-1.0" in problem for problem in capability["problems"])

    def test_negative_success_rate_is_rejected_not_clamped(self, tree, collected):
        tree.add("api-contract-check", uses=8, success_rate=-0.2)
        assert collected(tree)["api-contract-check"]["success_rate"] is None

    def test_an_unreadable_score_decides_nothing(self, tree, flags_of):
        """The failure path that matters: a broken score sitting on a capability
        with plenty of uses must not be read as a failure, and must not be read
        as a success either."""
        tree.add("api-contract-check", tier="trial", state="trial", uses=10, success_rate="n/a")
        flags = flags_of(tree, "api-contract-check")
        assert "RETIRE" not in flags
        assert "PROMOTE" not in flags
        assert "FRONTMATTER" in flags
        assert "UNSCORED" in flags


class TestDescription:
    def test_takes_the_first_sentence_only(self, tree, collected):
        tree.add("api-contract-check",
                 description="Compares the shipped schema against the contract. Then explains it.")
        assert collected(tree)["api-contract-check"]["description"] == (
            "Compares the shipped schema against the contract."
        )

    def test_strips_surrounding_quotes(self, tree, collected):
        tree.add("api-contract-check", description='"Compares two schemas."')
        assert collected(tree)["api-contract-check"]["description"] == "Compares two schemas."

    def test_reads_a_block_scalar(self, tree, collected):
        tree.add_raw("block-form/SKILL.md", (
            "---\n"
            "name: block-form\n"
            "description: |\n"
            "  Walks the migration history.\n"
            "  And keeps going on a second line.\n"
            "tier: core\n"
            "state: graduated\n"
            "---\n\n# block-form\n"
        ))
        assert collected(tree)["block-form"]["description"] == "Walks the migration history."

    def test_truncates_a_long_unbroken_description(self, tree, collected):
        tree.add("api-contract-check", description="word " * 60)
        description = collected(tree)["api-contract-check"]["description"]
        assert description.endswith("...")
        assert len(description) <= 95

    def test_a_missing_description_says_so(self, tree, collected):
        tree.add("api-contract-check", description=None)
        assert collected(tree)["api-contract-check"]["description"] == "(no description)"


class TestWhatCountsAsACapability:
    def test_an_archived_directory_is_invisible(self, tree, collected):
        tree.add("still-here")
        tree.add("moved-out", subdir="_archived")
        assert set(collected(tree)) == {"still-here"}

    def test_a_dot_directory_is_invisible(self, tree, collected):
        tree.add("still-here")
        tree.add("hidden-one", subdir=".cache")
        assert set(collected(tree)) == {"still-here"}

    def test_a_directory_without_a_manifest_is_skipped(self, tree, collected):
        tree.add("still-here")
        (tree.root / "notes").mkdir()
        (tree.root / "notes" / "README.md").write_text("just notes\n", encoding="utf-8")
        assert set(collected(tree)) == {"still-here"}

    def test_a_manifest_without_frontmatter_is_skipped(self, tree, collected):
        tree.add("still-here")
        tree.add_raw("prose-only/SKILL.md", "# prose-only\n\nNo frontmatter here.\n")
        assert set(collected(tree)) == {"still-here"}

    def test_a_nested_capability_is_not_collected(self, tree, collected):
        """The layout contract is flat: one directory per capability, directly
        under the root. A nested tree would make two capabilities with the same
        leaf name indistinguishable in the registry."""
        tree.add("still-here")
        tree.add("buried", subdir="group")
        assert set(collected(tree)) == {"still-here"}

    def test_recorded_paths_are_relative_to_the_root(self, tree, collected):
        tree.add("still-here")
        assert collected(tree)["still-here"]["path"] == "still-here/SKILL.md"

    def test_recorded_paths_reach_the_document(self, tree):
        tree.add("still-here")
        assert tree.run().returncode == 0
        assert "`still-here/SKILL.md`" in tree.document
        assert str(tree.root) not in tree.document
