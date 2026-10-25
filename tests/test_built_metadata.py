"""A real build backend independently expands optional groups into wheel metadata."""

import email.parser
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from marker_scenarios.core import evaluate, parse_project


def test_activation_matches_built_wheel_metadata(tmp_path):
    source = """[build-system]
requires = ["setuptools>=77", "wheel"]
build-backend = "setuptools.build_meta"
[project]
name = "local-marker-oracle"
version = "1.0.0"
requires-python = ">=3.11"
dependencies = [
    "corepkg>=1",
    "basedoc; extra == 'docs'",
    "negative; extra != 'docs'",
    "windowspkg; sys_platform == 'win32'",
]
[project.optional-dependencies]
docs = ["docpkg", "impossible; extra == 'speed'"]
speed = ["speedpkg; sys_platform != 'win32'"]
[tool.setuptools]
py-modules = ["dummy"]
"""
    (tmp_path / "pyproject.toml").write_text(source)
    (tmp_path / "dummy.py").write_text('"""Synthetic build fixture, never installed."""\n')
    built = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--no-isolation"],
        cwd=tmp_path,
        env=os.environ,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    with zipfile.ZipFile(next((tmp_path / "dist").glob("*.whl"))) as wheel:
        metadata_name = next(p for p in wheel.namelist() if p.endswith(".dist-info/METADATA"))
        metadata = email.parser.Parser().parsestr(wheel.read(metadata_name).decode())
    requirements = [Requirement(r) for r in metadata.get_all("Requires-Dist")]
    examples = Path(__file__).resolve().parents[1] / "examples"
    fixture = json.loads((examples / "scenarios.json").read_text())
    for row in fixture["scenarios"]:
        row.pop("exact", None)
        row.pop("absent", None)
    fixture["scenarios"][2]["extras"] = ["docs", "speed"]
    result = evaluate(parse_project(source), fixture)
    for scenario, reported in zip(fixture["scenarios"], result["scenarios"], strict=True):
        env = fixture["environments"][scenario["environment"]]
        contexts = ["", *scenario["extras"]]
        expected = sorted(
            {
                str(canonicalize_name(r.name))
                for r in requirements
                if r.marker is None
                or any(r.marker.evaluate(environment=env | {"extra": e}) for e in contexts)
            }
        )
        assert reported["active_names"] == expected
