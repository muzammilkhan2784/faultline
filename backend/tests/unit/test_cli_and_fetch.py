from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from faultline import cli
from faultline.ingest import repo_source
from faultline.ingest.pipeline import Analysis
from faultline.ingest.repo_source import SourceError, fetch_commit

# -- CLI ------------------------------------------------------------------------------------


def test_analyze_prints_a_readable_summary(
    fixture_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["analyze", str(fixture_root)]) == 0
    out = capsys.readouterr().out
    assert "Parsed 16 files" in out
    assert "(1 file(s) failed to parse)" in out
    assert "Call sites: 35" in out
    assert "shop.cycle_a -> shop.cycle_b -> shop.cycle_a" in out


def test_analyze_json_is_valid_and_complete(
    fixture_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["analyze", str(fixture_root), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["files"] == 16 and data["nodes"] == 55 and data["edges"] == 54
    assert data["resolution"]["call_sites_total"] == 35
    assert data["import_cycles"] == [["shop.cycle_a", "shop.cycle_b"]]


def test_impact_option_lists_callers(
    fixture_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["analyze", str(fixture_root), "--impact", "shop.utils.clamp", "--depth", "3"])
    out = capsys.readouterr().out
    assert code == 0
    assert "5 function(s) can be affected" in out
    assert "1  shop.services.billing._apply_discount" in out


def test_impact_in_json_mode(fixture_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["analyze", str(fixture_root), "--json", "--impact", "shop.utils.clamp"])
    data = json.loads(capsys.readouterr().out)
    assert {i["fqn"]: i["distance"] for i in data["impact"]}["shop.services.billing.charge"] == 2


def test_unknown_function_is_an_error(
    fixture_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["analyze", str(fixture_root), "--impact", "no.such.fn"]) == 1
    assert "no function named" in capsys.readouterr().err


def test_missing_directory_is_an_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["analyze", str(tmp_path / "nope")]) == 2
    assert "not a directory" in capsys.readouterr().err


def test_reverse_reach_respects_depth_and_confidence(fixture_analysis: Analysis) -> None:
    clamp = "shop.utils.clamp"
    assert len(cli.reverse_reach(fixture_analysis, clamp, 1, 0.5)) == 1
    assert len(cli.reverse_reach(fixture_analysis, clamp, 4, 0.5)) == 6
    save = "shop.models.User.save"
    loose = {f for f, _ in cli.reverse_reach(fixture_analysis, save, 1, 0.5)}
    strict = {f for f, _ in cli.reverse_reach(fixture_analysis, save, 1, 0.6)}
    assert "shop.services.notify.broadcast" in loose - strict


# -- pinned fetch ----------------------------------------------------------------------------


class RecordingRun:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        return subprocess.CompletedProcess(args, 0, "deadbeef\n", "")


def test_fetch_commit_fetches_by_sha_and_checks_it_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = RecordingRun()
    monkeypatch.setattr(repo_source.subprocess, "run", run)
    sha = fetch_commit("https://github.com/pallets/flask", "d73fa1cdcbd8", tmp_path / "flask")
    assert sha == "deadbeef"
    verbs = [c[1] if c[1] != "-C" else c[3] for c in run.calls]
    assert verbs[:4] == ["init", "remote", "fetch", "checkout"]
    fetch = next(c for c in run.calls if "fetch" in c)
    assert fetch[-2:] == ["origin", "d73fa1cdcbd8"]
    assert "--depth" in fetch
    assert (tmp_path / "flask").is_dir()


@pytest.mark.parametrize(
    "bad", ["", "HEAD", "main", "--upload-pack=x", "d73fa1c; rm -rf /", "xyz1234", "abc"]
)
def test_fetch_commit_rejects_anything_that_is_not_a_sha(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bad: str
) -> None:
    run = RecordingRun()
    monkeypatch.setattr(repo_source.subprocess, "run", run)
    with pytest.raises(SourceError, match="invalid commit"):
        fetch_commit("https://github.com/a/b", bad, tmp_path / "x")
    assert run.calls == []


def test_fetch_commit_rejects_unsafe_urls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run = RecordingRun()
    monkeypatch.setattr(repo_source.subprocess, "run", run)
    with pytest.raises(SourceError):
        fetch_commit("file:///etc", "d73fa1c", tmp_path / "x")
    assert run.calls == []


def test_an_empty_folder_is_an_error_with_an_explanation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "app.js").write_text("1\n", encoding="utf-8")
    assert cli.main(["analyze", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "No Python (.py) files were found" in err
    assert "JavaScript" in err
