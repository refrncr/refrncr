// Shared acceptance guard: keep CSP enforced and fail on violations in every browser script.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const playwright = require(process.env.REFRNCR_PLAYWRIGHT_MODULE || 'playwright');

async function guardContext(context, violations) {
  await context.exposeBinding('__recordCspViolation', (_source, directive) => violations.push(directive));
  await context.addInitScript(() => {
    // An outgoing cross-document transition is skipped when its document is hidden
    // or the destination does not opt in (for example a JSON form response).
    // Handle only that transition's ready promise, not general page errors.
    window.addEventListener('pageswap', event => {
      event.viewTransition?.ready.catch(error => {
        if (!['AbortError', 'InvalidStateError'].includes(error.name)) throw error;
      });
    });
    document.addEventListener('securitypolicyviolation', event => {
      window.__recordCspViolation(event.effectiveDirective);
    });
  });
  context.on('page', page => page.on('console', message => {
    if (message.type() === 'error' && /content.security.policy|violat.*(?:script-src|style-src|frame-ancestors)/i.test(message.text())) {
      violations.push('CSP console error');
    }
  }));
}

module.exports.chromium = {
  async launch(options = {}) {
    const browser = await playwright.chromium.launch({
      ...options,
      executablePath: options.executablePath || process.env.REFRNCR_BROWSER_EXECUTABLE || process.env.REFRNCR_CHROMIUM,
    });
    const violations = [];
    const newContext = browser.newContext.bind(browser);
    browser.newContext = async (options = {}) => {
      assert.notEqual(options.bypassCSP, true, 'Acceptance tests must enforce CSP');
      const context = await newContext(options);
      await guardContext(context, violations);
      return context;
    };
    browser.newPage = async (options = {}) => (await browser.newContext(options)).newPage();
    const close = browser.close.bind(browser);
    browser.close = async () => {
      await close();
      assert.deepEqual(violations, [], 'Unexpected CSP violations');
    };
    return browser;
  },
};

module.exports.injectAxe = async page => {
  // Serve the optional accessibility tool as a same-origin script in the test only.
  // No CSP bypass, inline script, or production endpoint is needed.
  const url = new URL('/__browser_test_axe.js', page.url()).href;
  await page.route(url, route => route.fulfill({
    contentType: 'application/javascript', body: fs.readFileSync(process.env.REFRNCR_AXE_SOURCE),
  }));
  await page.addScriptTag({url});
};
