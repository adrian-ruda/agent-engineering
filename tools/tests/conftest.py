"""
Shared fixtures for the capability registry tests.

Every test builds a throwaway capability tree under `tmp_path`. Nothing here
reads or writes a real registry, and no test depends on another test's tree.

Two ways in, on purpose:

* `registry` — the module itself, for asserting on parsed structures and on
  lifecycle verdicts.
* `tree.run(...)` — the script as a subprocess, for asserting on the part users
  actually consume: exit codes, stdout, stderr and the file on disk.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parent.parent
SCRIPT = TOOLS_DIR / "skill_registry.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("skill_registry_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def registry():
    return _load_module()


class Tree:
    """A capability tree under `tmp_path`, plus the two ways to run against it."""

    def __init__(self, base: Path):
        self.base = base
        self.root = base / "capabilities"
        self.root.mkdir(parents=True, exist_ok=True)
        self.out = base / "capability-registry.md"
        self.policy_path: Path | None = None

    def add(
        self,
        slug: str,
        *,
        tier: str | None = "core",
        state: str | None = "graduated",
        uses=None,
        success_rate=None,
        description: str | None = "does one thing well.",
        body: str = "",
        extra: dict | None = None,
        subdir: str | None = None,
    ) -> Path:
        """Write one `SKILL.md`. Any field passed as None is left out entirely,
        which is how 'the author never wrote that key' is expressed."""
        directory = (self.root / subdir / slug) if subdir else (self.root / slug)
        directory.mkdir(parents=True, exist_ok=True)

        lines = ["---", f"name: {slug}"]
        if description is not None:
            lines.append(f"description: {description}")
        if tier is not None:
            lines.append(f"tier: {tier}")
        if state is not None:
            lines.append(f"state: {state}")
        if uses is not None:
            lines.append(f"uses: {uses}")
        if success_rate is not None:
            lines.append(f"success_rate: {success_rate}")
        for key, value in (extra or {}).items():
            lines.append(f"{key}: {value}")
        lines += ["---", "", body or f"# {slug}"]

        manifest = directory / "SKILL.md"
        manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return manifest

    def add_raw(self, relative: str, text: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def write_policy(self, policy, name: str = "policy.json") -> Path:
        path = self.base / name
        path.write_text(
            policy if isinstance(policy, str) else json.dumps(policy, indent=2),
            encoding="utf-8",
        )
        self.policy_path = path
        return path

    def run(self, *args, root=..., out=..., policy=..., env_extra=None):
        argv = [sys.executable, str(SCRIPT)]
        root = self.root if root is ... else root
        out = self.out if out is ... else out
        policy = self.policy_path if policy is ... else policy
        if root is not None:
            argv += ["--root", str(root)]
        if out is not None:
            argv += ["--out", str(out)]
        if policy is not None:
            argv += ["--policy", str(policy)]
        argv += [str(arg) for arg in args]

        # The tool reads $CAPABILITY_* as fallbacks. A value leaking in from the
        # developer's shell would make these tests pass or fail for reasons that
        # have nothing to do with the code.
        env = {k: v for k, v in os.environ.items() if not k.startswith("CAPABILITY_")}
        env.update(env_extra or {})
        return subprocess.run(argv, capture_output=True, text=True, cwd=self.base, env=env)

    @property
    def document(self) -> str:
        return self.out.read_text(encoding="utf-8")


@pytest.fixture
def tree(tmp_path):
    return Tree(tmp_path)


@pytest.fixture
def collected(registry):
    def _collect(tree: Tree) -> dict:
        return {capability["slug"]: capability for capability in registry.collect(tree.root)}
    return _collect


@pytest.fixture
def flags_of(registry, collected):
    def _flags(tree: Tree, slug: str, policy: dict | None = None) -> list[str]:
        return registry.flags_for(collected(tree)[slug], policy or registry.default_policy())
    return _flags
