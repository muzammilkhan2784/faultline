"""Every Cypher statement Faultline runs, in one place.

Route handlers never build query strings. Keeping them here means the queries
can be read as a set, diffed as a set, and — for the handful that need a
literal depth — validated in exactly one place.

**On the depth interpolation.** Neo4j does not allow a parameter in a
variable-length pattern: ``[:CALLS*1..$depth]`` is a syntax error, full stop.
The bound therefore has to be baked into the string. Every function that does so
asserts the bound is an ``int`` in a small range *before* formatting, and the
API layer independently constrains it with Pydantic, so no caller-controlled
text ever reaches the query. Everything else is a real parameter.
"""

from __future__ import annotations

MIN_DEPTH = 1
MAX_DEPTH = 10


def _checked_depth(depth: int) -> int:
    """Guarantee a depth bound is safe to interpolate into a query string."""
    if not isinstance(depth, int) or isinstance(depth, bool):
        raise TypeError(f"depth must be an int, got {type(depth).__name__}")
    if not MIN_DEPTH <= depth <= MAX_DEPTH:
        raise ValueError(f"depth must be between {MIN_DEPTH} and {MAX_DEPTH}, got {depth}")
    return depth


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------

#: Run before the first write. The composite uniqueness constraints are what
#: make the writer's MERGEs index lookups instead of full label scans — without
#: them, ingesting a large repository takes minutes rather than seconds.
SCHEMA_STATEMENTS: tuple[str, ...] = (
    "CREATE CONSTRAINT repo_id IF NOT EXISTS FOR (r:Repo) REQUIRE r.id IS UNIQUE",
    "CREATE CONSTRAINT module_key IF NOT EXISTS "
    "FOR (m:Module) REQUIRE (m.repo_id, m.fqn) IS UNIQUE",
    "CREATE CONSTRAINT class_key IF NOT EXISTS FOR (c:Class) REQUIRE (c.repo_id, c.fqn) IS UNIQUE",
    "CREATE CONSTRAINT function_key IF NOT EXISTS "
    "FOR (f:Function) REQUIRE (f.repo_id, f.fqn) IS UNIQUE",
    "CREATE INDEX function_name IF NOT EXISTS FOR (f:Function) ON (f.repo_id, f.name)",
    "CREATE INDEX class_name IF NOT EXISTS FOR (c:Class) ON (c.repo_id, c.name)",
    "CREATE INDEX function_test IF NOT EXISTS FOR (f:Function) ON (f.repo_id, f.is_test)",
    "CREATE INDEX function_entrypoint IF NOT EXISTS "
    "FOR (f:Function) ON (f.repo_id, f.is_entrypoint)",
    "CREATE INDEX module_repo IF NOT EXISTS FOR (m:Module) ON (m.repo_id)",
    "CREATE FULLTEXT INDEX symbol_search IF NOT EXISTS "
    "FOR (n:Function|Class|Module) ON EACH [n.fqn, n.name]",
)

# ---------------------------------------------------------------------------
# writes
# ---------------------------------------------------------------------------

WRITE_REPO = """
MERGE (r:Repo {id: $id})
SET r += $props
"""

UPDATE_REPO_STATUS = """
MATCH (r:Repo {id: $id})
SET r.status = $status, r.updated_at = datetime()
SET r += $props
RETURN r.id AS id
"""

WRITE_MODULES = """
UNWIND $rows AS row
MERGE (m:Module {repo_id: row.repo_id, fqn: row.fqn})
SET m += row.props
WITH m, row
MATCH (r:Repo {id: row.repo_id})
MERGE (r)-[:HAS_MODULE]->(m)
"""

WRITE_CLASSES = """
UNWIND $rows AS row
MERGE (c:Class {repo_id: row.repo_id, fqn: row.fqn})
SET c += row.props
WITH c, row
MATCH (m:Module {repo_id: row.repo_id, fqn: row.module_fqn})
MERGE (m)-[:CONTAINS]->(c)
"""

WRITE_FUNCTIONS = """
UNWIND $rows AS row
MERGE (f:Function {repo_id: row.repo_id, fqn: row.fqn})
SET f += row.props
WITH f, row
MATCH (m:Module {repo_id: row.repo_id, fqn: row.module_fqn})
MERGE (m)-[:CONTAINS]->(f)
"""

#: A method belongs to its class as well as its module.
WRITE_METHOD_LINKS = """
UNWIND $rows AS row
MATCH (c:Class {repo_id: row.repo_id, fqn: row.class_fqn})
MATCH (f:Function {repo_id: row.repo_id, fqn: row.fqn})
MERGE (c)-[:CONTAINS]->(f)
"""

WRITE_IMPORTS = """
UNWIND $rows AS row
MATCH (a:Module {repo_id: row.repo_id, fqn: row.source})
MATCH (b:Module {repo_id: row.repo_id, fqn: row.target})
MERGE (a)-[i:IMPORTS {line: row.line}]->(b)
SET i.kind = row.kind,
    i.alias = row.alias,
    i.is_type_only = row.is_type_only,
    i.is_deferred = row.is_deferred
"""

WRITE_INHERITS = """
UNWIND $rows AS row
MATCH (a:Class {repo_id: row.repo_id, fqn: row.child})
MATCH (b:Class {repo_id: row.repo_id, fqn: row.parent})
MERGE (a)-[e:INHERITS {position: row.position}]->(b)
"""

WRITE_INSTANTIATES = """
UNWIND $rows AS row
MATCH (f:Function {repo_id: row.repo_id, fqn: row.caller})
MATCH (c:Class {repo_id: row.repo_id, fqn: row.class_fqn})
MERGE (f)-[e:INSTANTIATES {line: row.line}]->(c)
SET e.confidence = row.confidence
"""

#: MATCH, not MERGE, for the endpoints: a row whose callee is not in the repo is
#: silently skipped, which is exactly right — it means the call left the repo.
WRITE_CALLS = """
UNWIND $rows AS row
MATCH (a:Function {repo_id: row.repo_id, fqn: row.caller})
MATCH (b:Function {repo_id: row.repo_id, fqn: row.callee})
MERGE (a)-[c:CALLS {line: row.line}]->(b)
SET c.confidence = row.confidence,
    c.resolution = row.resolution,
    c.via = row.via
"""


def delete_repo_nodes_query(batch_size: int = 10_000) -> str:
    """Batched delete, so removing a 40k-node repository does not build one
    enormous transaction. Must be run outside an explicit transaction.

    Like the depth bound, the batch size cannot be a parameter, so it is
    validated as an int before being formatted in.
    """
    if not isinstance(batch_size, int) or isinstance(batch_size, bool):
        raise TypeError("batch_size must be an int")
    if not 100 <= batch_size <= 100_000:
        raise ValueError("batch_size must be between 100 and 100000")
    return f"""
    MATCH (n) WHERE n.repo_id = $repo_id
    CALL (n) {{ DETACH DELETE n }} IN TRANSACTIONS OF {batch_size} ROWS
    """


DELETE_REPO_ROOT = """
MATCH (r:Repo {id: $repo_id})
DETACH DELETE r
"""

# ---------------------------------------------------------------------------
# repo reads
# ---------------------------------------------------------------------------

LIST_REPOS = """
MATCH (r:Repo)
RETURN r AS repo
ORDER BY r.analyzed_at DESC
LIMIT $limit
"""

GET_REPO = """
MATCH (r:Repo {id: $repo_id})
RETURN r AS repo
"""

REPO_COUNTS = """
MATCH (r:Repo {id: $repo_id})
OPTIONAL MATCH (m:Module {repo_id: $repo_id})
WITH r, count(DISTINCT m) AS modules
OPTIONAL MATCH (c:Class {repo_id: $repo_id})
WITH r, modules, count(DISTINCT c) AS classes
OPTIONAL MATCH (f:Function {repo_id: $repo_id})
WITH r, modules, classes, count(DISTINCT f) AS functions,
     count(DISTINCT CASE WHEN f.is_test THEN f END) AS test_functions
OPTIONAL MATCH (:Function {repo_id: $repo_id})-[call:CALLS]->(:Function {repo_id: $repo_id})
WITH r, modules, classes, functions, test_functions, count(call) AS call_edges
OPTIONAL MATCH (:Module {repo_id: $repo_id})-[imp:IMPORTS]->(:Module {repo_id: $repo_id})
RETURN modules, classes, functions, test_functions, call_edges, count(imp) AS import_edges
"""

PARSE_FAILURES = """
MATCH (m:Module {repo_id: $repo_id})
WHERE m.parse_error IS NOT NULL
RETURN m.fqn AS fqn, m.path AS path, m.parse_error AS error
ORDER BY m.path
LIMIT $limit
"""

# ---------------------------------------------------------------------------
# symbols
# ---------------------------------------------------------------------------

SEARCH_SYMBOLS_FULLTEXT = """
CALL db.index.fulltext.queryNodes('symbol_search', $search) YIELD node, score
WHERE node.repo_id = $repo_id
  AND ($kinds IS NULL OR any(label IN labels(node) WHERE label IN $kinds))
RETURN node.fqn AS fqn,
       coalesce(node.name, node.fqn) AS name,
       labels(node)[0] AS kind,
       coalesce(node.module_fqn, node.fqn) AS module,
       coalesce(node.line_start, 1) AS line,
       coalesce(node.is_test, false) AS is_test,
       score
ORDER BY score DESC, size(node.fqn) ASC
LIMIT $limit
"""

#: Used when the fulltext index is unavailable, and for exact-substring lookups.
SEARCH_SYMBOLS_SCAN = """
MATCH (node:Function|Class|Module)
WHERE node.repo_id = $repo_id
  AND toLower(node.fqn) CONTAINS $needle
  AND ($kinds IS NULL OR any(label IN labels(node) WHERE label IN $kinds))
RETURN node.fqn AS fqn,
       coalesce(node.name, node.fqn) AS name,
       labels(node)[0] AS kind,
       coalesce(node.module_fqn, node.fqn) AS module,
       coalesce(node.line_start, 1) AS line,
       coalesce(node.is_test, false) AS is_test,
       1.0 AS score
ORDER BY size(node.fqn) ASC, node.fqn ASC
LIMIT $limit
"""

GET_SYMBOL = """
MATCH (f:Function {repo_id: $repo_id, fqn: $fqn})
OPTIONAL MATCH (caller:Function)-[in_edge:CALLS]->(f)
WITH f, count(DISTINCT caller) AS fan_in
OPTIONAL MATCH (f)-[:CALLS]->(callee:Function)
RETURN f AS node, fan_in, count(DISTINCT callee) AS fan_out
"""

SYMBOL_EXISTS = """
MATCH (f:Function {repo_id: $repo_id, fqn: $fqn})
RETURN f.fqn AS fqn
LIMIT 1
"""

#: Typo-tolerant suggestions: a fuzzy fulltext match on the symbol *name*.
DID_YOU_MEAN_FUZZY = """
CALL db.index.fulltext.queryNodes('symbol_search', $search) YIELD node, score
WHERE node.repo_id = $repo_id AND (node:Function OR node:Class)
RETURN node.fqn AS fqn
ORDER BY score DESC, size(node.fqn) ASC
LIMIT 5
"""

#: Substring fallback for servers without the fulltext index.
DID_YOU_MEAN = """
MATCH (n:Function|Class {repo_id: $repo_id})
WHERE toLower(n.name) CONTAINS $needle OR toLower(n.fqn) CONTAINS $needle
RETURN n.fqn AS fqn
ORDER BY size(n.fqn) ASC
LIMIT 5
"""

# ---------------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------------


def impact_query(depth: int) -> str:
    """Reverse reachability: every function that can reach ``$fqn``.

    Path confidence is the *product* of the confidences along the path, so a
    three-hop route through two guesses scores far below a direct call. Each
    caller is reported with its best path, because one solid route is enough to
    make it worth looking at.
    """
    depth = _checked_depth(depth)
    return f"""
    MATCH (target:Function {{repo_id: $repo_id, fqn: $fqn}})
    MATCH path = (caller:Function)-[rels:CALLS*1..{depth}]->(target)
    WHERE ALL(r IN rels WHERE r.confidence >= $min_confidence)
      AND caller <> target
    WITH caller,
         min(length(path)) AS distance,
         count(DISTINCT path) AS path_count,
         max(reduce(c = 1.0, r IN rels | c * r.confidence)) AS confidence
    RETURN caller.fqn AS fqn,
           caller.name AS name,
           caller.module_fqn AS module,
           caller.class_fqn AS class_fqn,
           caller.is_test AS is_test,
           caller.is_entrypoint AS is_entrypoint,
           caller.line_start AS line,
           caller.complexity AS complexity,
           distance,
           path_count,
           round(confidence, 4) AS confidence
    ORDER BY distance ASC, confidence DESC, path_count DESC, fqn ASC
    LIMIT $limit
    """


def dependencies_query(depth: int) -> str:
    """Forward reachability: everything ``$fqn`` depends on."""
    depth = _checked_depth(depth)
    return f"""
    MATCH (source:Function {{repo_id: $repo_id, fqn: $fqn}})
    MATCH path = (source)-[rels:CALLS*1..{depth}]->(callee:Function)
    WHERE ALL(r IN rels WHERE r.confidence >= $min_confidence)
      AND callee <> source
    WITH callee,
         min(length(path)) AS distance,
         count(DISTINCT path) AS path_count,
         max(reduce(c = 1.0, r IN rels | c * r.confidence)) AS confidence
    RETURN callee.fqn AS fqn,
           callee.name AS name,
           callee.module_fqn AS module,
           callee.class_fqn AS class_fqn,
           callee.is_test AS is_test,
           callee.is_entrypoint AS is_entrypoint,
           callee.line_start AS line,
           callee.complexity AS complexity,
           distance,
           path_count,
           round(confidence, 4) AS confidence
    ORDER BY distance ASC, confidence DESC, fqn ASC
    LIMIT $limit
    """


def test_selection_query(depth: int) -> str:
    """The tests that transitively reach ``$fqn`` — the shortlist to run."""
    depth = _checked_depth(depth)
    return f"""
    MATCH (target:Function {{repo_id: $repo_id, fqn: $fqn}})
    MATCH path = (t:Function)-[rels:CALLS*1..{depth}]->(target)
    WHERE t.is_test = true
      AND ALL(r IN rels WHERE r.confidence >= $min_confidence)
    WITH t,
         min(length(path)) AS distance,
         max(reduce(c = 1.0, r IN rels | c * r.confidence)) AS confidence
    MATCH (m:Module {{repo_id: $repo_id, fqn: t.module_fqn}})
    RETURN t.fqn AS fqn,
           t.name AS name,
           t.module_fqn AS module,
           m.path AS path,
           t.class_fqn AS class_fqn,
           distance,
           round(confidence, 4) AS confidence
    ORDER BY distance ASC, confidence DESC, fqn ASC
    LIMIT $limit
    """


def neighbourhood_query(depth: int) -> str:
    """The subgraph the UI draws: callers and callees within ``depth`` hops.

    ``*0..n`` includes the centre itself, so a leaf function still renders as a
    single node rather than an empty canvas.
    """
    depth = _checked_depth(depth)
    return f"""
    MATCH (center:Function {{repo_id: $repo_id, fqn: $fqn}})
    CALL {{
      WITH center
      MATCH (center)-[down:CALLS*0..{depth}]->(n:Function)
      WHERE ALL(r IN down WHERE r.confidence >= $min_confidence)
      RETURN n
      UNION
      WITH center
      MATCH (n:Function)-[up:CALLS*0..{depth}]->(center)
      WHERE ALL(r IN up WHERE r.confidence >= $min_confidence)
      RETURN n
    }}
    WITH center, collect(DISTINCT n) AS found
    WITH center, found, size(found) AS total
    WITH center, total,
         [center] + [x IN found WHERE x <> center][0..$max_nodes - 1] AS kept
    UNWIND kept AS n
    OPTIONAL MATCH (n)-[r:CALLS]->(m:Function)
    WHERE m IN kept AND r.confidence >= $min_confidence
    RETURN total,
           collect(DISTINCT {{
             id: n.fqn, name: n.name, module: n.module_fqn, class_fqn: n.class_fqn,
             is_test: n.is_test, is_entrypoint: n.is_entrypoint,
             complexity: n.complexity, line: n.line_start
           }}) AS nodes,
           collect(DISTINCT CASE WHEN r IS NULL THEN null ELSE {{
             source: startNode(r).fqn, target: endNode(r).fqn,
             confidence: r.confidence, line: r.line,
             resolution: r.resolution, via: r.via
           }} END) AS edges
    """


SHORTEST_PATH = """
MATCH (a:Function {repo_id: $repo_id, fqn: $from_fqn}),
      (b:Function {repo_id: $repo_id, fqn: $to_fqn})
MATCH path = shortestPath((a)-[:CALLS*..12]->(b))
RETURN [n IN nodes(path) | {fqn: n.fqn, name: n.name, module: n.module_fqn,
                            line: n.line_start}] AS hops,
       [r IN relationships(path) | {line: r.line, confidence: r.confidence,
                                    resolution: r.resolution}] AS edges,
       reduce(c = 1.0, r IN relationships(path) | c * r.confidence) AS confidence
"""

IMPORT_EDGES = """
MATCH (a:Module {repo_id: $repo_id})-[i:IMPORTS]->(b:Module {repo_id: $repo_id})
WHERE ($include_deferred OR (i.is_type_only = false AND i.is_deferred = false))
RETURN DISTINCT a.fqn AS source, b.fqn AS target
"""

HOTSPOTS = """
MATCH (caller:Function {repo_id: $repo_id})-[r:CALLS]->(f:Function {repo_id: $repo_id})
WHERE r.confidence >= $min_confidence
WITH f, count(DISTINCT caller) AS fan_in
WHERE fan_in >= $min_fan_in
RETURN f.fqn AS fqn,
       f.name AS name,
       f.module_fqn AS module,
       f.line_start AS line,
       fan_in,
       f.complexity AS complexity,
       fan_in * f.complexity AS risk_score
ORDER BY risk_score DESC, fan_in DESC, fqn ASC
LIMIT $limit
"""

#: "Candidates", never "dead code". Dynamic dispatch, reflection, plugin
#: registries and callers outside the repo all produce false positives — and an
#: override that is only ever called through its base class would too, which is
#: why the inheritance subquery excludes them.
DEAD_CODE = """
MATCH (f:Function {repo_id: $repo_id})
WHERE NOT EXISTS { MATCH (:Function)-[:CALLS]->(f) }
  AND f.is_test = false
  AND f.is_entrypoint = false
  AND f.is_dunder = false
  AND NOT f.name STARTS WITH 'test_'
  AND NOT EXISTS {
    MATCH (c:Class {repo_id: $repo_id})-[:CONTAINS]->(f)
    MATCH (c)-[:INHERITS*1..6]->(base:Class)-[:CONTAINS]->(inherited:Function)
    WHERE inherited.name = f.name
  }
RETURN f.fqn AS fqn,
       f.name AS name,
       f.module_fqn AS module,
       f.line_start AS line,
       f.complexity AS complexity,
       f.is_private AS is_private
ORDER BY f.complexity DESC, f.fqn ASC
LIMIT $limit
"""

RESOLUTION_BREAKDOWN = """
MATCH (:Function {repo_id: $repo_id})-[c:CALLS]->(:Function {repo_id: $repo_id})
RETURN c.resolution AS resolution, count(*) AS count
ORDER BY count DESC
"""

SAMPLE_FUNCTIONS = """
MATCH (f:Function {repo_id: $repo_id})
WHERE EXISTS { MATCH (:Function)-[:CALLS]->(f) }
RETURN f.fqn AS fqn
ORDER BY rand()
LIMIT $limit
"""
