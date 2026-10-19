"""Everything about accepting user-controlled input that touches the filesystem
and the network. These are the security tests the README claims exist."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from faultline.config import DEFAULT_EXCLUDE_DIRS, Settings
from faultline.ingest import repo_source
from faultline.ingest.repo_source import (
    SourceError,
    check_size,
    checkout,
    clone,
    explain_no_python_files,
    looks_like_url,
    repo_name_from_url,
    validate_local_path,
    validate_url,
)


def settings(**kw: Any) -> Settings:
    return Settings(**kw)


# -- URL validation -------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "git://github.com/a/b.git",
        "ssh://git@github.com/a/b.git",
        "http://github.com/a/b",  # https only
        "ftp://example.com/x",
        "git@github.com:a/b.git",
        "https://user:token@github.com/a/b.git",  # credentials in the URL
        "https:///nohost",
    ],
)
def test_rejected_urls(url: str) -> None:
    with pytest.raises(SourceError):
        validate_url(url)


def test_https_url_is_accepted() -> None:
    assert validate_url("https://github.com/pallets/flask") == "https://github.com/pallets/flask"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("https://github.com/a/b", True),
        ("git@github.com:a/b.git", True),
        ("file:///x", True),
        ("/some/local/path", False),
        ("C:\\Users\\me\\repo", False),
        ("./relative", False),
    ],
)
def test_looks_like_url(source: str, expected: bool) -> None:
    assert looks_like_url(source) is expected


@pytest.mark.parametrize(
    ("url", "name"),
    [
        ("https://github.com/pallets/flask", "flask"),
        ("https://github.com/pallets/flask.git", "flask"),
        ("https://github.com/pallets/flask/", "flask"),
        ("https://example.com/", "repo"),
    ],
)
def test_repo_name_from_url(url: str, name: str) -> None:
    assert repo_name_from_url(url) == name


# -- local paths ----------------------------------------------------------------------


def test_local_path_must_exist(tmp_path: Path) -> None:
    with pytest.raises(SourceError, match="not found"):
        validate_local_path(str(tmp_path / "nope"), settings())


def test_local_path_must_be_a_directory(tmp_path: Path) -> None:
    file = tmp_path / "f.py"
    file.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(SourceError, match="not a directory"):
        validate_local_path(str(file), settings())


def test_local_path_inside_the_allowed_root_is_accepted(tmp_path: Path) -> None:
    inside = tmp_path / "repos" / "mine"
    inside.mkdir(parents=True)
    got = validate_local_path(str(inside), settings(allowed_root=str(tmp_path / "repos")))
    assert got == inside.resolve()


def test_the_allowed_root_itself_is_accepted(tmp_path: Path) -> None:
    assert (
        validate_local_path(str(tmp_path), settings(allowed_root=str(tmp_path)))
        == tmp_path.resolve()
    )


def test_local_path_outside_the_allowed_root_is_rejected(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    with pytest.raises(SourceError, match="outside the allowed root"):
        validate_local_path(str(outside), settings(allowed_root=str(allowed)))


def test_dotdot_traversal_out_of_the_allowed_root_is_rejected(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    (tmp_path / "secret").mkdir()
    allowed.mkdir()
    sneaky = allowed / ".." / "secret"
    with pytest.raises(SourceError, match="outside the allowed root"):
        validate_local_path(str(sneaky), settings(allowed_root=str(allowed)))


def test_a_sibling_directory_sharing_a_name_prefix_is_not_inside_the_root(tmp_path: Path) -> None:
    """``/repos-evil`` starts with ``/repos`` as a string but is not inside it."""
    allowed = tmp_path / "repos"
    evil = tmp_path / "repos-evil"
    allowed.mkdir()
    evil.mkdir()
    with pytest.raises(SourceError, match="outside the allowed root"):
        validate_local_path(str(evil), settings(allowed_root=str(allowed)))


# -- size caps ------------------------------------------------------------------------


def test_size_check_counts_python_files(tmp_path: Path) -> None:
    for i in range(3):
        (tmp_path / f"m{i}.py").write_text("x = 1\n", encoding="utf-8")
    files, size = check_size(tmp_path, settings())
    assert files == 3
    assert size > 0


def test_too_many_python_files_is_rejected(tmp_path: Path) -> None:
    for i in range(4):
        (tmp_path / f"m{i}.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(SourceError, match="Python files"):
        check_size(tmp_path, settings(max_repo_files=3))


def test_too_many_bytes_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "big.bin").write_bytes(b"0" * (2 * 1024 * 1024))
    with pytest.raises(SourceError, match="exceeds 1 MB"):
        check_size(tmp_path, settings(max_repo_mb=1))


# -- cloning (subprocess is mocked; nothing touches the network) -------------------------


class FakeRun:
    def __init__(self, returncode: int = 0, stdout: str = "abc123\n", stderr: str = "") -> None:
        self.calls: list[list[str]] = []
        self.kwargs: list[dict[str, Any]] = []
        self.result = subprocess.CompletedProcess([], returncode, stdout, stderr)

    def __call__(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        self.kwargs.append(kwargs)
        return self.result


def test_clone_uses_an_argument_list_and_never_a_shell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeRun()
    monkeypatch.setattr(repo_source.subprocess, "run", fake)
    sha = clone("https://github.com/a/b", tmp_path / "dest", ref=None, settings=settings())
    assert sha == "abc123"
    clone_call = fake.calls[0]
    assert isinstance(clone_call, list)
    assert clone_call[:2] == ["git", "clone"]
    assert "--depth" in clone_call and "1" in clone_call
    assert all(kw.get("shell") in (None, False) for kw in fake.kwargs)
    assert all(kw.get("timeout") for kw in fake.kwargs)


def test_clone_separates_options_from_the_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--`` stops a hostile URL being parsed as an option."""
    fake = FakeRun()
    monkeypatch.setattr(repo_source.subprocess, "run", fake)
    clone("https://github.com/a/b", tmp_path / "dest", ref=None, settings=settings())
    args = fake.calls[0]
    assert args[args.index("--") + 1] == "https://github.com/a/b"


def test_clone_passes_a_valid_ref(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = FakeRun()
    monkeypatch.setattr(repo_source.subprocess, "run", fake)
    clone("https://github.com/a/b", tmp_path / "d", ref="release/3.0", settings=settings())
    args = fake.calls[0]
    assert args[args.index("--branch") + 1] == "release/3.0"


@pytest.mark.parametrize(
    "ref", ["--upload-pack=evil", "-x", "a b", "a;rm -rf /", "$(id)", "", "a" * 201]
)
def test_hostile_refs_never_reach_git(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ref: str
) -> None:
    fake = FakeRun()
    monkeypatch.setattr(repo_source.subprocess, "run", fake)
    if not ref:
        return  # an empty ref means "no ref" and is simply omitted
    with pytest.raises(SourceError, match="invalid git ref"):
        clone("https://github.com/a/b", tmp_path / "d", ref=ref, settings=settings())
    assert fake.calls == []


def test_git_failure_becomes_a_source_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        repo_source.subprocess,
        "run",
        FakeRun(returncode=128, stdout="", stderr="fatal: repository not found\n"),
    )
    with pytest.raises(SourceError, match="repository not found"):
        clone("https://github.com/a/nope", tmp_path / "d", ref=None, settings=settings())


def test_git_timeout_becomes_a_source_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def boom(*_: Any, **__: Any) -> None:
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(repo_source.subprocess, "run", boom)
    with pytest.raises(SourceError, match="timed out"):
        clone("https://github.com/a/b", tmp_path / "d", ref=None, settings=settings())


def test_missing_git_binary_becomes_a_source_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def boom(*_: Any, **__: Any) -> None:
        raise FileNotFoundError("git")

    monkeypatch.setattr(repo_source.subprocess, "run", boom)
    with pytest.raises(SourceError, match="not installed"):
        clone("https://github.com/a/b", tmp_path / "d", ref=None, settings=settings())


# -- checkout lifecycle ---------------------------------------------------------------------


def test_local_checkout_is_used_in_place_and_never_deleted(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    with checkout(str(tmp_path), settings=settings()) as repo:
        assert repo.root == tmp_path.resolve()
        assert repo.is_temporary is False
        assert repo.commit_sha is None
    assert (tmp_path / "m.py").exists()


def test_remote_checkout_is_deleted_even_when_the_body_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Path] = {}

    def fake_clone(url: str, destination: Path, *, ref: str | None, settings: Settings) -> str:
        destination.mkdir(parents=True)
        (destination / "m.py").write_text("x = 1\n", encoding="utf-8")
        seen["dest"] = destination
        return "deadbeef"

    monkeypatch.setattr(repo_source, "clone", fake_clone)
    with (
        pytest.raises(RuntimeError, match="boom"),
        checkout("https://github.com/a/b", settings=settings()) as repo,
    ):
        assert repo.is_temporary and repo.commit_sha == "deadbeef" and repo.name == "b"
        raise RuntimeError("boom")
    assert not seen["dest"].exists()
    assert not seen["dest"].parent.exists()  # the whole mkdtemp directory is gone


def test_remote_checkout_rejects_a_bad_url_before_cloning(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def fake_clone(*_: Any, **__: Any) -> str:
        nonlocal called
        called = True
        return ""

    monkeypatch.setattr(repo_source, "clone", fake_clone)
    with pytest.raises(SourceError), checkout("file:///etc", settings=settings()):
        pass
    assert called is False


# -- helpful errors -------------------------------------------------------------------


def test_a_windows_path_in_a_container_explains_why_it_cannot_be_seen(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    (root / "flask").mkdir(parents=True)
    with pytest.raises(SourceError) as info:
        validate_local_path(r"C:\Users\me\project", settings(allowed_root=str(root)))
    message = str(info.value)
    assert message.startswith("path not found: C:")
    assert "Windows path" in message
    assert "container" in message
    assert ".repos folder" in message
    assert "flask" in message  # the folders that *are* visible


def test_a_wrong_path_lists_what_the_server_can_see(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    for name in ("alpha", "beta"):
        (root / name).mkdir(parents=True)
    (root / ".hidden").mkdir()
    with pytest.raises(SourceError) as info:
        validate_local_path("/home/me/code", settings(allowed_root=str(root)))
    message = str(info.value)
    assert "alpha" in message and "beta" in message
    assert ".hidden" not in message
    assert "Windows path" not in message


def test_an_empty_allowed_root_says_to_copy_a_project_in(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    root.mkdir()
    with pytest.raises(SourceError, match="is empty: copy a project into it"):
        validate_local_path("/nope", settings(allowed_root=str(root)))


def test_a_long_folder_list_is_truncated(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    for i in range(12):
        (root / f"proj{i:02d}").mkdir(parents=True)
    with pytest.raises(SourceError) as info:
        validate_local_path("/nope", settings(allowed_root=str(root)))
    assert "proj07" in str(info.value)
    assert "proj08" not in str(info.value)
    assert str(info.value).endswith("...")


def test_without_an_allowed_root_the_message_stays_plain(tmp_path: Path) -> None:
    with pytest.raises(SourceError) as info:
        validate_local_path(str(tmp_path / "nope"), settings())
    assert "container" not in str(info.value)


# -- explaining an empty source -------------------------------------------------------


def write(root: Path, *names: str) -> None:
    for name in names:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n", encoding="utf-8")


def test_a_javascript_project_is_named_as_such(tmp_path: Path) -> None:
    write(tmp_path, "src/a.js", "src/b.js", "src/c.tsx", "README.md")
    message = explain_no_python_files(tmp_path, DEFAULT_EXCLUDE_DIRS)
    assert message.startswith("No Python (.py) files were found.")
    assert "looks like a JavaScript project (2 JavaScript files)" in message


def test_a_single_file_is_singular(tmp_path: Path) -> None:
    write(tmp_path, "index.js")
    message = explain_no_python_files(tmp_path, DEFAULT_EXCLUDE_DIRS)
    assert "a JavaScript project (1 JavaScript file)." in message


def test_the_dominant_language_wins(tmp_path: Path) -> None:
    write(tmp_path, "a.go", "b.go", "c.go", "d.rs")
    assert "a Go project (3 Go files)" in explain_no_python_files(tmp_path, DEFAULT_EXCLUDE_DIRS)


def test_notebooks_get_a_conversion_hint(tmp_path: Path) -> None:
    write(tmp_path, "analysis.ipynb", "other.ipynb")
    message = explain_no_python_files(tmp_path, DEFAULT_EXCLUDE_DIRS)
    assert "2 Jupyter notebooks (.ipynb)" in message
    assert "jupyter nbconvert --to script" in message


def test_a_single_notebook_is_singular(tmp_path: Path) -> None:
    write(tmp_path, "analysis.ipynb")
    assert "1 Jupyter notebook (.ipynb)" in explain_no_python_files(tmp_path, DEFAULT_EXCLUDE_DIRS)


def test_an_empty_folder_suggests_it_may_be_empty_or_skipped(tmp_path: Path) -> None:
    message = explain_no_python_files(tmp_path, DEFAULT_EXCLUDE_DIRS)
    assert "may be empty" in message
    assert "node_modules" in message


def test_excluded_directories_are_not_counted(tmp_path: Path) -> None:
    write(tmp_path, "node_modules/lib/x.js", "node_modules/lib/y.js")
    message = explain_no_python_files(tmp_path, DEFAULT_EXCLUDE_DIRS)
    assert "JavaScript" not in message
    assert "may be empty" in message
