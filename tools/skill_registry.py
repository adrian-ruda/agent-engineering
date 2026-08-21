#!/usr/bin/env python3
"""
skill_registry.py — regenerate the capability registry from frontmatter.

A capability is a directory holding a `SKILL.md` whose YAML frontmatter carries
its lifecycle state. This script reads that frontmatter — the real one, on disk,
at the moment it is asked — and writes one Markdown registry: an index of what
exists, grouped by domain, annotated with recorded use and with the lifecycle
flags that follow from it.

The registry is generated, never hand-edited. Drift between the document and the
tree is impossible by construction, because the document is not the source.

Modes
  (default)         write the registry
  --dry-run         print it to stdout, write nothing
  --check           exit 1 if the registry on disk is stale
  --refresh-quiet   rewrite only if stale, always exit 0 (for a session hook)

Everything repository-specific — the domain taxonomy and the lifecycle
thresholds — lives in the policy file (`--policy`), never in this script. The
script is the mechanism; the policy file is the policy.

Exit codes
  0  ok (or up to date)
  1  stale registry under --check, or any operational error
"""
import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

DEFAULT_ROOT = "capabilities"
REGISTRY_FILENAME = "capability-registry.md"
CAPABILITY_FILENAME = "SKILL.md"

# The one line that legitimately changes on every run. It is excluded from every
# staleness comparison: if it were not, `--check` would report every registry as
# stale one day after it was written, and a check that always fails is a check
# everyone learns to skip.
DATE_LINE_PREFIX = "Last generated:"

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# A tier names what a capability is trusted with. A state names where it is in
# the lifecycle. They are two fields on purpose, and they can disagree — which
# is the whole reason the registry checks them against each other.
GRADUATED_TIERS = frozenset({"core", "extended", "support"})
TRIAL_TIER = "trial"
STATES = frozenset({"graduated", "trial"})

DEFAULT_PROMOTE_USES = 5
DEFAULT_PROMOTE_RATE = 0.70
DEFAULT_RETIRE_USES = 3
DEFAULT_RETIRE_RATE = 0.30
DEFAULT_FALLBACK_DOMAIN = "Uncategorised"

DESCRIPTION_MAX = 95
BLOCK_SCALAR_MARKERS = {"|", ">", "|-", ">-", "|+", ">+", ""}


class PolicyError(Exception):
    """The policy file could not be understood. Never recovered from silently:
    falling back to 'no taxonomy, default thresholds' would produce a registry
    that looks right and answers a different question than the one asked."""


# ── frontmatter ──────────────────────────────────────────────────────────────

def get_field(body: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}\s*:\s*(.+)$", body, re.MULTILINE)
    if not match:
        return None
    value = match.group(1).strip().strip('"').strip("'")
    return value if value not in ("", "null", "None", "~") else None


def extract_description(body: str) -> str:
    """First sentence of `description:`, including when it is a block scalar."""
    lines = body.split("\n")
    for index, line in enumerate(lines):
        if not line.startswith("description:"):
            continue
        rest = line[len("description:"):].strip()
        if rest in BLOCK_SCALAR_MARKERS:
            for following in lines[index + 1:]:
                if following.strip() == "":
                    continue
                if not following.startswith((" ", "\t")):
                    break
                rest = following.strip()
                break
        rest = rest.strip('"').strip("'")
        first = re.split(r"(?<=[.。])\s", rest)[0]
        if len(first) > DESCRIPTION_MAX:
            first = first[:DESCRIPTION_MAX - 3].rstrip() + "..."
        return first or "(no description)"
    return "(no description)"


def _parse_uses(raw: str | None, problems: list[str]) -> int:
    if raw is None:
        return 0
    try:
        parsed = int(raw)
    except ValueError:
        problems.append(f"uses is not an integer: {raw!r}")
        return 0
    if parsed < 0:
        problems.append(f"uses is negative: {raw!r}")
        return 0
    return parsed


def _parse_success_rate(raw: str | None, problems: list[str]) -> float | None:
    """Returns None both for 'absent' and for 'unreadable', and flags the
    second. None is not zero: a capability nobody graded has no score, and a
    missing score must never be able to retire anything."""
    if raw is None:
        return None
    try:
        parsed = float(raw)
    except ValueError:
        problems.append(f"success_rate is not a number: {raw!r}")
        return None
    if not 0.0 <= parsed <= 1.0:
        problems.append(f"success_rate is outside 0.0-1.0: {raw!r}")
        return None
    return parsed


def _consistency_problems(tier: str | None, state: str | None) -> list[str]:
    problems = []
    if tier is None:
        problems.append("missing tier")
    if state is None:
        problems.append("missing state")
    elif state not in STATES:
        problems.append(f"unknown state: {state!r}")
    if tier in GRADUATED_TIERS and state == "trial":
        problems.append(f"tier {tier!r} is a graduated tier but state is 'trial'")
    if tier == TRIAL_TIER and state == "graduated":
        problems.append("tier 'trial' but state is 'graduated'")
    return problems


def parse_capability(path: Path, root: Path) -> dict | None:
    """Parse one `SKILL.md`. Returns None when the file carries no frontmatter
    at all — that is a document, not a capability."""
    content = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(content)
    if not match:
        return None
    body = match.group(1)

    tier = get_field(body, "tier")
    state = get_field(body, "state")
    problems = _consistency_problems(tier, state)
    uses = _parse_uses(get_field(body, "uses"), problems)
    success_rate = _parse_success_rate(get_field(body, "success_rate"), problems)

    return {
        "slug": path.parent.name,
        "tier": tier or "?",
        "state": state if state in STATES else "?",
        "uses": uses,
        "success_rate": success_rate,
        "problems": problems,
        "description": extract_description(body),
        # Relative to the scanned root on purpose. A registry that embeds
        # absolute paths differs on every machine, and a document that never
        # compares equal cannot be checked for staleness.
        "path": path.relative_to(root).as_posix(),
    }


def collect(root: Path) -> list[dict]:
    """Every immediate child directory of `root` holding a `SKILL.md`.

    Directories whose name starts with `_` or `.` are skipped. That is the
    archive: retiring a capability is `git mv name/ _archived/name/`, so it
    leaves the registry because of where it sits, not because of a flag someone
    remembered to set — and it comes back with the same command backwards.
    """
    capabilities = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name.startswith(("_", ".")):
            continue
        manifest = entry / CAPABILITY_FILENAME
        if not manifest.is_file():
            continue
        parsed = parse_capability(manifest, root)
        if parsed is not None:
            capabilities.append(parsed)
    return capabilities


# ── policy ───────────────────────────────────────────────────────────────────

def default_policy() -> dict:
    return {
        "promote": {"uses": DEFAULT_PROMOTE_USES, "success_rate": DEFAULT_PROMOTE_RATE},
        "retire": {"uses": DEFAULT_RETIRE_USES, "success_rate": DEFAULT_RETIRE_RATE},
        "domains": [],
        "fallback_domain": DEFAULT_FALLBACK_DOMAIN,
    }


def _validated_threshold(raw: object, name: str, base: dict) -> dict:
    if raw is None:
        return dict(base)
    if not isinstance(raw, dict):
        raise PolicyError(f"{name!r} must be an object with 'uses' and 'success_rate'")
    result = dict(base)
    if "uses" in raw:
        value = raw["uses"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PolicyError(f"{name}.uses must be a non-negative integer")
        result["uses"] = value
    if "success_rate" in raw:
        value = raw["success_rate"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise PolicyError(f"{name}.success_rate must be a number")
        if not 0.0 <= float(value) <= 1.0:
            raise PolicyError(f"{name}.success_rate must be between 0.0 and 1.0")
        result["success_rate"] = float(value)
    return result


def load_policy(path: Path | None) -> dict:
    """Read the policy file, or return the defaults when none was given.

    Every failure raises. The dangerous case is a policy file that is present
    but unreadable: a taxonomy silently collapsing into one bucket looks like a
    small repository rather than like a broken configuration.
    """
    policy = default_policy()
    if path is None:
        return policy

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PolicyError(f"policy file not found: {path}") from None
    except OSError as exc:
        raise PolicyError(f"policy file could not be read: {path} ({exc})") from None
    except json.JSONDecodeError as exc:
        raise PolicyError(f"policy file is not valid JSON: {path} ({exc})") from None

    if not isinstance(raw, dict):
        raise PolicyError("the policy file must contain a JSON object")

    policy["promote"] = _validated_threshold(raw.get("promote"), "promote", policy["promote"])
    policy["retire"] = _validated_threshold(raw.get("retire"), "retire", policy["retire"])

    fallback = raw.get("fallback_domain", policy["fallback_domain"])
    if not isinstance(fallback, str) or not fallback.strip():
        raise PolicyError("'fallback_domain' must be a non-empty string")
    policy["fallback_domain"] = fallback

    domains_raw = raw.get("domains", [])
    if not isinstance(domains_raw, list):
        raise PolicyError("'domains' must be a list")

    domains: list[dict] = []
    seen: set[str] = {fallback}
    for position, item in enumerate(domains_raw):
        where = f"domains[{position}]"
        if not isinstance(item, dict):
            raise PolicyError(f"{where} must be an object with 'label' and 'match'")
        label = item.get("label")
        if not isinstance(label, str) or not label.strip():
            raise PolicyError(f"{where}.label must be a non-empty string")
        if label in seen:
            raise PolicyError(
                f"{where}.label duplicates an earlier label or the fallback: {label!r} — "
                "two sections sharing one name silently merge"
            )
        seen.add(label)
        match = item.get("match")
        if not isinstance(match, list) or not match:
            raise PolicyError(f"{where}.match must be a non-empty list of substrings")
        for needle in match:
            if not isinstance(needle, str) or not needle:
                raise PolicyError(f"{where}.match entries must be non-empty strings")
        domains.append({"label": label, "match": list(match)})

    policy["domains"] = domains
    return policy


def classify(slug: str, policy: dict) -> str:
    """First domain whose match list hits the slug wins, so the order in the
    policy file is meaningful: most specific first, fallback last."""
    for domain in policy["domains"]:
        if any(needle in slug for needle in domain["match"]):
            return domain["label"]
    return policy["fallback_domain"]


# ── lifecycle flags ──────────────────────────────────────────────────────────

def flags_for(capability: dict, policy: dict) -> list[str]:
    """The lifecycle verdict for one capability.

    There is no time input here, and that is the point. Age, staleness and
    'last used' are not arguments to this function, because a capability nobody
    reached for has not failed — it has not been given the chance to. Only a
    measured record moves anything.
    """
    flags = []
    uses = capability["uses"]
    rate = capability["success_rate"]
    promote, retire = policy["promote"], policy["retire"]

    if (
        capability["state"] == "trial"
        and rate is not None
        and uses >= promote["uses"]
        and rate >= promote["success_rate"]
    ):
        flags.append("PROMOTE")

    if rate is not None and uses >= retire["uses"] and rate < retire["success_rate"]:
        flags.append("RETIRE")

    # Used often enough to have earned a verdict, and never graded. A plain
    # invocation counter would have reported this as unbroken success.
    if rate is None and uses >= promote["uses"]:
        flags.append("UNSCORED")

    if capability["problems"]:
        flags.append("FRONTMATTER")

    return flags


# ── document ─────────────────────────────────────────────────────────────────

def format_entry(capability: dict, policy: dict) -> str:
    rate = capability["success_rate"]
    rate_text = f"{rate:.2f}" if rate is not None else "n/a"
    suffix = "".join(f" · {flag}" for flag in flags_for(capability, policy))
    return (
        f"- **{capability['slug']}** `[{capability['state']}]` "
        f"`{capability['uses']}·{rate_text}` → {capability['description']}"
        f"{suffix} · `{capability['path']}`"
    )


def build_document(
    capabilities: list[dict],
    policy: dict,
    source_label: str,
    today: str | None = None,
) -> str:
    today = today or date.today().isoformat()
    promote, retire = policy["promote"], policy["retire"]

    graduated = sum(1 for c in capabilities if c["state"] == "graduated")
    trial = sum(1 for c in capabilities if c["state"] == "trial")
    scored = sum(1 for c in capabilities if c["success_rate"] is not None)
    used = sum(1 for c in capabilities if c["uses"] > 0)
    flags = {c["slug"]: flags_for(c, policy) for c in capabilities}
    to_promote = [c for c in capabilities if "PROMOTE" in flags[c["slug"]]]
    to_retire = [c for c in capabilities if "RETIRE" in flags[c["slug"]]]
    unscored = [c for c in capabilities if "UNSCORED" in flags[c["slug"]]]
    broken = [c for c in capabilities if c["problems"]]

    out = [
        "# Capability registry",
        "",
        "> Generated by `tools/skill_registry.py` from the frontmatter of every",
        f"> `{source_label}/*/{CAPABILITY_FILENAME}`. Do not edit by hand — the next run",
        "> overwrites it. To change the grouping or the thresholds, edit the policy",
        "> file, not this document and not the script.",
        f"> {DATE_LINE_PREFIX} {today}",
        "",
        "## Summary",
        "",
        f"- **{len(capabilities)} capabilities** · {graduated} graduated · {trial} on trial"
        + (f" · {len(broken)} with inconsistent frontmatter" if broken else ""),
        f"- **Recorded use:** {used} with `uses > 0` · {scored} carrying a success rate"
        f" · {len(capabilities) - scored} never graded",
        f"- **Candidates:** {len(to_promote)} to promote (`uses >= {promote['uses']}`"
        f" and `success_rate >= {promote['success_rate']:.2f}`)"
        f" · {len(to_retire)} to retire (`uses >= {retire['uses']}`"
        f" and `success_rate < {retire['success_rate']:.2f}`)"
        + (f" · {len(unscored)} used but never graded" if unscored else ""),
        "",
        "## Legend",
        "",
        "`name [state] uses·success_rate → when to use it · path`",
        "",
        "- `PROMOTE` — on trial and past both promotion thresholds.",
        "- `RETIRE` — measured failure: enough graded runs, and a rate below the floor.",
        f"- `UNSCORED` — used at least {promote['uses']} times and never graded. Nothing can",
        "  be concluded about it, which is itself the finding.",
        "- `FRONTMATTER` — the file contradicts itself; see the section at the end.",
        "",
        "Time is not an input. A capability with no recorded use is not a failing",
        "capability, so nothing here retires on age.",
        "",
        "---",
        "",
    ]

    order = [domain["label"] for domain in policy["domains"]] + [policy["fallback_domain"]]
    for label in order:
        group = sorted(
            (c for c in capabilities if classify(c["slug"], policy) == label),
            key=lambda c: c["slug"],
        )
        if not group:
            continue
        out.append(f"## {label} ({len(group)})")
        out.extend(format_entry(c, policy) for c in group)
        out.append("")

    if broken:
        out.extend(["---", "", "## Inconsistent frontmatter", ""])
        for capability in sorted(broken, key=lambda c: c["slug"]):
            out.append(
                f"- **{capability['slug']}** — " + "; ".join(capability["problems"])
                + f" · `{capability['path']}`"
            )
        out.append("")

    return "\n".join(out)


def comparable(text: str) -> str:
    """The document minus the one line that changes on every run."""
    return re.sub(rf"{re.escape(DATE_LINE_PREFIX)}.*", "", text).strip()


def is_stale(existing: str, generated: str) -> bool:
    return comparable(existing) != comparable(generated)


# ── cli ──────────────────────────────────────────────────────────────────────

def resolve_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    return Path(os.environ.get("CAPABILITY_ROOT") or DEFAULT_ROOT)


def resolve_out(explicit: str | None, root: Path) -> Path:
    if explicit:
        return Path(explicit)
    from_env = os.environ.get("CAPABILITY_REGISTRY")
    if from_env:
        return Path(from_env)
    return root.parent / REGISTRY_FILENAME


def resolve_policy(explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit)
    from_env = os.environ.get("CAPABILITY_POLICY")
    return Path(from_env) if from_env else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Regenerate the capability registry from frontmatter.",
    )
    parser.add_argument(
        "--root",
        help="directory holding one subdirectory per capability "
             f"(default: $CAPABILITY_ROOT, else ./{DEFAULT_ROOT})",
    )
    parser.add_argument(
        "--out",
        help="registry file to write "
             f"(default: $CAPABILITY_REGISTRY, else <root>/../{REGISTRY_FILENAME})",
    )
    parser.add_argument(
        "--policy",
        help="JSON file with the domain taxonomy and the lifecycle thresholds "
             "(default: $CAPABILITY_POLICY, else the built-in defaults)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print to stdout, write nothing")
    mode.add_argument("--check", action="store_true",
                      help="exit 1 if the registry on disk is stale")
    mode.add_argument("--refresh-quiet", action="store_true",
                      help="rewrite only if stale, always exit 0")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    root = resolve_root(args.root)
    if not root.is_dir():
        print(f"error: capability root does not exist: {root}", file=sys.stderr)
        return 1

    try:
        policy = load_policy(resolve_policy(args.policy))
    except PolicyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        capabilities = collect(root)
    except OSError as exc:
        print(f"error: could not read {root}: {exc}", file=sys.stderr)
        return 1

    if not capabilities:
        # An empty result is never reported as a clean registry. Zero parsed
        # capabilities means the scan did not happen: wrong root, wrong
        # filename, or frontmatter that stopped parsing.
        print(f"error: no capability parsed under {root} — expected "
              f"<root>/<name>/{CAPABILITY_FILENAME} with YAML frontmatter", file=sys.stderr)
        return 1

    document = build_document(capabilities, policy, root.name)

    if args.dry_run:
        print(document)
        return 0

    out = resolve_out(args.out, root)
    existing = out.read_text(encoding="utf-8") if out.is_file() else ""

    if args.check:
        if not is_stale(existing, document):
            print(f"registry is up to date: {out}")
            return 0
        print(f"error: registry is stale: {out} — regenerate it with "
              f"{Path(__file__).name}", file=sys.stderr)
        return 1

    if args.refresh_quiet:
        if is_stale(existing, document):
            out.write_text(document.rstrip() + "\n", encoding="utf-8")
            print(f"registry regenerated: {out}", file=sys.stderr)
        return 0

    out.write_text(document.rstrip() + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(capabilities)} capabilities)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
