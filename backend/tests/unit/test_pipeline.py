"""The ingestion pipeline: status lifecycle, failure recording, persisted stats."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from faultline.config import Settings
from faultline.graph import cypher
from faultline.ingest.pipeline import (
    STATUS_FAILED,
    STATUS_PARSING,
    STATUS_PENDING,
    STATUS_READY,
    STATUS_WRITING,
    IngestPipeline,
    _default_name,
    analyse_path,
)


class RecordingClient:
    def __init__(self, fail_on_write: bool = False) -> None:
        self.statuses: list[tuple[str, dict[str, Any]]] = []
        self.upserts: list[dict[str, Any]] = []
        self.fail_on_write = fail_on_write

    def write(self, query: str, /, **params: Any) -> list[dict[str, Any]]:
        if query is cypher.WRITE_REPO:
            self.upserts.append(params["props"])
        elif query is cypher.UPDATE_REPO_STATUS:
            self.statuses.append((params["status"], params["props"]))
        return []

    def write_batches(self, query: str, rows: list[dict[str, Any]], **_: Any) -> int:
        if self.fail_on_write:
            raise RuntimeError("disk full")
        return len(rows)

    def run_autocommit(self, query: str, /, **params: Any) -> list[dict[str, Any]]:
        return []


def pipeline(client: RecordingClient, **settings: Any) -> IngestPipeline:
    return IngestPipeline(client, Settings(**settings))  # type: ignore[arg-type]


# -- analyse_path ---------------------------------------------------------------------


def test_analyse_path_needs_no_database(fixture_root: Path) -> None:
    analysis = analyse_path(fixture_root, progress_every=0)
    assert analysis.file_count == 16
    # Hand-counted from the fixture source: 16 modules + 6 classes + 33 functions.
    assert analysis.node_count == 55
    # 30 calls + 18 imports + 2 inherits + 4 instantiations.
    assert analysis.edge_count == 54
    assert analysis.parse_seconds >= 0 and analysis.resolve_seconds >= 0


def test_analyse_path_logs_progress(fixture_root: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO"):
        analyse_path(fixture_root, progress_every=5)
    assert any(r.getMessage() == "parsing" for r in caplog.records)


def test_analyse_path_honours_exclude_dirs(fixture_root: Path) -> None:
    analysis = analyse_path(fixture_root, exclude_dirs=("tests",), progress_every=0)
    assert not any(m.is_test for m in analysis.resolved.modules)


def test_analyse_path_counts_loc(fixture_root: Path) -> None:
    assert analyse_path(fixture_root, progress_every=0).loc > 100


# -- register -------------------------------------------------------------------------


def test_register_creates_a_pending_repo() -> None:
    client = RecordingClient()
    repo_id = pipeline(client).register("https://github.com/pallets/flask", ref="3.0.x")
    (props,) = client.upserts
    assert props["id"] == repo_id
    assert props["status"] == STATUS_PENDING
    assert props["name"] == "flask"
    assert props["ref"] == "3.0.x"


def test_register_uses_an_explicit_name() -> None:
    client = RecordingClient()
    pipeline(client).register("/some/path", name="mine")
    assert client.upserts[0]["name"] == "mine"


def test_repo_ids_are_unique() -> None:
    p = pipeline(RecordingClient())
    assert p.register("/a") != p.register("/a")


# -- run: the happy path --------------------------------------------------------------


def test_status_moves_through_parsing_writing_ready(fixture_root: Path) -> None:
    client = RecordingClient()
    result = pipeline(client).run("r1", str(fixture_root))
    assert result is not None
    assert [s for s, _ in client.statuses] == [STATUS_PARSING, STATUS_WRITING, STATUS_READY]


def test_ready_status_persists_counts_timings_and_resolution_stats(fixture_root: Path) -> None:
    client = RecordingClient()
    pipeline(client).run("r1", str(fixture_root))
    status, props = client.statuses[-1]
    assert status == STATUS_READY
    assert props["file_count"] == 16
    assert props["parse_failures"] == 1
    assert props["error"] is None
    assert props["node_count"] > 0 and props["edge_count"] > 0
    for key in ("parse_seconds", "resolve_seconds", "write_seconds", "total_seconds"):
        assert props[key] >= 0
    # Resolution stats are stored, not recomputed: dropped calls never became
    # edges, so the graph alone could never reproduce these numbers.
    assert props["stat_call_sites_total"] == 35
    assert props["stat_dropped_builtin"] == 6
    assert props["stat_resolved_exact"] == 24
    assert "stat_internal_exact_rate" in props


def test_local_ingest_reports_the_directory_name(fixture_root: Path) -> None:
    client = RecordingClient()
    result = pipeline(client).run("r1", str(fixture_root))
    assert result is not None
    assert result.name == fixture_root.name


def test_explicit_name_wins(fixture_root: Path) -> None:
    result = pipeline(RecordingClient()).run("r1", str(fixture_root), name="custom")
    assert result is not None and result.name == "custom"


# -- run: failures land on the node, never in the caller ------------------------------


def test_a_rejected_source_marks_the_repo_failed_with_the_reason() -> None:
    client = RecordingClient()
    result = pipeline(client).run("r1", "file:///etc/passwd")
    assert result is None
    status, props = client.statuses[-1]
    assert status == STATUS_FAILED
    assert "unsupported scheme" in props["error"]


def test_a_missing_path_marks_the_repo_failed() -> None:
    client = RecordingClient()
    assert pipeline(client).run("r1", "/definitely/not/here") is None
    assert client.statuses[-1][0] == STATUS_FAILED


def test_a_path_outside_the_allowed_root_marks_the_repo_failed(
    fixture_root: Path, tmp_path: Path
) -> None:
    client = RecordingClient()
    pipeline(client, allowed_root=str(tmp_path)).run("r1", str(fixture_root))
    status, props = client.statuses[-1]
    assert status == STATUS_FAILED
    assert "outside the allowed root" in props["error"]


def test_an_unexpected_error_is_recorded_with_its_type(fixture_root: Path) -> None:
    client = RecordingClient(fail_on_write=True)
    result = pipeline(client).run("r1", str(fixture_root))
    assert result is None
    status, props = client.statuses[-1]
    assert status == STATUS_FAILED
    assert props["error"] == "RuntimeError: disk full"
    # It got as far as writing before it failed.
    assert STATUS_WRITING in [s for s, _ in client.statuses]


def test_a_failed_run_does_not_raise(fixture_root: Path) -> None:
    """This runs in a background task: there is nobody to raise at."""
    pipeline(RecordingClient(fail_on_write=True)).run("r1", str(fixture_root))


# -- delete --------------------------------------------------------------------------


def test_delete_is_batched_then_removes_the_root() -> None:
    calls: list[str] = []

    class Client(RecordingClient):
        def run_autocommit(self, query: str, /, **params: Any) -> list[dict[str, Any]]:
            calls.append("batched-delete")
            return []

        def write(self, query: str, /, **params: Any) -> list[dict[str, Any]]:
            if query is cypher.DELETE_REPO_ROOT:
                calls.append("delete-root")
            return []

    pipeline(Client()).delete("r1")
    assert calls == ["batched-delete", "delete-root"]


# -- helpers ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "name"),
    [
        ("https://github.com/a/b.git", "b"),
        ("https://github.com/a/b", "b"),
        ("/some/local/repo/", "repo"),
        ("repo", "repo"),
    ],
)
def test_default_name(source: str, name: str) -> None:
    assert _default_name(source) == name


def test_a_repo_with_no_python_files_fails_with_an_explanation_instead_of_reading_zero(
    tmp_path: Path,
) -> None:
    """Regression: a JavaScript-only repo finished as "ready" with 0 files, 0 nodes and
    0 edges, and the UI offered to explore it. An empty graph is not a result."""
    (tmp_path / "index.js").write_text("module.exports = {}\n", encoding="utf-8")
    client = RecordingClient()
    assert pipeline(client).run("r1", str(tmp_path)) is None
    status, props = client.statuses[-1]
    assert status == STATUS_FAILED
    assert "No Python (.py) files were found" in props["error"]
    assert "JavaScript" in props["error"]
    assert {s for s, _ in client.statuses}.isdisjoint({"writing", "ready"})


def test_a_python_repo_with_no_calls_is_still_ready(tmp_path: Path) -> None:
    """Only a missing language is a failure. Scripts that call nothing are a real, if
    small, result and are reported with a notice rather than rejected."""
    (tmp_path / "script.py").write_text("x = 1\nprint(x)\n", encoding="utf-8")
    client = RecordingClient()
    assert pipeline(client).run("r1", str(tmp_path)) is not None
    status, props = client.statuses[-1]
    assert status == "ready"
    assert props["file_count"] == 1
