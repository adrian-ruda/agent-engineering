"""
The policy file: the domain taxonomy and the lifecycle thresholds.

Grouping is repository-specific — the domains one team needs are noise to
another — so none of it lives in the script. The script holds the mechanism and
the policy file holds the policy, which is also why the script ships with no
taxonomy at all: without a policy file every capability lands in one section,
which is honest about knowing nothing rather than imposing someone else's
categories.

Matching is by substring against the capability's directory name, first match
wins, so order in the file is meaningful. Everything about the file is
validated up front and every failure is fatal. The tempting alternative —
ignore a malformed policy and carry on with defaults — produces a registry that
is well-formed, plausible, and grouped by rules nobody wrote.
"""
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "policy.example.json"


class TestGrouping:
    def test_without_a_policy_everything_lands_in_one_section(self, tree):
        tree.add("schema-drift-check")
        tree.add("release-notes-draft")
        assert tree.run(policy=None).returncode == 0
        assert "## Uncategorised (2)" in tree.document

    def test_capabilities_are_grouped_by_their_domain(self, tree):
        tree.write_policy({"domains": [
            {"label": "Data", "match": ["schema", "migration"]},
            {"label": "Delivery", "match": ["release"]},
        ]})
        tree.add("schema-drift-check")
        tree.add("migration-plan")
        tree.add("release-notes-draft")
        assert tree.run().returncode == 0
        assert "## Data (2)" in tree.document
        assert "## Delivery (1)" in tree.document

    def test_the_first_matching_domain_wins(self, tree):
        """`schema-test-helper` matches both. It belongs to whichever domain the
        author listed first, which is the only rule that makes a hand-ordered
        taxonomy predictable."""
        tree.write_policy({"domains": [
            {"label": "Data", "match": ["schema"]},
            {"label": "Testing", "match": ["test"]},
        ]})
        tree.add("schema-test-helper")
        assert tree.run().returncode == 0
        assert "## Data (1)" in tree.document
        assert "## Testing" not in tree.document

    def test_reordering_the_policy_moves_the_capability(self, tree):
        tree.write_policy({"domains": [
            {"label": "Testing", "match": ["test"]},
            {"label": "Data", "match": ["schema"]},
        ]})
        tree.add("schema-test-helper")
        assert tree.run().returncode == 0
        assert "## Testing (1)" in tree.document

    def test_an_unmatched_capability_falls_through_to_the_fallback(self, tree):
        tree.write_policy({
            "fallback_domain": "General",
            "domains": [{"label": "Data", "match": ["schema"]}],
        })
        tree.add("unrelated-thing")
        assert tree.run().returncode == 0
        assert "## General (1)" in tree.document

    def test_the_fallback_section_comes_last(self, tree):
        tree.write_policy({
            "fallback_domain": "General",
            "domains": [{"label": "Data", "match": ["schema"]}],
        })
        tree.add("schema-drift-check")
        tree.add("unrelated-thing")
        assert tree.run().returncode == 0
        document = tree.document
        assert document.index("## Data") < document.index("## General")

    def test_a_domain_with_no_members_produces_no_section(self, tree):
        tree.write_policy({"domains": [
            {"label": "Data", "match": ["schema"]},
            {"label": "Nobody lives here", "match": ["zzz"]},
        ]})
        tree.add("schema-drift-check")
        assert tree.run().returncode == 0
        assert "Nobody lives here" not in tree.document

    def test_capabilities_are_sorted_inside_a_section(self, tree):
        tree.write_policy({"domains": [{"label": "Data", "match": ["schema"]}]})
        tree.add("schema-zebra")
        tree.add("schema-alpha")
        assert tree.run().returncode == 0
        document = tree.document
        assert document.index("**schema-alpha**") < document.index("**schema-zebra**")


class TestThresholdsInThePolicy:
    def test_custom_thresholds_are_printed_in_the_document(self, tree):
        tree.write_policy({"promote": {"uses": 12, "success_rate": 0.9},
                           "retire": {"uses": 2, "success_rate": 0.5}})
        tree.add("one")
        assert tree.run().returncode == 0
        assert "`uses >= 12` and `success_rate >= 0.90`" in tree.document
        assert "`uses >= 2` and `success_rate < 0.50`" in tree.document

    def test_a_partial_threshold_keeps_the_other_default(self, tree, registry):
        policy = registry.load_policy(tree.write_policy({"promote": {"uses": 9}}))
        assert policy["promote"]["uses"] == 9
        assert policy["promote"]["success_rate"] == 0.70

    def test_custom_thresholds_change_which_capabilities_are_flagged(self, tree):
        tree.write_policy({"retire": {"uses": 2, "success_rate": 0.95}})
        tree.add("harsh", tier="trial", state="trial", uses=2, success_rate=0.9)
        assert tree.run().returncode == 0
        assert "1 to retire" in tree.document


class TestPolicyValidation:
    def _rejects(self, tree, policy, fragment):
        tree.write_policy(policy)
        tree.add("one")
        result = tree.run()
        assert result.returncode == 1, result.stdout
        assert fragment in result.stderr, result.stderr
        assert not tree.out.exists()

    def test_invalid_json_is_fatal(self, tree):
        self._rejects(tree, "{not json at all", "not valid JSON")

    def test_a_json_array_is_not_a_policy(self, tree):
        self._rejects(tree, "[]", "must contain a JSON object")

    def test_domains_must_be_a_list(self, tree):
        self._rejects(tree, {"domains": {"Data": ["schema"]}}, "'domains' must be a list")

    def test_a_domain_must_be_an_object(self, tree):
        self._rejects(tree, {"domains": ["Data"]}, "must be an object")

    def test_a_domain_needs_a_label(self, tree):
        self._rejects(tree, {"domains": [{"match": ["schema"]}]}, "label must be a non-empty string")

    def test_a_domain_needs_a_non_empty_match_list(self, tree):
        self._rejects(tree, {"domains": [{"label": "Data", "match": []}]},
                      "match must be a non-empty list")

    def test_match_entries_must_be_strings(self, tree):
        self._rejects(tree, {"domains": [{"label": "Data", "match": [7]}]},
                      "match entries must be non-empty strings")

    def test_a_duplicated_label_is_fatal(self, tree):
        """Two sections with the same heading merge on the page and hide half
        the taxonomy, while the file that produced them looks fine."""
        self._rejects(tree, {"domains": [
            {"label": "Data", "match": ["schema"]},
            {"label": "Data", "match": ["migration"]},
        ]}, "duplicates an earlier label")

    def test_a_label_colliding_with_the_fallback_is_fatal(self, tree):
        self._rejects(tree, {
            "fallback_domain": "General",
            "domains": [{"label": "General", "match": ["schema"]}],
        }, "duplicates an earlier label or the fallback")

    def test_an_empty_fallback_name_is_fatal(self, tree):
        self._rejects(tree, {"fallback_domain": "   "}, "non-empty string")

    def test_a_negative_use_threshold_is_fatal(self, tree):
        self._rejects(tree, {"promote": {"uses": -1}}, "non-negative integer")

    def test_a_non_integer_use_threshold_is_fatal(self, tree):
        self._rejects(tree, {"retire": {"uses": 2.5}}, "non-negative integer")

    def test_a_rate_threshold_above_one_is_fatal(self, tree):
        self._rejects(tree, {"promote": {"success_rate": 1.4}}, "between 0.0 and 1.0")

    def test_a_threshold_that_is_not_an_object_is_fatal(self, tree):
        self._rejects(tree, {"promote": 5}, "must be an object")

    def test_a_missing_policy_file_is_fatal(self, tree):
        tree.add("one")
        result = tree.run(policy=tree.base / "no-such-policy.json")
        assert result.returncode == 1
        assert "policy file not found" in result.stderr


class TestShippedExample:
    """The example under `fixtures/` is the copy-paste starting point, so it is
    held to the same validation as any other policy file."""

    def test_the_example_policy_is_valid(self, registry):
        policy = registry.load_policy(FIXTURE)
        assert [domain["label"] for domain in policy["domains"]][0] == "Data & migrations"
        assert policy["fallback_domain"] == "General"

    def test_the_example_policy_classifies_by_order(self, registry):
        policy = registry.load_policy(FIXTURE)
        assert registry.classify("schema-drift-check", policy) == "Data & migrations"
        assert registry.classify("flaky-test-triage", policy) == "Testing"
        assert registry.classify("rollback-drill", policy) == "Delivery"
        assert registry.classify("something-else", policy) == "General"

    def test_the_example_policy_drives_a_real_run(self, tree):
        tree.add("schema-drift-check")
        tree.add("flaky-test-triage")
        result = tree.run(policy=FIXTURE)
        assert result.returncode == 0
        assert "## Data & migrations (1)" in tree.document
        assert "## Testing (1)" in tree.document
