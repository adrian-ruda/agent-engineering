"""
The four modes, and the property that makes the generated registry usable in a
repository: regenerating it must not produce a diff unless something actually
changed.

    (default)         write the registry
    --dry-run         print it, write nothing
    --check           exit 1 if what is on disk is stale
    --refresh-quiet   rewrite only if stale, always exit 0

Exactly one line in the document changes on every run — the generation date —
and every staleness comparison excludes it. Without that exclusion the registry
would be rewritten by every session hook on every day, each rewrite landing in
someone's commit as a one-line diff that means nothing. People stop reading
diffs that always contain noise, which is how a real change slips through in
the same hunk.

The failure modes are the other half. A run that cannot see any capability
reports an error and writes nothing, because an empty registry and a registry
that was never scanned look identical once written.
"""
import re


def _replace_date(text: str, replacement: str) -> str:
    return re.sub(r"Last generated:.*", f"Last generated: {replacement}", text)


class TestDefaultMode:
    def test_writes_the_registry_and_reports_the_count(self, tree):
        tree.add("one")
        tree.add("two")
        result = tree.run()
        assert result.returncode == 0
        assert "(2 capabilities)" in result.stdout
        assert tree.out.is_file()

    def test_the_document_names_its_generator_and_forbids_hand_editing(self, tree):
        tree.add("one")
        assert tree.run().returncode == 0
        document = tree.document
        assert "tools/skill_registry.py" in document
        assert "Do not edit by hand" in document

    def test_regenerating_an_unchanged_tree_changes_nothing(self, tree, registry):
        tree.add("one", uses=3, success_rate=0.9)
        assert tree.run().returncode == 0
        first = tree.document
        assert tree.run().returncode == 0
        assert registry.comparable(tree.document) == registry.comparable(first)

    def test_the_generation_date_appears_exactly_once(self, tree):
        tree.add("one")
        assert tree.run().returncode == 0
        assert tree.document.count("Last generated:") == 1

    def test_a_changed_capability_changes_the_document(self, tree, registry):
        tree.add("one", uses=1, success_rate=0.5)
        assert tree.run().returncode == 0
        first = tree.document
        tree.add("one", uses=9, success_rate=0.95)
        assert tree.run().returncode == 0
        assert registry.comparable(tree.document) != registry.comparable(first)


class TestDryRun:
    def test_prints_the_document(self, tree):
        tree.add("one")
        result = tree.run("--dry-run")
        assert result.returncode == 0
        assert "# Capability registry" in result.stdout
        assert "**one**" in result.stdout

    def test_writes_nothing(self, tree):
        tree.add("one")
        assert tree.run("--dry-run").returncode == 0
        assert not tree.out.exists()

    def test_does_not_overwrite_an_existing_registry(self, tree):
        tree.add("one")
        tree.out.write_text("hand written, about to be defended\n", encoding="utf-8")
        assert tree.run("--dry-run").returncode == 0
        assert tree.document == "hand written, about to be defended\n"


class TestCheck:
    def test_passes_right_after_a_write(self, tree):
        tree.add("one")
        assert tree.run().returncode == 0
        result = tree.run("--check")
        assert result.returncode == 0
        assert "up to date" in result.stdout

    def test_fails_when_the_registry_does_not_exist_yet(self, tree):
        tree.add("one")
        result = tree.run("--check")
        assert result.returncode == 1
        assert "stale" in result.stderr

    def test_fails_after_a_capability_changed(self, tree):
        tree.add("one", uses=1, success_rate=0.5)
        assert tree.run().returncode == 0
        tree.add("one", uses=6, success_rate=0.9)
        assert tree.run("--check").returncode == 1

    def test_fails_after_a_capability_was_added(self, tree):
        tree.add("one")
        assert tree.run().returncode == 0
        tree.add("two")
        assert tree.run("--check").returncode == 1

    def test_a_different_generation_date_alone_is_not_stale(self, tree):
        tree.add("one")
        assert tree.run().returncode == 0
        tree.out.write_text(_replace_date(tree.document, "1999-12-31"), encoding="utf-8")
        assert tree.run("--check").returncode == 0

    def test_trailing_whitespace_alone_is_not_stale(self, tree):
        tree.add("one")
        assert tree.run().returncode == 0
        tree.out.write_text(tree.document + "\n\n\n", encoding="utf-8")
        assert tree.run("--check").returncode == 0

    def test_check_never_writes(self, tree):
        tree.add("one")
        tree.out.write_text("stale content\n", encoding="utf-8")
        assert tree.run("--check").returncode == 1
        assert tree.document == "stale content\n"


class TestRefreshQuiet:
    def test_rewrites_when_stale_and_still_exits_zero(self, tree):
        tree.add("one")
        tree.out.write_text("stale content\n", encoding="utf-8")
        result = tree.run("--refresh-quiet")
        assert result.returncode == 0
        assert "regenerated" in result.stderr
        assert "# Capability registry" in tree.document

    def test_creates_the_registry_when_it_is_missing(self, tree):
        tree.add("one")
        assert tree.run("--refresh-quiet").returncode == 0
        assert tree.out.is_file()

    def test_leaves_a_current_registry_untouched(self, tree):
        tree.add("one")
        assert tree.run().returncode == 0
        before = tree.out.stat().st_mtime_ns
        result = tree.run("--refresh-quiet")
        assert result.returncode == 0
        assert result.stderr == ""
        assert tree.out.stat().st_mtime_ns == before

    def test_a_stale_date_alone_does_not_trigger_a_rewrite(self, tree):
        """The property that keeps this out of every commit: a registry written
        yesterday is still current today."""
        tree.add("one")
        assert tree.run().returncode == 0
        tree.out.write_text(_replace_date(tree.document, "1999-12-31"), encoding="utf-8")
        before = tree.out.stat().st_mtime_ns
        assert tree.run("--refresh-quiet").returncode == 0
        assert tree.out.stat().st_mtime_ns == before

    def test_exits_zero_even_though_it_had_work_to_do(self, tree):
        tree.add("one")
        assert tree.run("--refresh-quiet").returncode == 0


class TestFailureModes:
    def test_a_missing_root_is_an_error(self, tree):
        result = tree.run(root=tree.base / "nowhere")
        assert result.returncode == 1
        assert "does not exist" in result.stderr

    def test_an_empty_root_is_an_error_not_an_empty_registry(self, tree):
        result = tree.run()
        assert result.returncode == 1
        assert "no capability parsed" in result.stderr
        assert not tree.out.exists()

    def test_a_root_holding_only_archived_capabilities_is_an_error(self, tree):
        tree.add("moved-out", subdir="_archived")
        assert tree.run().returncode == 1

    def test_a_root_of_unparsable_manifests_is_an_error(self, tree):
        tree.add_raw("prose-only/SKILL.md", "# prose-only\n\nNo frontmatter.\n")
        result = tree.run()
        assert result.returncode == 1
        assert "no capability parsed" in result.stderr

    def test_an_error_never_overwrites_an_existing_registry(self, tree):
        tree.out.write_text("previous registry\n", encoding="utf-8")
        assert tree.run().returncode == 1
        assert tree.document == "previous registry\n"

    def test_two_modes_at_once_is_refused(self, tree):
        tree.add("one")
        result = tree.run("--check", "--dry-run")
        assert result.returncode == 2
        assert "not allowed with" in result.stderr


class TestPathResolution:
    def _alternate_root(self, tree):
        alternate = tree.base / "alternate"
        (alternate / "elsewhere").mkdir(parents=True)
        (alternate / "elsewhere" / "SKILL.md").write_text(
            "---\nname: elsewhere\ntier: core\nstate: graduated\n---\n\n# elsewhere\n",
            encoding="utf-8",
        )
        return alternate

    def test_the_registry_defaults_to_a_sibling_of_the_root(self, tree):
        tree.add("one")
        assert tree.run(out=None).returncode == 0
        assert (tree.root.parent / "capability-registry.md").is_file()

    def test_the_root_defaults_to_capabilities_under_the_working_directory(self, tree):
        tree.add("one")
        result = tree.run(root=None, out=None)
        assert result.returncode == 0
        assert "**one**" in (tree.base / "capability-registry.md").read_text(encoding="utf-8")

    def test_the_root_can_come_from_the_environment(self, tree):
        tree.add("in-default-root")
        alternate = self._alternate_root(tree)
        result = tree.run(root=None, env_extra={"CAPABILITY_ROOT": str(alternate)})
        assert result.returncode == 0
        assert "**elsewhere**" in tree.document
        assert "in-default-root" not in tree.document

    def test_an_explicit_root_beats_the_environment(self, tree):
        tree.add("in-default-root")
        alternate = self._alternate_root(tree)
        result = tree.run(env_extra={"CAPABILITY_ROOT": str(alternate)})
        assert result.returncode == 0
        assert "**in-default-root**" in tree.document
        assert "elsewhere" not in tree.document

    def test_the_registry_path_can_come_from_the_environment(self, tree):
        tree.add("one")
        target = tree.base / "somewhere-else.md"
        result = tree.run(out=None, env_extra={"CAPABILITY_REGISTRY": str(target)})
        assert result.returncode == 0
        assert target.is_file()
        assert not tree.out.exists()
