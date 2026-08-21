# Capability lifecycle

**How a repository decides which of its own instructions are worth keeping —
and the one measurement it refuses to make.**

---

## The problem

A repository that works with coding agents accumulates instructions. Not code:
capabilities. A folder per thing the team knows how to do, each one a document
telling an agent how to do it — how this project reviews a migration, how it
writes a release note, how it triages a flaky test. Thirty of them arrive
quickly. Then sixty. On the tree this chapter is drawn from, one hundred and
sixty-nine.

Every one of them was written for a reason, and none of them announce when they
stop being worth loading. So the question arrives: **which of these still earn
their place?**

The two answers available today are both unsatisfying.

The first is to read them and decide. That works at ten capabilities and stops
working somewhere around forty, at which point the honest state of the roster is
"nobody knows" and the practical policy becomes "keep everything", which is the
same as having no policy.

The second is to count invocations. It is available, it is cheap, and it
measures the wrong thing. A counter says a capability was reached for eleven
times. It cannot say whether any of the eleven produced something the team kept.

What follows is the third answer: a counter with a threshold, where the counter
counts verdicts instead of calls, and where the threshold was deliberately built
without the one rule everybody reaches for first.

---

## The shape

A capability is a directory holding a `SKILL.md`. Its frontmatter carries its
own record:

```yaml
---
name: schema-drift-check
description: Compares the shipped schema against the declared contract.
tier: trial          # what it is trusted with: core, extended, support, trial
state: trial         # where it is in the lifecycle: trial or graduated
uses: 6              # graded runs, not invocations
success_rate: 0.83   # running mean of the verdicts on those runs
---
```

A capability occupies one of four positions:

| Position | Where it lives | Visible to the runtime | How it gets there |
|---|---|---|---|
| **staged** | `capabilities/_staging/<name>/` | no | created, not yet released to real work |
| **on trial** | `capabilities/<name>/`, `state: trial` | yes | moved out of staging; takes real work with a fallback ready |
| **graduated** | `capabilities/<name>/`, `state: graduated` | yes | passed both promotion thresholds |
| **archived** | `capabilities/_archived/<name>/` | no | measured failure, or orphaned |

Two of those four positions are enforced by the filesystem rather than by a
field, and that is not incidental. The registry generator never scans a
directory whose name begins with `_`, so a staged or archived capability is
invisible **because of where it sits**, not because someone remembered to set a
flag. Visibility is structural; trust level is a field.

That split decides what each transition costs. Promotion is a two-field flip —
cheap, and cheap to undo. Retirement is `git mv name/ _archived/name/` — never
`rm`, so the thing that was retired is still readable, still in history, and one
command away from coming back. Nothing in this system deletes.

---

## Three decisions

The scoring is the least interesting part. These three are what make it
different from an analytics dashboard.

### 1. The number comes from a verdict, not from a call count

`success_rate` is not "times it worked over times it ran", because nothing in an
agent run knows whether it worked. It is the running mean of a **verdict issued
by an adversarial close-out review** — a review at the end of the session whose
job is to find what the session got wrong, run by an actor that did not do the
work.

Three values, and only three:

| Verdict | Value | What it means |
|---|---|---|
| clean | `1.0` | the capability produced what was asked, and the review found nothing to correct |
| partial | `0.5` | it produced something usable, after correction — or it covered part of the task and a human covered the rest |
| failed | `0.0` | it was loaded, it ran, and the result was thrown away |

`uses` counts graded runs. Not invocations — graded runs. The two fields cannot
drift apart, because a run that was never judged never increments either.

The middle row is doing more work than it looks. Without it the metric is a coin
flip and everything lands near `1.0`, since almost nothing fails outright. With
it, a capability that reliably needs correcting converges toward `0.5` and never
reaches the promotion floor of `0.70` — which is the correct answer for
something that helps but does not stand on its own.

**This is the difference between analytics and evaluation.** Analytics tells you
a capability was reached for eleven times. Evaluation tells you what happened on
those eleven occasions. Only the second can retire anything, and a system that
collects the first while presenting it as the second will retire the wrong
things with great confidence.

The distinction shows up in the output. `uses > 0` with no `success_rate` is a
real, reportable state — the registry flags it `UNSCORED`: used, never judged,
nothing may be concluded. An invocation counter would have reported that same
capability as eleven unbroken successes.

### 2. The rule we refused to write

Every system like this one grows the same rule: **unused for N days, delete it.**
It is the first thing anyone proposes, it is trivial to implement, the data is
already there, and it produces a satisfying number of deletions on the first
run.

It is forbidden here, by design.

The retirement check takes no date. It reads no `last_used` field. It has no
clock and no access to one. A capability that has not been used has not failed —
it has not been given the chance to fail, and the reason it sat idle is a fact
about which work came through the door, not a fact about the capability. Bench
time is not evidence. The best thing on the roster is still the best thing on
the roster during a month it was not needed.

Exactly two conditions retire a capability:

- **Measured failure** — `uses >= 3` and `success_rate < 0.30`. Three graded
  runs is enough that it is not one bad afternoon, and a rate that low means
  correcting the output costs more than the capability saves.
- **Orphaned** — nothing in the repository refers to it any more. That is not a
  judgement about quality; it is the observation that the path which used to
  reach it no longer exists.

Promotion is the mirror: `state: trial`, `uses >= 5`, `success_rate >= 0.70`.

Those thresholds are not statistics, and are not presented as such. Five graded
runs is not a significant sample of anything. It is the point at which the
question becomes worth a human minute. **The registry flags; a person decides.**
That division is what lets the numbers be small enough to act on, and it is only
affordable because every move is reversible — a wrong promotion costs one flip
backwards, and a wrong retirement costs one `git mv`.

### 3. The registry is generated, never written

There is one index of every capability: what exists, what state it is in, what
its record says, grouped by domain. It is a Markdown file, it is committed, and
**it is never edited by hand.** It is regenerated from the frontmatter at the
start of a session, and again at the end, after the close-out review has written
the new scores.

Drift between the index and the tree is impossible, because the index is not the
source. The frontmatter is the source. The document is a projection of it, and a
projection cannot disagree with what it projects.

The failure mode this removes is specific and it is worse than having no index:
a hand-maintained one still lists a capability that was archived three weeks
ago, and unlike a stale comment, an index is *consulted* — it is the thing an
orchestrator reads to decide what to reach for.

Two properties make regeneration survivable in a real repository:

- **Exactly one line changes on every run** — the generation date — and every
  staleness comparison excludes it. Without that exclusion the file is rewritten
  every day, each rewrite lands in somebody's commit as a one-line diff that
  means nothing, and people stop reading diffs that always contain noise. That
  is how a real change slips through in the same hunk as the noise.
- **Paths are recorded relative to the scanned root**, so the document is
  byte-identical on every machine that generates it. A document that never
  compares equal cannot be checked for staleness at all.

Those two together make the index enforceable rather than aspirational:
`--check` exits `1` when what is on disk no longer matches the tree.

Generating from the real frontmatter has one more consequence, which was not
planned. Because `tier` and `state` are separate fields, a file can contradict
itself — a capability claiming a graduated tier while still marked on trial,
usually a promotion where somebody flipped one field and not the other. A
hand-written index inherits the contradiction silently. A generated one reports
it.

---

## On a real tree

The system described here runs over a private repository of 169 capabilities.
Its generated registry reports:

| | |
|---|---|
| capabilities | 169 |
| graduated | 112 |
| on trial | 56 |
| carrying any recorded use | 63 |
| flagged for promotion | 2 |
| flagged for retirement | 0 |
| with contradictory frontmatter | 1 |

Three of those numbers are worth reading together.

**63 of 169 have ever been graded.** Two thirds of the roster has no record at
all. That is not a flattering number and it is not meant to be — it says the
roster is larger than the work that has come through it. The lifecycle's job is
to keep that visible rather than to resolve it by deleting things nobody asked
for yet.

**0 flagged for retirement**, after months of operation. A staleness rule over
the same tree would have pointed at the 106 capabilities with no recorded use,
every one of them for the offence of not having come up. The retirement count is
zero because nothing has failed *measurably*. Those two rules answer different
questions, and only one of them is about quality.

**2 flagged for promotion.** Small, because the bar is real: five graded runs at
a 0.70 average is a thing that has to actually happen.

---

## How the rule hardened

The audit pass that proposes these moves is a second script — not included in
this repository, but worth one paragraph, because the file preserves the moment
the rule changed.

Its module docstring, fourteen lines from the top, still lists the retirement
criteria as (translated):

```
1. RETIRE
   - last_used more than 60 days without being invoked, OR
   - orphaned capability (no inbound references in the repository)
```

The function that actually decides retirement sits 155 lines further down. It
does not implement the first clause. It opens with a comment naming the rule
that replaced it and the date it was replaced, and then checks two things:
orphaned, or sustained measured failure.

Nobody updated the docstring. That part is ordinary. What is worth keeping is
what the pair records between them: a rule that was written down as obvious,
implemented, and then deliberately removed from the implementation — while the
sentence proposing it stayed on the page above. The staleness heuristic did not
survive contact with the practice. Both halves of that argument are still in the
file, fourteen lines and one hundred and fifty-five lines from the top, and the
distance between them is the history.

---

## What is in this repository

| Path | Lines | What it does |
|------|-------|--------------|
| `tools/skill_registry.py` | 553 | The generator. Walks the capability root, parses every `SKILL.md` frontmatter, applies the lifecycle rules, and writes the registry. Standard library only. |
| `tools/tests/` | 978 | 114 tests across 4 files. |
| `tools/tests/fixtures/policy.example.json` | 13 | A policy file to copy from. |

The domain taxonomy and the thresholds are **not** in the script. They live in a
JSON policy file, because the domains one team needs are noise to another:

```json
{
  "promote": { "uses": 5, "success_rate": 0.7 },
  "retire":  { "uses": 3, "success_rate": 0.3 },
  "fallback_domain": "General",
  "domains": [
    { "label": "Data & migrations", "match": ["migration", "schema"] },
    { "label": "Delivery",          "match": ["release", "rollback"] },
    { "label": "Testing",           "match": ["test", "flaky"] }
  ]
}
```

Matching is by substring against the directory name, first match wins, so the
order in the file is meaningful. With no policy file at all, every capability
lands in one section — which is honest about knowing nothing, rather than
imposing somebody else's categories.

Every failure to read that file is fatal. A malformed policy is never recovered
from by falling back to the defaults, because the result would be a registry
that is well-formed, plausible, and grouped by rules nobody wrote.

**Not included here:** the close-out review that produces the verdicts, and the
audit pass that detects orphans and semantic overlap. Both need the whole
repository rather than the frontmatter, and both are described above rather than
shipped.

---

## Running it

```bash
mkdir -p capabilities/schema-drift-check capabilities/flaky-test-triage capabilities/rollback-drill

cat > capabilities/schema-drift-check/SKILL.md <<'EOF'
---
name: schema-drift-check
description: Compares the shipped schema against the declared contract.
tier: trial
state: trial
uses: 6
success_rate: 0.83
---
EOF

cat > capabilities/flaky-test-triage/SKILL.md <<'EOF'
---
name: flaky-test-triage
description: Reruns a failing test under fixed seeds and reports the cause.
tier: trial
state: trial
uses: 4
success_rate: 0.12
---
EOF

cat > capabilities/rollback-drill/SKILL.md <<'EOF'
---
name: rollback-drill
description: Walks a service back to the previous known-good revision.
tier: core
state: graduated
uses: 0
---
EOF

python3 tools/skill_registry.py --dry-run
```

The three entries, and the three different things the tool has to say about
them:

```
## Summary

- **3 capabilities** · 1 graduated · 2 on trial
- **Recorded use:** 2 with `uses > 0` · 2 carrying a success rate · 1 never graded
- **Candidates:** 1 to promote (`uses >= 5` and `success_rate >= 0.70`) · 1 to retire (`uses >= 3` and `success_rate < 0.30`)

## Uncategorised (3)
- **flaky-test-triage** `[trial]` `4·0.12` → Reruns a failing test under fixed seeds and reports the cause. · RETIRE · `flaky-test-triage/SKILL.md`
- **rollback-drill** `[graduated]` `0·n/a` → Walks a service back to the previous known-good revision. · `rollback-drill/SKILL.md`
- **schema-drift-check** `[trial]` `6·0.83` → Compares the shipped schema against the declared contract. · PROMOTE · `schema-drift-check/SKILL.md`
```

`rollback-drill` is the one to look at. Zero uses, no score, and the tool has
nothing to say about it — no flag, no warning, no suggestion. That silence is
the design.

### Make it enforceable

```bash
python3 tools/skill_registry.py                      # wrote capability-registry.md (3 capabilities)
python3 tools/skill_registry.py --check              # registry is up to date · exit 0
```

Now change one score in a `SKILL.md` and ask again:

```bash
python3 tools/skill_registry.py --check
# error: registry is stale: capability-registry.md — regenerate it with skill_registry.py
# exit 1
```

The four modes:

| Mode | Does | Exit |
|---|---|---|
| *(default)* | writes the registry | `0` |
| `--dry-run` | prints it, writes nothing | `0` |
| `--check` | compares disk against the tree | `0` current · `1` stale |
| `--refresh-quiet` | rewrites only if stale | always `0` |

`--check` is what a pre-commit hook needs. `--refresh-quiet` is for a
session-start hook, where failing loudly would block work that has nothing to do
with the registry — it repairs, notes the repair on stderr, and never fails.
Neither one ever rewrites the file for a date change alone.

Roots resolve `--root` → `$CAPABILITY_ROOT` → `./capabilities`; the registry
itself `--out` → `$CAPABILITY_REGISTRY` → a sibling of the root.

Clean up when you are done: `rm -rf capabilities capability-registry.md`

---

## Tests

```bash
python3 -m pytest tools/tests -q
# 114 passed
```

| File | Tests | Covers |
|------|-------|--------|
| `test_skill_registry_modes.py` | 31 | The four modes, and the staleness comparison that ignores the date line |
| `test_skill_registry_taxonomy.py` | 29 | Domain resolution, and the policy validation that refuses to guess |
| `test_skill_registry_frontmatter.py` | 28 | Field parsing, and the tier/state contradictions |
| `test_skill_registry_lifecycle_rules.py` | 26 | Promotion, retirement, and the rules that must never fire |

The class named `TestTheRuleWeRefusedToWrite` is the point of the suite. Its
tests assert an absence: that a capability with an ancient `last_used` and no
recorded use is flagged for nothing, that it still appears in the registry, and
that it stays untouched even when sitting next to a capability that *is* being
retired for measured failure.

Absences do not defend themselves. Without those tests, a later reader looking
at a lifecycle system with no staleness rule would reasonably conclude it was an
oversight and add one — and the code would still pass everything else.

---

## What this does not tell you

- **Five runs is not a sample.** The thresholds are triggers for human
  attention, not statistical claims, and the registry proposes rather than acts.
- **The metric is exactly as good as the review behind it.** A close-out review
  that grades everything clean produces a registry that promotes everything, and
  nothing in the tool can detect a lenient reviewer. This is the real limit of
  the approach: it moves the trust problem from "is this capability good" to "is
  this review honest", which is a better place for it to sit, but it does not
  remove it.
- **Substring matching on a directory name is a crude classifier.** It is chosen
  because a misfiled capability is visible on the page, where somebody will fix
  the policy file. A cleverer classifier would be wrong less often and harder to
  correct when it was.
- **Nothing here detects a capability that is quietly redundant** with another
  one. Overlap and orphan detection need the whole repository; they are a
  separate pass, and not in this repository.
