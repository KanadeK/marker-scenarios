"""Evaluate declared dependency activation, never dependency resolution."""

from __future__ import annotations

import json
import re
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path

from packaging.markers import UndefinedComparison, default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import InvalidName, canonicalize_name
from packaging.version import InvalidVersion, Version

ENV_KEYS = frozenset(
    {
        "implementation_name",
        "implementation_version",
        "os_name",
        "platform_machine",
        "platform_release",
        "platform_system",
        "platform_version",
        "python_full_version",
        "platform_python_implementation",
        "python_version",
        "sys_platform",
    }
)
ENGINE = "packaging/25.0"
MAX_INPUT = 2 * 1024 * 1024


class InputError(ValueError):
    """Invalid input with no raw dependency URL in diagnostics."""


def read_text(path: Path) -> str:
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            raise InputError("input must be a regular file")
        with path.open("rb") as stream:
            data = stream.read(MAX_INPUT + 1)
        if len(data) > MAX_INPUT:
            raise InputError("input exceeds 2 MiB")
        return data.decode("utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise InputError(f"cannot read input ({type(exc).__name__})") from None


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError("duplicate JSON member")
        result[key] = value
    return result


def load_scenarios(path: Path) -> dict:
    try:
        return validate_scenarios(json.loads(read_text(path), object_pairs_hook=_pairs))
    except (ValueError, RecursionError) as exc:
        if isinstance(exc, InputError):
            raise
        raise InputError("invalid scenario JSON") from None


def identifier(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value):
        raise InputError(
            "identifiers must be 1..128 ASCII letters, digits, dots, underscores or dashes"
        )
    return value


def package_name(value) -> str:
    if not isinstance(value, str) or len(value) > 200:
        raise InputError("package and extra names must be strings of at most 200 characters")
    try:
        return str(canonicalize_name(value, validate=True))
    except InvalidName:
        raise InputError("invalid package or extra name") from None


def names(value, field: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 1000:
        raise InputError(f"{field} must be an array of at most 1000 names")
    normalized = [package_name(item) for item in value]
    if len(set(normalized)) != len(normalized):
        raise InputError(f"{field} contains duplicate normalized names")
    return sorted(normalized)


def validate_environment(value) -> dict:
    if not isinstance(value, dict) or set(value) != ENV_KEYS:
        raise InputError(
            "each environment must explicitly supply all 11 PEP 508 environment fields"
        )
    if any(not isinstance(v, str) or len(v) > 1024 for v in value.values()):
        raise InputError("environment values must be strings of at most 1024 characters")
    try:
        full = Version(value["python_full_version"])
        Version(value["implementation_version"])
    except InvalidVersion:
        raise InputError("invalid Python or implementation version") from None
    if len(full.release) < 3 or value["python_version"] != ".".join(map(str, full.release[:2])):
        raise InputError("python_full_version requires a patch level matching python_version")
    return dict(sorted(value.items()))


def validate_scenarios(data) -> dict:
    if not isinstance(data, dict) or set(data) != {"schema", "environments", "scenarios"}:
        raise InputError("scenario file must contain schema, environments and scenarios")
    if type(data["schema"]) is not int or data["schema"] != 1:
        raise InputError("unsupported scenario schema")
    envs = data["environments"]
    if not isinstance(envs, dict) or not 1 <= len(envs) <= 64:
        raise InputError("provide 1..64 named environments")
    environments = {identifier(k): validate_environment(v) for k, v in envs.items()}
    scenarios = data["scenarios"]
    if not isinstance(scenarios, list) or not 1 <= len(scenarios) <= 128:
        raise InputError("provide 1..128 scenarios")
    result = []
    seen = set()
    for scenario in scenarios:
        if not isinstance(scenario, dict) or not {"name", "environment"} <= scenario.keys():
            raise InputError("each scenario requires name and environment")
        if set(scenario) - {"name", "environment", "extras", "present", "absent", "exact"}:
            raise InputError("unknown scenario field")
        name = identifier(scenario["name"])
        reference = identifier(scenario["environment"])
        if name in seen:
            raise InputError("duplicate scenario name")
        seen.add(name)
        if reference not in environments:
            raise InputError("scenario references an unknown environment")
        row = {"name": name, "environment": reference}
        for field in ("extras", "present", "absent", "exact"):
            if field in scenario or field == "extras":
                row[field] = names(scenario.get(field, []), field)
        if len(row["extras"]) > 64:
            raise InputError("at most 64 selected extras per scenario")
        present, absent = set(row.get("present", [])), set(row.get("absent", []))
        if present & absent:
            raise InputError("present and absent expectations conflict")
        if "exact" in row and (present - set(row["exact"]) or absent & set(row["exact"])):
            raise InputError("exact expectations conflict with present/absent")
        result.append(row)
    return {"schema": 1, "environments": environments, "scenarios": result}


@dataclass(frozen=True)
class Declaration:
    identifier: str
    group: str | None
    requirement: Requirement

    def public(self) -> dict:
        req = self.requirement
        # Direct reference URLs can contain credentials. Never emit them.
        return {
            "id": self.identifier,
            "group": self.group,
            "name": package_name(req.name),
            "dependency_extras": sorted(req.extras),
            "specifier": str(req.specifier),
            "direct_reference": bool(req.url),
            "marker": str(req.marker) if req.marker else None,
        }


@dataclass(frozen=True)
class Project:
    name: str
    requires_python: SpecifierSet
    extras: frozenset[str]
    declarations: tuple[Declaration, ...]


def parse_project(text: str) -> Project:
    try:
        if len(text.encode("utf-8")) > MAX_INPUT:
            raise InputError("project input exceeds 2 MiB")
        document = tomllib.loads(text)
    except (ValueError, RecursionError) as exc:
        if isinstance(exc, InputError):
            raise
        raise InputError("invalid project TOML") from None
    project = document.get("project")
    if not isinstance(project, dict):
        raise InputError("a static [project] table is required")
    name = package_name(project.get("name"))
    dynamic = project.get("dynamic", [])
    if not isinstance(dynamic, list) or any(not isinstance(v, str) for v in dynamic):
        raise InputError("project.dynamic must be an array of names")
    if set(dynamic) & {"dependencies", "optional-dependencies", "requires-python"}:
        raise InputError("dynamic dependency or requires-python metadata is not supported")
    required = project.get("requires-python", "")
    if not isinstance(required, str):
        raise InputError("requires-python must be a string")
    try:
        requires_python = SpecifierSet(required)
    except InvalidSpecifier:
        raise InputError("invalid requires-python specifier") from None
    optional = project.get("optional-dependencies", {})
    if not isinstance(optional, dict) or len(optional) > 64:
        raise InputError("optional-dependencies must be a table of at most 64 extras")
    groups = [(None, project.get("dependencies", []))]
    seen = set()
    for extra, requirements in optional.items():
        extra = package_name(extra)
        if extra in seen:
            raise InputError("optional dependency names collide after normalization")
        seen.add(extra)
        groups.append((extra, requirements))
    declarations = []
    for group, requirements in groups:
        if not isinstance(requirements, list):
            raise InputError("dependencies must be arrays of PEP 508 strings")
        for index, raw in enumerate(requirements):
            label = f"extra:{group}[{index}]" if group is not None else f"base[{index}]"
            if not isinstance(raw, str) or len(raw) > 4096:
                raise InputError(
                    f"{label}: requirement must be a string of at most 4096 characters"
                )
            try:
                req = Requirement(raw)
            except (InvalidRequirement, RecursionError):
                raise InputError(f"{label}: invalid PEP 508 requirement") from None
            if req.marker:
                # Only metadata-context markers are supported. Strip literals before
                # checking variable tokens so words inside quoted values are harmless.
                expression = re.sub(r""""[^"]*"|'[^']*' """, "", str(req.marker), flags=re.X)
                if re.search(r"\b(extras|dependency_groups)\b", expression):
                    raise InputError(f"{label}: lock-file-only marker variables are not supported")
            declarations.append(Declaration(label, group, req))
            if len(declarations) > 1000:
                raise InputError("at most 1000 dependency declarations are supported")
    return Project(name, requires_python, frozenset(seen), tuple(declarations))


def capture_environment(name="current") -> dict:
    name = identifier(name)
    return {
        "schema": 1,
        "environments": {name: validate_environment(default_environment())},
        "scenarios": [{"name": name, "environment": name, "extras": []}],
    }


def evaluate(project: Project, fixture: dict) -> dict:
    fixture = validate_scenarios(fixture)
    coverage = {item.identifier: [] for item in project.declarations}
    results = []
    failures = []
    evaluations = 0
    for scenario in fixture["scenarios"]:
        unknown = set(scenario["extras"]) - project.extras
        if unknown:
            raise InputError(f"{scenario['name']}: selected extra is not declared by the project")
        env = fixture["environments"][scenario["environment"]]
        supported = project.requires_python.contains(env["python_full_version"], prereleases=True)
        diagnostics = []
        if not supported:
            diagnostics.append({"code": "requires_python_mismatch"})
        rows = []
        for item in project.declarations:
            eligible = item.group is None or item.group in scenario["extras"]
            contexts = [item.group] if item.group is not None else ["", *scenario["extras"]]
            matched = []
            if supported and eligible:
                for extra in contexts:
                    evaluations += 1
                    if evaluations > 250000:
                        raise InputError("scenario matrix exceeds 250000 marker evaluations")
                    try:
                        active = (
                            item.requirement.marker is None
                            or item.requirement.marker.evaluate(
                                environment={**env, "extra": extra}, context="metadata"
                            )
                        )
                    except (ValueError, KeyError, UndefinedComparison, RecursionError) as exc:
                        raise InputError(
                            f"{scenario['name']} {item.identifier}: "
                            f"marker evaluation failed ({type(exc).__name__})"
                        ) from None
                    if active:
                        matched.append(extra)
            if matched:
                coverage[item.identifier].append(scenario["name"])
            reason = (
                "matched"
                if matched
                else (
                    "requires_python_mismatch"
                    if not supported
                    else "extra_not_selected"
                    if not eligible
                    else "marker_false"
                )
            )
            rows.append(
                {
                    "id": item.identifier,
                    "active": bool(matched),
                    "reason": reason,
                    "matched_extras": matched,
                }
            )
        active_names = sorted(
            {
                package_name(item.requirement.name)
                for item, row in zip(project.declarations, rows, strict=True)
                if row["active"]
            }
        )
        actual = set(active_names)
        missing = set(scenario.get("present", [])) - actual
        forbidden = set(scenario.get("absent", [])) & actual
        if missing:
            diagnostics.append({"code": "expected_present", "names": sorted(missing)})
        if forbidden:
            diagnostics.append({"code": "expected_absent", "names": sorted(forbidden)})
        if "exact" in scenario and actual != set(scenario["exact"]):
            expected = set(scenario["exact"])
            diagnostics.append(
                {
                    "code": "exact_mismatch",
                    "missing": sorted(expected - actual),
                    "unexpected": sorted(actual - expected),
                }
            )
        failures.extend({"scenario": scenario["name"], **d} for d in diagnostics)
        results.append(
            {
                "name": scenario["name"],
                "environment": scenario["environment"],
                "extras": scenario["extras"],
                "supported_python": supported,
                "active_names": active_names,
                "declarations": rows,
                "diagnostics": diagnostics,
            }
        )
    uncovered = sorted(k for k, witnesses in coverage.items() if not witnesses)
    return {
        "schema": 1,
        "engine": ENGINE,
        "project": project.name,
        "requires_python": str(project.requires_python),
        "declarations": [item.public() for item in project.declarations],
        "scenarios": results,
        "coverage": coverage,
        "uncovered": uncovered,
        "failures": failures,
        "summary": {
            "scenarios": len(results),
            "declarations": len(project.declarations),
            "failures": len(failures),
            "uncovered": len(uncovered),
        },
    }
