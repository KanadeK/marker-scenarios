# Third-party software

Runtime: [packaging 25.0](https://github.com/pypa/packaging), dual licensed under
BSD-2-Clause and Apache-2.0. Its installed package retains its own notices.
This repository does not vendor its source. The MIT license here covers only
this project's own code and synthetic fixtures.

Development: pytest, Ruff, build, setuptools, and wheel use MIT licensing.
The integration test invokes an installed build backend on synthetic local
metadata; that fixture is never installed and its named dependencies are not
fetched. These development tools are not runtime dependencies.
