const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(process.argv[2], 'utf8');
const listeners = {};
const rootListeners = {};
const root = {
  addEventListener(name, handler) { rootListeners[name] = handler; },
  querySelectorAll() { return []; },
  replaceChildren() { this.replaced = true; },
};
let config;
let logoutArguments;
const sandbox = {
  URL,
  MutationObserver: class {
    constructor() {}
    observe() {}
  },
  SwaggerUIBundle(value) {
    config = value;
    return { authActions: { logout(value) { logoutArguments = value; } } };
  },
  document: {
    getElementById() { return root; },
    addEventListener(name, handler) { listeners[name] = handler; },
    activeElement: root,
  },
  window: {
    addEventListener(name, handler) { listeners[name] = handler; },
    location: { origin: 'https://localhost' },
  },
  location: { origin: 'https://localhost' },
  console,
};

vm.runInNewContext(source, sandbox, { filename: process.argv[2] });

assert.equal(config.queryConfigEnabled, false);
assert.equal(config.persistAuthorization, false);
assert.equal(config.validatorUrl, null);
assert.equal(config.withCredentials, false);
const plugin = config.plugins[0]({
  React: { createElement() {} },
});
assert.equal(plugin.components.curl(), null);

const allowed = config.requestInterceptor({ url: '/api/openapi.json', method: 'GET' });
assert.equal(allowed.credentials, 'omit');
assert.throws(
  () => config.requestInterceptor({ url: 'https://third-party.example/api', method: 'GET' }),
  /Only local read-only API requests are supported/,
);
assert.throws(
  () => config.requestInterceptor({ url: '/api/openapi.json', method: 'POST' }),
  /Only local read-only API requests are supported/,
);

listeners.pagehide();
assert.equal(JSON.stringify(logoutArguments), '["HTTPBearer"]');
assert.equal(root.replaced, true);
console.log('PASS api-docs privacy and request boundary');
