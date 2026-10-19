# How accurate is Faultline?

Static call resolution in Python is unsolved in general, so any tool that claims
a resolution rate without saying how it was measured is guessing. This page says
exactly how Faultline was measured, what it gets wrong, and where the numbers
should not be trusted.

**Headline** (142 hand-labelled call sites, 66 of which have a real in-repo target):

| View | Precision | Recall | What it means |
|---|---:|---:|---|
| **Default view** (confidence ≥ 0.5) | **100%** (66 / 66) | **100%** (66 / 66) | What you see without touching the confidence slider |
| Every emitted edge, any confidence | 89.2% (66 / 74) | 100% | Includes the low-confidence guesses hidden by default |
| Exact + inferred edges only | 100% | – | The edges Faultline is actually sure about |
| Wrong-target edges | **0** | | Resolving to the *wrong* symbol never happened |

Read those with the caveats in [What these numbers do not prove](#what-these-numbers-do-not-prove)
before quoting them. The short version: the sample is small, and one of the three
repositories was used to tune the resolver.

---

## Method

The harness is `backend/tests/accuracy/`. It has three moving parts.

**1. A seeded uniform sample, drawn blind.** `benchmarks/sample_call_sites.py`
draws call sites at random (fixed seed) from a repository's source, excluding only
builtins, and prints **source context and nothing else**. It never shows what
Faultline resolved a site to. The sample therefore includes the hard cases and the
easy ones in their natural proportions.

| Repository | Sample | Notes |
|---|---|---|
| `flask` @ `d73fa1c` | 70 of 884 call sites in `src/flask` | Tuned against (see below) |
| `requests` @ `dae7ef6` | 40 of 691 call sites in `src/requests` | **Held out**: labelled after tuning, never used to tune |
| `fixture` | every call site in the sample project | 34 sites; built to be resolvable, so it inflates the totals |

**2. Labels written by reading the source.** Each label says: *at this file, line and
callee expression, the call really targets that symbol* — or `null` when the callee
is outside the repository (`os.path.join`, `click.option`). Labels are never
copied from Faultline's output; that would make precision circular. Where a
human cannot name a single target, the site is **excluded and the exclusion is
recorded with a reason** in `labelled_calls.json` rather than being given an
invented answer.

**3. A scorer that is hard to flatter.**

- A site is *resolved* when Faultline produces at least one target.
- A resolved site is *correct* when the labelled target is among them.
- A site labelled `null` that Faultline resolves anyway is a **false positive** and
  counts against precision. Inventing an edge to code that is not there is the
  worst failure a call graph can have.
- Precision = correct ÷ resolved. Recall = correct ÷ labelled-with-a-target.
- The scorer judges what the **graph will actually contain** (no self-edges), not
  the resolver's raw output. It originally did not; see below.

Pinned commits matter: labels are line numbers, so "the latest Flask" would
silently invalidate them. CI fetches each repository by SHA, and
`FAULTLINE_REQUIRE_REPOS=1` turns "could not fetch" into a failure instead of a
skip, so the gate can never quietly stop gating.

## The gate

CI fails if any of these regress (`tests/accuracy/test_accuracy.py`):

| Check | Threshold |
|---|---|
| Precision, every emitted edge | ≥ 0.85 |
| Precision, default view | ≥ 0.95 |
| Recall, default view | ≥ 0.90 |
| Wrong-target edges | 0 |
| False-positive edges visible by default | 0 |
| Held-out repository, default precision and recall | ≥ 0.95 / ≥ 0.90 |

The original floor was 0.85. The default-view threshold is tighter on purpose: a
mutation test that made every ambiguous guess fully confident still scored
**0.94** default-view precision, so a flat 0.85 would have waved a real
regression through. With the tighter gate that mutation fails three tests.
(Try it: it is the first thing worth doing to any accuracy gate.)

## What the harness found

Running the resolver against real code, then labelling, surfaced bugs that a
hand-built fixture could not. Each is fixed and has a regression test.

| Found on | Problem | Fix |
|---|---|---|
| Flask | `flask/sansio/` has no `__init__.py` (a PEP 420 namespace subpackage). The original rule — stop at the first directory without one — named `sansio/app.py` plain `app`, breaking every import of it | Top package = *highest* ancestor with an `__init__.py`; gaps below it are namespace subpackages |
| httpx | `httpx/__init__.py` publishes its API through `from ._x import *`. **885 calls** to `httpx.Response(…)` etc. were unresolved (58.5% → 97.4% in-repo resolution once fixed) | Follow star-import re-exports |
| Flask | `app = Flask(); @app.route(...)` inside an app-factory: `app` is a local of the *enclosing* function | Resolve variable types through enclosing scopes, as Python does |
| Flask | `self.session_class(data)` where `session_class = SecureCookieSession` is a class-body alias (Flask's pluggable-class idiom); also `self.__class__(...)` | Class-body aliases, scored 0.9 because a subclass may override |
| Flask | A test's local `Wrapper.close()` matched `gen.close()` in **library** code | A function-local class is only visible inside its function; production code never resolves into test modules |
| Flask | `object.__repr__(self)` resolved into the repo | Attribute calls on bare builtin types never target repo code |
| requests | `try: from urllib3… import SOCKSProxyManager / except ImportError: def SOCKSProxyManager` was reported **exact 1.0** to the fallback stub | A name bound by both an import and a def is ambiguous, not exact |
| requests | A delegating wrapper (`def getheaders(self): self._headers.getheaders()`) matched itself. The *graph* never emits self-edges but the *scorer* did | Scorer now judges the emitted graph |
| Original plan | A bare `helper()` inside a method fell back to *methods* named `helper` — a `NameError` in Python | The fallback considers module-level definitions only |

## The held-out result, in full

`requests` was labelled **after** the resolver had been tuned on Flask, and was not
used to tune it. Its first score was worse than Flask's, and that is the honest
generalisation estimate:

| | Default-view precision | All-edges precision | Recall |
|---|---:|---:|---:|
| **First look** | 81.8% (9 / 11) | 69.2% (9 / 13) | 100% |
| After corrections | 100% (9 / 9) | 81.8% (9 / 11) | 100% |

The four first-look false positives were, on inspection: a **labelling error of
mine** (I called `SOCKSProxyManager` external without reading the module's
fallback definition; the label was withdrawn, not "corrected" to match the tool);
a **scorer artifact** (the self-edge above); and two genuine low-confidence
guesses (`kwargs.get`, `d.items`), which are below the 0.5 default threshold.
Both real code fixes are in the table above.

Note the sample size: `requests` has only **9** call sites with an in-repo target
among its 38 labelled sites — most of its calls go to `urllib3` and the
standard library. Nine correct out of nine is consistent with a true rate anywhere above
about 70%.

## What it still gets wrong

All eight remaining false positives are low-confidence (< 0.5) fan-out for a
receiver Faultline cannot type, on a method name that overwhelmingly belongs to a
builtin: `handler_map.get`, `options.pop`, `d.items`, `s.loads`. They are kept in
the graph — lower the confidence slider and they appear — but they are discounted
because `dict.get` is a far likelier target than an unrelated `_AppCtxGlobals.get`.

The resolver also cannot see, and will report as unresolved or ambiguous:

- **pytest fixtures.** `client` and `app` arrive by parameter *name*. This is the
  dominant source of ambiguity in Flask's own test suite: 257 `client.get(...)`
  calls are kept only as low-confidence guesses, hidden at the default threshold.
- `getattr`, monkey-patching, metaclasses, plugin registries and any other dispatch
  decided at runtime.
- A call on a call result: `factory().method()`.
- A name bound in two branches (`if x: h = A() else: h = B()`) — dropped rather
  than guessed.

## What these numbers do not prove

- **The sample is small.** 66 correct-of-66 in the default view gives a 95% Wilson
  lower bound of about **94.5%**, not 100%. The held-out repository alone
  (n = 9) supports only "above roughly 70%".
- **Flask was tuned against.** Its numbers are optimistic by construction. Trust
  `requests`, which is the held-out repository, and treat the combined figure as an
  upper estimate.
- **The fixture inflates the totals.** It is 34 of 142 labels and is perfect by
  design. Excluding it: 108 labels, 38 with an in-repo target.
- **One labeller.** Labels were written by a single author and were not
  independently double-checked. One label error was found *by the tool* and is
  documented above; there may be others the tool happened to agree with.
- **Two libraries are not the world.** Flask and requests are unusually clean,
  well-typed code. Faultline resolves 79% of in-repo calls on Django, which is
  dynamic and metaclass-heavy, and that is a fairer picture of the hard case
  (see the [benchmark table](../README.md#benchmarks)).

## Reproduce it

```bash
cd backend
pip install -e ".[dev]"
pytest tests/accuracy -v            # fetches flask and requests at their pinned commits

# or, to see the raw per-repo report:
python benchmarks/sample_call_sites.py <repo> --under src/<pkg> --n 70   # the blind sample
```

To add labels: draw a fresh sample, label it *before* looking at Faultline's
answer, and add held-out repositories rather than growing the tuned ones.
