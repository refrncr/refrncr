// Checks API-key creation, one-time secret display, lifecycle actions and safe redaction.
const {chromium} = require('./browser_policy.cjs');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const base = 'http://127.0.0.1:5140';
const output = process.env.REFRNCR_BROWSER_OUTPUT || os.tmpdir();
fs.mkdirSync(output, {recursive: true});
const runId = Date.now();
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.REFRNCR_BROWSER_EXECUTABLE});
  console.log('Browser ' + browser.version());
  try {
    for (const javaScriptEnabled of [true, false]) {
      const context = await browser.newContext({javaScriptEnabled});
      let page = await context.newPage();
      await page.goto(base + '/login');
      await page.getByLabel('Email address').fill('user@example.com');
      await page.getByLabel('Password', {exact:true}).fill('correct horse battery staple');
      await page.getByRole('button', {name:'Sign in',exact:true}).click();
      await page.waitForURL(base + '/');
      await page.goto(base + '/settings/api-keys');
      for (const width of [320, 768, 1440]) {
        await page.setViewportSize({width,height:900});
        await page.getByRole('link',{name:'Create API key',exact:true}).click();
        await page.getByRole('button',{name:'Create API key',exact:true}).click();
        await page.locator('#key-error').waitFor();
        if (javaScriptEnabled) await page.waitForFunction(() => document.activeElement.id === 'key-error');
        assert(await page.getByLabel('Name',{exact:true}).getAttribute('aria-invalid') === 'true');
        const name = 'Integration ' + width + ' ' + javaScriptEnabled + ' ' + runId;
        await page.getByLabel('Name',{exact:true}).fill(name);
        await page.waitForLoadState('load');
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        await page.getByRole('button',{name:'Create API key',exact:true}).click();
        await page.getByRole('heading',{name:'Copy your secret now'}).waitFor();
        await page.waitForLoadState('load');
        const secret = await page.getByLabel('API key secret').inputValue();
        assert(/^rfr_[0-9a-f]{32}\.[A-Za-z0-9_-]{43}$/.test(secret));
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        if (javaScriptEnabled) {
          await page.waitForFunction(() => document.activeElement.id === 'secret-warning');
          await page.getByRole('button',{name:'Copy secret',exact:true}).click();
          await page.getByRole('status').filter({hasText:/Secret copied|Copy was unavailable/}).waitFor();
        }
        await page.getByRole('link',{name:'Done',exact:true}).click();
        await page.waitForLoadState('load');
        await page.evaluate(() => document.fonts.ready);
        assert(!(await page.content()).includes(secret));
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        try {
          await page.goBack();
          await page.waitForLoadState('load');
          assert(!(await page.content()).includes(secret), 'History must not redisplay the secret');
        } catch (error) {
          if (!String(error.message).includes('ERR_CACHE_MISS')) throw error;
        }
        await page.close();
        page = await context.newPage();
        await page.setViewportSize({width,height:900});
        await page.goto(base + '/settings/api-keys');
        await page.screenshot({path:path.join(output, `ref40-${width}-${javaScriptEnabled}.png`)});
        const revoke = page.getByRole('link',{name:'Revoke ' + name,exact:true});
        await revoke.focus();
        await page.keyboard.press('Enter');
        await page.getByRole('heading',{name:'Revoke ' + name + '?'}).waitFor();
        if (javaScriptEnabled) {
          await page.waitForFunction(() => document.activeElement === document.querySelector('main h2'));
        }
        await page.getByLabel('I confirm permanent revocation').check();
        await page.getByRole('button',{name:'Revoke API key',exact:true}).click();
        await page.getByText('API key permanently revoked.',{exact:true}).waitFor();
        if (javaScriptEnabled) await page.waitForFunction(() => document.activeElement.id === 'key-status');
        assert.equal(await page.getByRole('row').filter({hasText:name}).getByText('Revoked',{exact:true}).count(),1);
        assert(!(await page.content()).includes(secret));
        console.log('PASS width=' + width + ' JavaScript=' + javaScriptEnabled + ': creation, secret, revoke, focus, overflow');
      }
      await context.close();
    }
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
