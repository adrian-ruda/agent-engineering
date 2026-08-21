# Third-party notices

## gentle-ai — MIT License

This project is built on top of
[`Gentleman-Programming/gentle-ai`](https://github.com/Gentleman-Programming/gentle-ai)
by Alan Buscaglia, distributed under the MIT License.

We did not invent a methodology. We adopted a serious one and built the
instrumentation it was missing for the way we operate it.

### What comes from `gentle-ai`

| Element | Where it comes from |
|---|---|
| The spec-driven cycle — `explore → propose → spec → design → tasks → apply → verify → archive` | The `gentle-ai` SDD workflow |
| The status contract: a phase's state is a **machine token**, never inferred from prose | The `gentle-ai` status model |
| Receipt-driven delivery — every handoff carries an auditable receipt rather than an assurance | The `gentle-ai` review and delivery model |
| The delta-spec layout — recursive walk of `changes/{change}/specs/`, accepting files named exactly `spec.md` | Adapted from `internal/sddstatus/status.go` (`findSpecFiles`) |
| The change-directory shape that `engine/sdd_status.py` reads | The `gentle-ai` change layout |

The recursive spec resolution in `engine/sdd_status.py` is a direct adaptation.
We aligned with the upstream engine rather than the reverse: a flat `spec.md` at
a change root is invisible to it, so it is invisible to ours too. One layout,
one behaviour, both engines in agreement.

### What was written here

| Element | What it adds |
|---|---|
| `engine/sdd_status.py` | The Python status engine itself — 1,011 lines, standard library only |
| The `in_progress` phase state | Upstream models a phase as `blocked`, `ready` or `all_done`. A phase whose artifact exists over unfinished work fits none of the three, and every rounding is a false report. `in_progress` is the fourth state |
| `taskProgress.notApplicable`, `.notApplicableUnjustified`, `.allComplete` | Three fields that make discarded work visible instead of free |
| The anti-shortcut rule for `[-]` tasks | Not-applicable stays in the denominator, must carry its reason inline, and blocks `archive` rather than `apply` |
| Fail-closed classification of task markers | An unreadable or unrecognised marker blocks; it never rounds to done |
| Deadlock-safe routing | Routing may never skip a phase the dependency map reports as `in_progress`, and no gate may block the work that repairs it |
| `engine/sdd_apply_gate.sh` | The blocking pre-apply hook, including the single-invocation fail-closed engine call |
| `engine/tests/` | 152 tests, most of them written from adversarial review findings rather than from a specification |

### License

```
MIT License

Copyright (c) Alan Buscaglia / Gentleman-Programming

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

The `gentle-ai` binary is not vendored here and is not required to run this
engine. The reference to `internal/sddstatus/status.go` is kept in the source
because it is publicly verifiable — removing it would delete the evidence of
where the layout decision came from.
