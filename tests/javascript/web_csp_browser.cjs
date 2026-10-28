// Checks real browser interactions and required assets while the production CSP is enforced.
const assert = require('node:assert/strict');
const {chromium} = require('./browser_policy.cjs');
const raw = require(process.env.REFRNCR_PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.REFRNCR_BROWSER_URL;

(async () => {
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext();
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const response = await page.goto(base + '/login');
    assert.match(response.headers()['content-security-policy'], /script-src 'self'/);
    await page.getByLabel('Email address').fill('admin@example.com');
    await page.getByLabel('Password', {exact: true}).fill('correct horse battery staple');
    await page.getByRole('button', {name: 'Sign in', exact: true}).click();
    await page.waitForURL(base + '/');
    await page.goto(base + '/select-fixture');
    const country = page.getByRole('combobox', {name: 'Country', exact: true});
    await country.click();
    await page.locator('#country-search').fill('Netherlands');
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#country').inputValue(), 'NL');
    const status = page.getByRole('combobox', {name: 'Status', exact: true});
    await status.click();
    await page.keyboard.press('ArrowUp');
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#changes').innerText(), '1');
    await page.getByRole('button', {name: 'Send', exact: true}).click();
    await page.waitForURL(/select-fixture\/echo/);
    assert.deepEqual(errors, []);
  } finally {
    await browser.close(); // Asserts zero CSP violations across all pages and navigations.
  }

  // Isolated negative control: demonstrate the real browser blocks inline code/styles
  // and framing. These expected violations must not contaminate legitimate-flow checks.
  const probe = await raw.chromium.launch({executablePath: process.env.REFRNCR_BROWSER_EXECUTABLE});
  try {
    const page = await probe.newPage();
    await page.goto(base + '/login');
    const violations = [];
    await page.exposeFunction('__probeViolation', directive => violations.push(directive));
    await page.evaluate(() => {
      document.addEventListener('securitypolicyviolation', e => window.__probeViolation(e.effectiveDirective));
      const script = document.createElement('script');
      script.textContent = 'window.inlineExecuted = true';
      document.body.append(script);
      const button = document.createElement('button');
      button.setAttribute('onclick', 'window.handlerExecuted = true');
      document.body.append(button);
      button.click();
      const style = document.createElement('style');
      style.textContent = 'body { --inline-probe: blocked; }';
      document.head.append(style);
      document.body.setAttribute('style', '--attribute-probe: blocked');
    });
    await assert.doesNotReject(async () => {
      await page.waitForFunction(() => !window.inlineExecuted && !window.handlerExecuted);
    });
    await page.waitForTimeout(100);
    for (const directive of ['script-src-elem', 'script-src-attr', 'style-src-elem', 'style-src-attr']) {
      assert(violations.includes(directive), `Browser must block ${directive}`);
    }
    assert.equal(await page.evaluate(() => getComputedStyle(document.body).getPropertyValue('--inline-probe')), '');
    assert.equal(await page.evaluate(() => getComputedStyle(document.body).getPropertyValue('--attribute-probe')), '');
    const host = base + '/frame-probe';
    await page.route(host, route => route.fulfill({contentType: 'text/html', body: '<iframe src="/login"></iframe>'}));
    const frameErrors = [];
    page.on('console', message => frameErrors.push(message.text()));
    await page.goto(host);
    await page.waitForTimeout(200);
    assert(frameErrors.some(message => /frame-ancestors|X-Frame-Options/.test(message)));
    assert.equal(await page.frameLocator('iframe').getByLabel('Email address').count(), 0);
  } finally {
    await probe.close();
  }
  console.log('CSP browser: legitimate controls work; inline scripts/styles and framing are blocked');
})().catch(error => { console.error(error); process.exitCode = 1; });
