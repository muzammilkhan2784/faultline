# Design decisions and changes from the original plan

Faultline started from a written plan. Following it was the default; every change
below was made because evidence, usually from running the tool on real code,
showed the plan's approach was wrong or measurably worse. Each is covered by a test.

## Where the build differs from the plan

| # | Plan said | Faultline does | Why |
|---|---|---|---|
| 1 | Module FQN: stop walking up at the first directory without `__init__.py` | The top package is the **highest** ancestor with an `__init__.py`; gaps below it are PEP 420 namespace subpackages | Flask's `sansio/` has no `__init__.py`. The spec's rule named `sansio/app.py` plain `app` and broke every import of it |
| 2 | A bare `foo()` may resolve to a method of the caller's own class | It never does. The last-resort fallback considers **module-level** definitions only | Class scope is not enclosing for method bodies: a bare `foo()` there is a `NameError`. The rule invented edges that cannot exist |
| 3 | A name found nowhere is counted as *unresolved* | Counted as **external** when no reachable repo definition has that name; *unresolved* is reserved for "our module, but we can't tell what it is" | Otherwise every call into the standard library inflates the failure count and hides the real failures |
| 4 | Batch size 1,000 | **5,000** | Controlled comparison on Django (alternating order, warm-up discarded): ~9.2 s vs ~13.6 s |
| 5 | Neo4j `APOC` plugin in `docker-compose.yml` | Not installed | Nothing calls APOC, and it forces a runtime plugin download on first start |
| 6 | Cypher `CALL { WITH n … } IN TRANSACTIONS` for batched delete | `CALL (n) { … }` | The old form is deprecated in Neo4j 5.23+ and removed later. **Requires Neo4j ≥ 5.23** |
| 7 | Python 3.12 | Supports **3.10+**; CI runs 3.10 and 3.12 | Widens where it runs at no cost; nothing 3.12-specific is used |
| 8 | Git clone failure → `400` | Synchronous validation (bad scheme, missing path, path outside `ALLOWED_ROOT`) is a `400`; a *clone that fails* surfaces as `status: "failed"` with the reason on the repo | The clone runs in a background task, so there is no request left to fail. The behaviour is honest about the tradeoff the spec already names |
| 9 | Accuracy gate: precision ≥ 0.85 | Gates precision at **both** the default view (≥ 0.95) and across every emitted edge (≥ 0.85), plus recall, zero wrong-target edges and zero visible false positives | A mutation test that made every ambiguous guess fully confident scored 0.94, so a flat 0.85 would pass a real regression |
| 10 | Import cycles ignore `TYPE_CHECKING` edges | Also ignore **function-local** imports (`is_deferred`) | Both are the standard *fix* for a cycle, not a cause |
| 11 | `is_entrypoint` and `test` rules | As specified, plus `@errorhandler`, `@before_request`, `@field_validator` and friends | Framework hooks are called by the framework, so they are not dead code |

## Additions the plan did not include

These came from measuring on real repositories; each has a regression test and
an entry in [ACCURACY.md](ACCURACY.md).

- **Star-import re-exports** (`from ._x import *`): 885 unresolved httpx calls
- **Closure scoping** for variable types: Flask's app-factory pattern
- **Class-body aliases** (`session_class = Foo`) and `self.__class__(...)`
- **Annotation- and return-type-based typing**, with compounded confidence
- **`try: import / except ImportError: def …` fallbacks** reported as ambiguous, not exact
- **Structural visibility rules**: a function-local class is invisible outside its function, and production
  code never resolves into test modules
- **Discounted confidence for builtin-like method names** (`get`, `pop`, `loads`) on unknown receivers
- **A CLI** (`faultline analyze`) that needs no database
- **A plain-English briefing** (`GET /repos/{id}/briefing`): four short paragraphs for someone new to
  the code (what the function is, what a change reaches, which tests cover it, where the risk sits),
  shown beside the graph. Added after a first-time user could read the numbers but not what they meant
- **Docstring capture** (first sentence, stored on the function) so "what does this do" can quote the
  author instead of guessing from a name
- **Empty analyses fail with a reason, and odd ones carry a notice.** A repository with no Python files is
  reported as `failed` and names what it found instead (JavaScript, notebooks, an empty folder); Python
  that calls nothing is `ready` with a warning on its card and on the start screen
- **Path errors that say what the server can see.** In Docker a Windows path such as `C:\Users\me\code`
  is unreachable, and the old `path not found` gave no hint why
- **Multi-run medians** in the benchmark, with the cold first run kept in the JSON

## Decisions made along the way

- **`confidence` compounds; it is not a `min`.** A return-annotation type
  (`u = get_user()` with `-> User`) is weaker evidence than `u = User()`, so its
  confidence is the *product* (0.7 × 0.85 ≈ 0.6). Using `min` made the 0.85 constant
  dead code, caught by a test.
- **The graph never contains a self-edge**, and the accuracy scorer judges the
  emitted graph rather than the resolver's raw output.
- **All Cypher lives in one file**, and a test fails if a query string appears anywhere else.
- **`depth` is the only interpolated value.** Neo4j cannot parameterise a
  variable-length bound, so the number is validated as an `int` in 1..10 in the
  Pydantic schema and again immediately before formatting.
- **`GraphClient.read/write` take the query as a positional-only argument.** A real bug: a Cypher
  parameter named `$query` collided with the method's own argument name and would have
  made every symbol search raise `TypeError`. It was caught by the API tests, whose fake graph client mirrors the real
  client's signature; the real one would have failed identically.
- **Dead code says "candidates".** The UI carries the caveat next to the table.
- **The briefing is templated, not written by a language model.** Every sentence is computed from a
  number or flag in the graph, so it cannot claim something the analysis did not find, the same graph
  always yields the same words, and it is unit-tested to 100% (including a check that no paragraph has
  unbalanced markup). The cost is prose plainer than a model would write; for a tool whose whole pitch
  is "trust the numbers", that is the right trade.
- **The briefing counts what the Impact tab counts.** Same depth (3) and same confidence filter, and an
  integration test asserts the two agree. Two different figures for one question reads as a bug.
- **Risk level is a blast-radius rating, not a code-quality score.** Points for how many functions
  depend on it (0 / 1-4 / 5-19 / 20+), for reaching an entry point, for complexity above 10 and above 20,
  and for having no covering test *in a repository that has tests*. Under 2 points is low, under 4 is
  moderate. A function nothing depends on is always low, however tangled: a change cannot spread.
  Every threshold is a named constant and the boundaries are tested. The headline states the reason (for
  example "and no test covers it"), so the rating never looks arbitrary.
- **Zero results are diagnosed, not shown as zero.** "Nothing calls this" has five different meanings
  (entry point, test, private helper, possible public API, or callers hidden by the confidence filter),
  and each gets its own explanation, because the honest answer to "what breaks?" differs.
- **No Python files is `failed`; scripts with no calls are `ready`.** The first is the wrong kind of
  project, the second is a real if small result.

## Bugs found by tests that could not have been found otherwise

Recorded because they justify the test strategy: a fake database proves routing and
call signatures, only a real one proves queries, and only a real repository proves resolution.

| Found by | Bug |
|---|---|
| API tests (strict fake client) | Symbol search raised `TypeError` (parameter name collision above) |
| API smoke test, DB down | `ServiceUnavailable` is a `DriverError`, not a `Neo4jError`: the API crashed at startup whenever Neo4j was down |
| Fixture project | An inherited constructor scored 1.0 instead of 0.95 |
| Parser smoke test | Nested methods reported `is_method=False`; bare `super()` counted as a builtin call |
| Running on Flask | Namespace packages, closures, class aliases (see ACCURACY.md) |
| Running on httpx | Star-import re-exports |
| Benchmark harness test | Nearest-rank percentile was off by one (p95 of 1..100 returned 96) |
| Building the Docker image | `readme = "../README.md"` is rejected by setuptools, so **`pip install -e ".[dev]"` (the first step of every CI job) would have failed**. The package had only ever been run via `sys.path` |
| `docker compose up` | `CORS_ORIGINS=http://localhost:5173` crashed settings loading: pydantic-settings JSON-decodes tuple fields from the environment *before* a CSV validator runs. Fixed with `NoDecode` and tests that read real environment variables |
| End-to-end run in containers | "Did you mean" was substring-only, so a real misspelling (`super_ln`) got no suggestion. The earlier tests used typos that happened to be substrings. Now a fuzzy fulltext query, verified on the real index |
| A real benchmark run (now pinned by a test) | The multi-run wrapper was defined *after* the `__main__` guard and crashed the run |
| A first-time user | `hideEdgesOnViewport` made every edge vanish while dragging a node, which reads as broken. A JavaScript-only repository finished as "ready" with 0 files, 0 nodes and 0 edges and offered to "Explore" it. A Windows path returned `path not found` with no hint that the API runs in a container |
| Reading the briefing's own output | A function with one dependent and no test was rated "moderate" under a headline that did not say why. The headline now names the reason |
