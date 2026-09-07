"""Exercise the shared controller without adding a browser/runtime dependency."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_password_visibility_preserves_value_and_focuses_error_summary():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is unavailable; use the documented manual browser checks")
    script = Path(__file__).parents[1] / "src/refrncr/static/js/refrncr.js"
    result = subprocess.run(
        [
            node,
            "-e",
            r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const password = {type: 'password', value: 'entered password'};
let activate;
let focused = false;
const button = {
  hidden: true,
  textContent: 'Show password',
  getAttribute: () => 'password',
  addEventListener: (event, callback) => { if (event === 'click') activate = callback; },
};
const document = {
  querySelectorAll: (selector) => selector === '[data-password-toggle]' ? [button] : [],
  querySelector: (selector) => selector === '[data-error-focus]'
    ? {focus: () => {focused = true;}} : null,
  getElementById: () => password,
  addEventListener: () => {},
};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {document});
assert.equal(focused, true);
assert.equal(button.hidden, false);
activate();
assert.equal(password.type, 'text');
assert.equal(button.textContent, 'Hide password');
assert.equal(password.value, 'entered password');
activate();
assert.equal(password.type, 'password');
assert.equal(button.textContent, 'Show password');
assert.equal(password.value, 'entered password');
""",
            str(script),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
