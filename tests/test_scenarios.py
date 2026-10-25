import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from packaging.markers import Marker

from marker_scenarios.core import (
    InputError,
    capture_environment,
    evaluate,
    load_scenarios,
    parse_project,
    validate_scenarios,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def fixture():
    return json.loads((EXAMPLES / "scenarios.json").read_text())


def project(dependencies, extras=None, requires=">=3.11"):
    text = '[project]\nname="test-project"\nrequires-python=' + json.dumps(requires)
    text += "\ndependencies=" + json.dumps(dependencies)
    if extras:
        text += "\n[project.optional-dependencies]\n"
        text += "\n".join(json.dumps(k) + "=" + json.dumps(v) for k, v in extras.items())
    return parse_project(text)


def one_fixture(extras=None):
    data = fixture()
    data["scenarios"] = [{"name": "test", "environment": "linux311", "extras": extras or []}]
    return data


def test_end_to_end_example():
    result = evaluate(parse_project((EXAMPLES / "pyproject.toml").read_text()), fixture())
    assert result["summary"] == {"scenarios": 3, "declarations": 5, "failures": 0, "uncovered": 0}
    assert result["scenarios"][0]["active_names"] == ["platformdirs", "typing-extensions"]
    assert result["scenarios"][1]["active_names"] == ["platformdirs", "pywin32", "sphinx"]


def test_no_host_fallback_and_native_marker_parity(monkeypatch):
    data = one_fixture()
    fake_host = {k: "wrong-host" for k in data["environments"]["linux311"]}
    monkeypatch.setattr("packaging.markers.default_environment", lambda: fake_host.copy())
    expressions = [
        "sys_platform == 'linux'",
        "python_version < '3.12'",
        "os_name == 'nt' or platform_machine == 'x86_64'",
        "python_full_version >= '3.11.8' and implementation_name == 'cpython'",
    ]
    result = evaluate(project([f"p{i}; {m}" for i, m in enumerate(expressions)]), data)
    env = data["environments"]["linux311"] | {"extra": ""}
    assert [r["active"] for r in result["scenarios"][0]["declarations"]] == [
        Marker(m).evaluate(environment=env) for m in expressions
    ]
    assert len(result["scenarios"][0]["active_names"]) == 4


def test_base_plus_selected_extra_union_and_group_gating():
    model = project(
        ["core", "baseextra; extra == 'docs'", "negative; extra != 'docs'"],
        {"Docs": ["doclib", "impossible; extra == 'speed'"], "speed": ["fastlib"]},
    )
    result = evaluate(model, one_fixture(["DOCS", "speed"]))
    assert result["scenarios"][0]["active_names"] == [
        "baseextra",
        "core",
        "doclib",
        "fastlib",
        "negative",
    ]
    assert result["scenarios"][0]["declarations"][4]["active"] is False
    assert result["scenarios"][0]["declarations"][1]["matched_extras"] == ["docs"]


def test_optional_not_selected_and_uncovered_are_visible():
    model = project(["windows; sys_platform == 'win32'"], {"docs": ["sphinx"]})
    result = evaluate(model, one_fixture())
    assert result["uncovered"] == ["base[0]", "extra:docs[0]"]
    assert [r["reason"] for r in result["scenarios"][0]["declarations"]] == [
        "marker_false",
        "extra_not_selected",
    ]


def test_multiple_entries_same_dependency_are_preserved():
    model = project(["foo>=2", "Foo<2", "foo==7; sys_platform == 'win32'"])
    result = evaluate(model, one_fixture())
    assert result["scenarios"][0]["active_names"] == ["foo"]
    assert len(result["declarations"]) == 3
    assert len(result["coverage"]["base[0]"]) == 1
    # No claim that the mutually incompatible specifiers are satisfiable.
    assert result["summary"]["failures"] == 0


def test_expectations_fail_with_names_and_scenario():
    data = one_fixture()
    data["scenarios"][0].update(present=["missing"], absent=["foo"], exact=["missing"])
    result = evaluate(project(["foo"]), data)
    assert {f["code"] for f in result["failures"]} == {
        "expected_present",
        "expected_absent",
        "exact_mismatch",
    }
    assert all(f["scenario"] == "test" for f in result["failures"])


def test_empty_exact_expectation_and_empty_project():
    data = one_fixture()
    data["scenarios"][0]["exact"] = []
    assert evaluate(project([]), data)["summary"]["failures"] == 0
    assert evaluate(project(["foo"]), data)["summary"]["failures"] == 1


def test_requires_python_failure_does_not_increase_coverage():
    result = evaluate(project(["foo"], requires=">=3.12"), one_fixture())
    assert result["failures"][0]["code"] == "requires_python_mismatch"
    assert result["coverage"]["base[0]"] == []
    assert not result["scenarios"][0]["supported_python"]


def test_direct_reference_url_never_appears_in_output():
    model = project(["foo @ https://user:TOPSECRET@example.invalid/foo.whl"])
    result = evaluate(model, one_fixture())
    assert "TOPSECRET" not in json.dumps(result)
    assert result["declarations"][0]["direct_reference"] is True


@pytest.mark.parametrize(
    "text",
    [
        '[tool.poetry]\nname="old-format"',
        '[project]\nname="x"\ndynamic=["dependencies"]',
        '[project]\nname="x"\ndynamic=["optional-dependencies"]',
        '[project]\nname="x"\ndependencies="foo"',
        '[project]\nname="x"\ndependencies=[2]',
        '[project]\nname="x"\nrequires-python="nonsense"',
        '[project]\nname="x"\ndependencies=["secret==not a requirement"]',
        '[project]\nname="x"\n[project.optional-dependencies]\nfoo_bar=[]\nfoo-bar=[]',
    ],
)
def test_invalid_or_dynamic_metadata_rejected(text):
    with pytest.raises(InputError) as error:
        parse_project(text)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("expression", ["'foo' in extras", "'dev' in dependency_groups"])
def test_lockfile_context_is_rejected(expression):
    with pytest.raises(InputError, match="lock-file-only"):
        project([f"foo; {expression}"])


def test_literal_marker_words_are_not_rejected():
    assert project(["foo; platform_release == 'extras' or platform_release == 'dependency_groups'"])


def test_unknown_extra_rejected():
    with pytest.raises(InputError, match="extra"):
        evaluate(project(["foo"]), one_fixture(["typo"]))


@pytest.mark.parametrize("key", sorted(fixture()["environments"]["linux311"]))
def test_every_missing_environment_key_is_error(key):
    data = one_fixture()
    del data["environments"]["linux311"][key]
    with pytest.raises(InputError, match="11"):
        validate_scenarios(data)


@pytest.mark.parametrize(
    "modification",
    [
        {"python_version": "3.12"},
        {"python_full_version": "3.11"},
        {"python_full_version": "not-version"},
        {"extra": "docs"},
        {"os_name": False},
    ],
)
def test_bad_environment_rejected(modification):
    data = one_fixture()
    data["environments"]["linux311"].update(modification)
    with pytest.raises(InputError):
        validate_scenarios(data)


@pytest.mark.parametrize(
    "change",
    [
        {"present": ["Foo", "foo"]},
        {"extra": ["docs"]},
        {"environment": "missing"},
        {"present": ["foo"], "absent": ["FOO"]},
        {"present": ["foo"], "exact": []},
        {"absent": ["foo"], "exact": ["foo"]},
        {"name": "control\nname"},
        {"extras": "docs"},
    ],
)
def test_bad_scenario_rejected(change):
    data = one_fixture()
    data["scenarios"][0].update(change)
    with pytest.raises(InputError):
        validate_scenarios(data)


def test_duplicate_scenarios_and_json_members(tmp_path):
    data = one_fixture()
    data["scenarios"].append(copy.deepcopy(data["scenarios"][0]))
    with pytest.raises(InputError, match="duplicate"):
        validate_scenarios(data)
    path = tmp_path / "bad.json"
    path.write_text('{"schema":1,"schema":1}')
    with pytest.raises(InputError, match="duplicate"):
        load_scenarios(path)


def test_capture_roundtrips():
    captured = capture_environment("my-target")
    assert validate_scenarios(captured) == captured
    assert evaluate(project([]), captured)["summary"]["failures"] == 0


def cli(*args):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    return subprocess.run(
        [sys.executable, "-m", "marker_scenarios", *map(str, args)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_cli_example_capture_failures_and_no_clobber(tmp_path):
    example = EXAMPLES / "pyproject.toml"
    original = example.read_bytes()
    run = cli("check", example, "--scenarios", EXAMPLES / "scenarios.json", "--fail-uncovered")
    assert run.returncode == 0, run.stderr
    assert "3 scenarios; 0 failures; 0 declarations uncovered" in run.stdout
    target = tmp_path / "target.json"
    assert cli("capture", "--output", target).returncode == 0
    assert cli("capture", "--output", target).returncode == 2
    data = one_fixture()
    data["scenarios"][0]["present"] = ["absent-package"]
    target.write_text(json.dumps(data))
    assert cli("check", example, "--scenarios", target).returncode == 1
    data["scenarios"][0].pop("present")
    target.write_text(json.dumps(data))
    assert cli("check", example, "--scenarios", target).returncode == 0
    assert cli("check", example, "--scenarios", target, "--fail-uncovered").returncode == 1
    assert cli("check", tmp_path / "missing", "--scenarios", target).returncode == 2
    assert example.read_bytes() == original
