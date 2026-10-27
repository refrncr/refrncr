# Browser acceptance tests

The browser acceptance suite exercises the rendered Community application in real Chromium against disposable, loopback-only fixtures. It covers CSP and navigation, dataset creation and deletion, draft/import/review/publication flows, search, access management, APIs, data presentation, responsive layouts and accessibility interactions. Each `tests/javascript/*_browser.cjs` file states its focused journey at the top and is mapped to a fresh fixture in `tests/run_browser_tests.py`.

## Running tests locally

Install the development dependencies and the locked JavaScript tools, then install Chromium once:

```shell
python -m pip install -e ".[dev]"
npm ci --ignore-scripts --prefix tests/javascript
node tests/javascript/node_modules/playwright/cli.js install chromium
```

Run the pull-request core set, the full suite, or named cases:

```shell
python tests/run_browser_tests.py --core
python tests/run_browser_tests.py --all
python tests/run_browser_tests.py dataset_creation record_search
```

The runner starts and destroys an isolated fixture for every case. Browser scripts refuse non-loopback targets. Chromium is the supported browser for this suite; cross-browser coverage is not part of REF-310.

## CI policy

Every pull request runs the short core set. It protects CSP and navigation, the critical dataset lifecycle, access management, and the REF-294 REST installation policy. The record-search case joins this set after the responsive defect tracked in REF-315 is resolved.

The full suite can be started manually and is scheduled daily at 02:17 Europe/Amsterdam. A scheduled run skips the expensive browser job when `main` has the same commit as the previous completed scheduled run. Manual runs always execute.

An acceptance script must remain red when it exposes a confirmed product defect. Record that defect as a linked Jira bug instead of weakening the assertion or folding an unrelated product fix into a browser-maintenance change.
