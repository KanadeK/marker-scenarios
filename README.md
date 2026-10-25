# marker-scenarios

Test which direct Python dependencies activate for explicit target environments
and extras. Turn expectations such as “Windows needs pywin32; Linux must not”
into repeatable assertions against a static `pyproject.toml`, without installing
dependencies or running build backends. Every declaration has a stable positional
ID and matching-scenario witnesses. Runtime works offline.

## Install and try

Requires Python 3.11 or later:

```sh
python -m pip install "git+https://github.com/KanadeK/marker-scenarios.git@v0.1.0"
git clone https://github.com/KanadeK/marker-scenarios.git
cd marker-scenarios
marker-scenarios check examples/pyproject.toml --scenarios examples/scenarios.json --fail-uncovered
```

Expected output, exit 0:

```text
3 scenarios; 0 failures; 0 declarations uncovered
linux-base: ["platformdirs", "typing-extensions"]
windows-docs: ["platformdirs", "pywin32", "sphinx"]
linux-speed: ["platformdirs", "typing-extensions", "uvloop"]
```

Install from a source checkout with `python -m pip install .`. The module entry
point is `python -m marker_scenarios`. Uninstall with
`python -m pip uninstall marker-scenarios`. Remove generated capture/report files
when no longer needed. The tool has no persistent cache.

## Write a scenario contract

Start by running this command **on each target interpreter**:

```sh
marker-scenarios capture --name linux311 --output linux311.json
```

Capture emits a complete fixture with `schema: 1`, a named `environments` object,
and a `scenarios` array. Combine captured environment entries into one file and
add scenarios such as:

```json
{
  "name": "windows-docs",
  "environment": "windows313",
  "extras": ["docs"],
  "present": ["pywin32", "sphinx"],
  "absent": ["uvloop"]
}
```

This object is one item in `scenarios`, not the whole file. See the complete
[example contract](examples/scenarios.json). Multiple scenarios can reference
the same environment. Optional `exact` asserts the complete set of activated
distribution names; `exact: []` means none. `present` and `absent` assert subsets.
Contradictory expectations, unknown extras, duplicate scenario names, unknown
fields, and duplicate JSON members are rejected.

All eleven environment fields are mandatory: `implementation_name`,
`implementation_version`, `os_name`, `platform_machine`, `platform_release`,
`platform_system`, `platform_version`, `python_full_version`,
`platform_python_implementation`, `python_version`, and `sys_platform`.
Partial overrides are rejected so omitted fields cannot fall back to the host.
`python_full_version` must include a patch level and agree with `python_version`.
Other platform fields are user-supplied facts, not inferred or independently
verified by this tool. Capture contains OS/build metadata; review it before
publishing. Example environments are clearly synthetic fixtures.

## Semantics and output

```sh
marker-scenarios check pyproject.toml --scenarios targets.json --format json
marker-scenarios check pyproject.toml --scenarios targets.json --output new-report.json --format json
```

The tool reads static `[project].dependencies`, `[project.optional-dependencies]`
and `requires-python`. It evaluates PEP 508 markers with the pinned
[`packaging` implementation](https://packaging.pypa.io/en/stable/markers.html).
Package and extra names use PyPA normalization, so `Foo_Bar` and `foo-bar` name
the same distribution/extra. Section IDs remain positional (`base[0]`,
`extra:docs[0]`); reordering declarations changes those IDs.

Base requirements are evaluated in the empty-extra context plus each requested
extra. An optional group's requirements are eligible only when that group is
selected, and their markers are evaluated in that group's context. This models
the extra guard added to wheel metadata by build backends. Requested dependency
extras, such as `requests[socks]`, are reported but not recursively expanded.

Each scenario reports active names and each declaration's reason (`matched`,
`marker_false`, `extra_not_selected`, or `requires_python_mismatch`). The
`matched_extras` array supplies witnesses; `""` denotes the base context. A
target outside `requires-python` is a failure and contributes no activation
coverage. Prerelease target versions are evaluated explicitly.

The JSON report includes `schema`, pinned `engine`, source declarations,
scenarios, per-declaration coverage, uncovered IDs, failures, and a summary.
Direct reference URLs are never printed; only a `direct_reference` boolean is
included. Invalid requirement diagnostics identify the entry without echoing
its text. Dependency names and marker literals still appear in reports.
`--output` creates a new file and refuses to overwrite an existing one.

| Exit | Meaning |
|---|---|
| 0 | Expectations pass and supported Python targets are used |
| 1 | Expectation/Requires-Python failure, or uncovered declarations with `--fail-uncovered` |
| 2 | Invalid or unsupported input, marker evaluation error, resource bound, or I/O failure |

“Uncovered” means a declaration was never activated by the supplied scenarios.
It does **not** mean an impossible marker, exhaustive platform coverage, or
coverage of every Boolean branch. Without `--fail-uncovered`, uncovered rows are
informational. Scenarios without assertions still produce reports and check
`requires-python`; add explicit expectations to make a useful regression gate.

## What this does not validate

This is an activation test, not a resolver or installer. Conflicting active
version specifiers can still pass; no compatible wheel, package existence,
transitive dependency, index, ABI, libc, or actual installation is verified.
Use [uv](https://docs.astral.sh/uv/concepts/resolution/) or another resolver for
resolution and real target CI for installation tests. The distinction between
wheel filtering and full marker environments is documented in
[pip issue 11664](https://github.com/pypa/pip/issues/11664).

Dynamic dependencies/requires-python, legacy Poetry-only metadata, `setup.py`
execution, lockfile-only `extras`/`dependency_groups` markers, dependency groups,
and build-system requirements are outside scope. Only dependency-related fields
are validated; this is not a complete pyproject validator. The pinned packaging
25.0 engine makes results reproducible, but future marker specification changes
require a deliberate engine update.

Bounds: 2 MiB per input, 1,000 declarations, 4,096 characters per requirement,
64 environments, 128 scenarios, 64 optional groups/selected extras, and 250,000
marker evaluations. Hostile input should also run under OS resource limits.

## Development

```sh
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m build
```

Tests include host-environment poisoning, every missing environment field,
expectation failures, normalization, URL suppression, unsupported/dynamic inputs,
capture/check CLI flows, and a real setuptools-built wheel whose metadata is
independently evaluated and compared with the tool's result. No dependency from
the synthetic test project is downloaded or installed. CI covers Windows/Linux
on Python 3.11/3.13.

MIT license. The only runtime package is packaging 25.0, dual licensed
BSD-2-Clause/Apache-2.0. No third-party implementation is vendored.

## 中文说明

本工具用完整、明确的目标环境和 extras 场景检查 `pyproject.toml` 中哪些直接依赖
应被激活，并支持“必须出现”“必须不出现”和“精确集合”的断言。
可用于发现平台条件、Python 版本边界或可选依赖配置的回归，无需安装待检查的依赖。

在目标解释器上运行 `capture` 获取环境，把场景和预期写入 JSON，再执行 `check`。
缺失任一环境字段会报错，不会偷偷使用当前主机值。示例包含 Linux 和 Windows
三个场景，预期全部通过。它不解析依赖版本冲突、不检查 wheel 是否存在，也不保证
实际可安装；完整兼容性仍需解析器和目标环境测试。
