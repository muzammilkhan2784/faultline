from __future__ import annotations

from faultline.analysis.test_selection import node_id, pytest_command


def test_node_id_for_a_plain_function() -> None:
    test = {"path": "tests/test_auth.py", "name": "test_login", "module": "tests.test_auth"}
    assert node_id(test) == "tests/test_auth.py::test_login"


def test_node_id_for_a_method_includes_the_class() -> None:
    test = {
        "path": "tests/test_auth.py",
        "name": "test_expired",
        "class_fqn": "tests.test_auth.TestLogin",
    }
    assert node_id(test) == "tests/test_auth.py::TestLogin::test_expired"


def test_node_id_falls_back_to_the_module_path() -> None:
    assert node_id({"module": "tests.test_x", "name": "test_a"}) == "tests/test_x.py::test_a"


def test_pytest_command_lists_node_ids() -> None:
    tests = [
        {"path": "tests/test_auth.py", "name": "test_expired_token"},
        {"path": "tests/test_api.py", "name": "test_login_flow"},
    ]
    assert pytest_command(tests) == (
        "pytest tests/test_auth.py::test_expired_token tests/test_api.py::test_login_flow"
    )


def test_pytest_command_is_empty_when_nothing_is_selected() -> None:
    assert pytest_command([]) == ""


def test_long_selection_collapses_to_files() -> None:
    tests = [{"path": f"tests/test_{i % 3}.py", "name": f"test_{i}"} for i in range(50)]
    command = pytest_command(tests, max_ids=40)
    assert command == "pytest tests/test_0.py tests/test_1.py tests/test_2.py"
