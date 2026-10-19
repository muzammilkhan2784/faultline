from __future__ import annotations

from pathlib import Path

import pytest

from faultline.parsing.discovery import (
    count_loc,
    derive_module_fqn,
    discover,
    is_excluded,
    is_test_path,
    iter_python_files,
)


def make(root: Path, *relative: str) -> None:
    for rel in relative:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n", encoding="utf-8")


# -- module FQN derivation (spec section 7.2 worked examples) ---------------------


def test_src_layout_without_init_in_src(tmp_path: Path) -> None:
    make(tmp_path, "src/pkg/__init__.py", "src/pkg/mod.py")
    assert derive_module_fqn(tmp_path, tmp_path / "src/pkg/mod.py") == ("pkg.mod", False)


def test_package_init_is_the_package_itself(tmp_path: Path) -> None:
    make(tmp_path, "src/pkg/__init__.py")
    assert derive_module_fqn(tmp_path, tmp_path / "src/pkg/__init__.py") == ("pkg", True)


def test_nested_packages(tmp_path: Path) -> None:
    make(tmp_path, "pkg/__init__.py", "pkg/sub/__init__.py", "pkg/sub/mod.py")
    assert derive_module_fqn(tmp_path, tmp_path / "pkg/sub/mod.py") == ("pkg.sub.mod", False)


def test_script_outside_any_package(tmp_path: Path) -> None:
    make(tmp_path, "scripts/tool.py")
    assert derive_module_fqn(tmp_path, tmp_path / "scripts/tool.py") == ("tool", False)


def test_a_gap_in_the_init_chain_is_a_namespace_subpackage(tmp_path: Path) -> None:
    """``top/gap/`` has no __init__.py but lives inside the regular package
    ``top``, so it is a PEP 420 namespace subpackage and keeps its name.

    This is Flask's real layout (``flask/sansio/`` has no ``__init__.py``). The
    spec's original rule -- stop at the first missing ``__init__.py`` -- named
    ``flask/sansio/app.py`` plain ``app`` and broke every import of it.
    """
    make(tmp_path, "top/__init__.py", "top/gap/inner/__init__.py", "top/gap/inner/mod.py")
    assert derive_module_fqn(tmp_path, tmp_path / "top/gap/inner/mod.py") == (
        "top.gap.inner.mod",
        False,
    )


def test_namespace_directory_directly_under_a_package(tmp_path: Path) -> None:
    make(tmp_path, "src/flask/__init__.py", "src/flask/sansio/app.py")
    assert derive_module_fqn(tmp_path, tmp_path / "src/flask/sansio/app.py") == (
        "flask.sansio.app",
        False,
    )


def test_a_directory_with_no_package_above_it_is_still_a_script(tmp_path: Path) -> None:
    """Without a regular package above, there is nothing to attach a namespace to."""
    make(tmp_path, "examples/demo/app.py")
    assert derive_module_fqn(tmp_path, tmp_path / "examples/demo/app.py") == ("app", False)


def test_the_repo_root_is_never_treated_as_a_package(tmp_path: Path) -> None:
    make(tmp_path, "__init__.py", "pkg/__init__.py", "pkg/mod.py")
    assert derive_module_fqn(tmp_path, tmp_path / "pkg/mod.py") == ("pkg.mod", False)


def test_root_level_init(tmp_path: Path) -> None:
    make(tmp_path, "__init__.py")
    fqn, is_package = derive_module_fqn(tmp_path, tmp_path / "__init__.py")
    assert is_package is True
    assert fqn == tmp_path.name


# -- exclusion --------------------------------------------------------------------


@pytest.mark.parametrize(
    "parts",
    [
        (".venv", "lib"),
        ("node_modules", "x"),
        ("pkg", "__pycache__"),
        ("migrations",),
        ("build",),
        ("proj.egg-info",),
        (".git",),
    ],
)
def test_excluded_segments(parts: tuple[str, ...]) -> None:
    from faultline.config import DEFAULT_EXCLUDE_DIRS

    assert is_excluded(parts, DEFAULT_EXCLUDE_DIRS)


def test_not_excluded() -> None:
    from faultline.config import DEFAULT_EXCLUDE_DIRS

    assert not is_excluded(("pkg", "sub"), DEFAULT_EXCLUDE_DIRS)


def test_iter_prunes_excluded_directories(tmp_path: Path) -> None:
    make(tmp_path, "pkg/a.py", ".venv/lib/b.py", "pkg/migrations/0001.py", "x.egg-info/c.py")
    found = {p.relative_to(tmp_path).as_posix() for p in iter_python_files(tmp_path)}
    assert found == {"pkg/a.py"}


def test_symlinks_are_not_followed(tmp_path: Path) -> None:
    make(tmp_path, "real/a.py")
    link = tmp_path / "link"
    try:
        link.symlink_to(tmp_path / "real", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")
    found = {p.relative_to(tmp_path).as_posix() for p in iter_python_files(tmp_path)}
    assert found == {"real/a.py"}


# -- test-module rule (spec 10.2): one definition, in one place --------------------


@pytest.mark.parametrize(
    "rel",
    ["tests/test_a.py", "tests/sub/helper.py", "pkg/test_a.py", "pkg/a_test.py", "conftest.py"],
)
def test_is_test_path_true(rel: str) -> None:
    assert is_test_path(rel)


@pytest.mark.parametrize("rel", ["pkg/a.py", "pkg/latest.py", "pkg/contest.py", "src/pkg/mod.py"])
def test_is_test_path_false(rel: str) -> None:
    assert not is_test_path(rel)


# -- discover ---------------------------------------------------------------------


def test_discover_marks_packages_and_tests(tmp_path: Path) -> None:
    make(tmp_path, "pkg/__init__.py", "pkg/mod.py", "tests/test_mod.py")
    found = {f.rel_path: f for f in discover(tmp_path)}
    assert found["pkg/__init__.py"].is_package
    assert found["pkg/mod.py"].module_fqn == "pkg.mod"
    assert found["tests/test_mod.py"].is_test
    assert not found["pkg/mod.py"].is_test


def test_colliding_module_names_fall_back_to_path(tmp_path: Path) -> None:
    """scripts/build.py and tools/build.py are both just ``build``; neither is dropped."""
    make(tmp_path, "scripts/build.py", "tools/build.py")
    fqns = sorted(f.module_fqn for f in discover(tmp_path))
    assert fqns == ["scripts.build", "tools.build"]


def test_colliding_package_inits_keep_a_valid_name(tmp_path: Path) -> None:
    make(tmp_path, "a/pkg/__init__.py", "b/pkg/__init__.py")
    fqns = sorted(f.module_fqn for f in discover(tmp_path))
    assert fqns == ["a.pkg", "b.pkg"]


def test_discover_is_sorted_and_deterministic(tmp_path: Path) -> None:
    make(tmp_path, "z.py", "a.py", "m.py")
    assert [f.module_fqn for f in discover(tmp_path)] == ["a", "m", "z"]


# -- loc ---------------------------------------------------------------------------


def test_count_loc_skips_blank_and_comment_lines() -> None:
    assert count_loc("# comment\n\nx = 1\n   \n  # indented comment\ny = 2\n") == 2


def test_count_loc_empty() -> None:
    assert count_loc("") == 0
