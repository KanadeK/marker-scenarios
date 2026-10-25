Initial release: explicit PEP 508 environment matrices, optional-extra activation,
present/absent/exact-name contracts, per-declaration witnesses, uncovered-entry
reporting, and a local environment capture command.

49 tests passed locally on Windows/Python 3.13, including a real wheel build
whose Requires-Dist metadata supplies an independent oracle for optional-group
semantics. Also covers all missing environment fields, host-environment poisoning,
URL suppression, normalization, unsupported input and CLI workflows.
CI covers Windows/Linux and Python 3.11/3.13.

Version 0.1.0 reads static PEP 621 dependency metadata and uses packaging 25.0.
It does not resolve packages, validate transitive dependencies or guarantee
installability. Coverage is finite-scenario declaration activation, not proof of
marker impossibility or exhaustive branch coverage. See README for limits.
