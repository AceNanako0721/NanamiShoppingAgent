# Dependency provenance

Checked 2026-11-11 against official package metadata and upstream documentation:

- Official MCP Python SDK: **mcp 2.3.0**, stable/non-yanked; Python >=3.10. [Exact PyPI metadata](https://pypi.org/pypi/mcp/2.3.0/json), [official source](https://github.com/modelcontextprotocol/python-sdk), [SDK documentation](https://py.sdk.modelcontextprotocol.io/).
- Optional real browser execution: **Playwright 1.65.0**, Python >=3.10. [Exact PyPI metadata](https://pypi.org/pypi/playwright/1.65.0/json), [official source](https://github.com/microsoft/playwright-python), [browser documentation](https://playwright.dev/python/docs/browsers).
- Build backend installed for the locked development environment: **setuptools 84.2.1**. [Exact PyPI metadata](https://pypi.org/pypi/setuptools/84.2.1/json).

`pyproject.toml` pins the runtime SDK and optional browser/test tools. `requirements.lock` pins the complete tested development/build environment, including transitive packages. It contains public package versions only, no local editable URL, private index or machine path. A lock update requires the protocol and browser suites; it must not silently cross an SDK major version. This is a version lock, not a hash-verified software supply-chain attestation.

GitHub Actions SHAs were resolved from the official GitHub API tag refs:

- actions/checkout v4: `11d5960a326750d5838078e36cf38b85af677262`
- actions/setup-python v5: `a26af69be951a213d495a4c3e4e4022e16d87065`

CI grants only `contents: read`, disables checkout credential persistence, uses no pull_request_target, external shopping credentials, live platform tests or evidence uploads. Dependency installation and browser downloads are separate from the offline test behavior.
