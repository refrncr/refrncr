// Disposable ASGI acceptance. Never print keys, Record values or response bodies.
const {chromium} = require(process.env.REFRNCR_PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const base = 'http://127.0.0.1:5147';
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.REFRNCR_BROWSER_EXECUTABLE});
  console.log('Browser ' + browser.version());
  try {
    for (const javaScriptEnabled of [true, false]) {
      const context = await browser.newContext({javaScriptEnabled});
      const page = await context.newPage();
      await page.goto(base + '/login');
      await page.getByLabel('Email address').fill('user@example.com');
      await page.getByLabel('Password', {exact:true}).fill('correct horse battery staple');
      await page.getByRole('button', {name:'Sign in',exact:true}).click();
      await page.waitForURL(base + '/');
      assert.equal((await context.request.get(base + '/api/v1/datasets')).status(),401);
      assert.equal((await context.request.post(base + '/logout')).status(),403);
      for (const width of [320,768,1440]) {
        await page.setViewportSize({width,height:900});
        await page.goto(base + '/datasets/countries/records');
        await page.getByRole('heading', {name:'Countries',exact:true}).waitFor();
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        await page.goto(base + '/settings/api-keys');
        await page.getByRole('link',{name:'Create API key',exact:true}).click();
        const name = 'REST ' + width + ' ' + javaScriptEnabled + ' ' + Date.now();
        await page.getByLabel('Name',{exact:true}).fill(name);
        await page.getByRole('button',{name:'Create API key',exact:true}).click();
        await page.getByRole('heading',{name:'Copy your secret now'}).waitFor();
        const secret = await page.getByLabel('API key secret').inputValue();
        const headers = {Authorization:'Bearer ' + secret};
        const prefix = base + '/api/v1/datasets';
        const records = await context.request.get(prefix + '/countries/records',{headers});
        assert.equal(records.status(),200);
        const body = await records.json();
        assert.equal(body.data.length,1);
        for (const path of ['', '/countries', '/countries/records', '/countries/records/' + encodeURIComponent(body.data[0].identifier)]) {
          const response = await context.request.get(prefix + path,{headers});
          assert.equal(response.status(),200);
          assert.match(response.headers()['cache-control'],/private/);
          const cached = await context.request.get(prefix + path,{headers:{...headers,'If-None-Match':response.headers().etag}});
          assert.equal(cached.status(),304);
          assert.equal((await cached.body()).length,0);
        }
        await page.getByRole('link',{name:'Done',exact:true}).click();
        await page.waitForLoadState('load');
        assert(!(await page.content()).includes(secret));
        await page.getByRole('link',{name:'Revoke ' + name,exact:true}).click();
        await page.getByLabel('I confirm permanent revocation').check();
        await page.getByRole('button',{name:'Revoke API key',exact:true}).click();
        await page.getByText('API key permanently revoked.',{exact:true}).waitFor();
        assert.equal((await context.request.get(prefix,{headers})).status(),401);
        assert.equal((await context.request.get(base + '/static/css/base.css')).status(),200);
        console.log('PASS width=' + width + ' JavaScript=' + javaScriptEnabled + ': Flask login/Records/assets/CSRF, API endpoints/304/revoke');
      }
      await context.close();
    }
  } finally { await browser.close(); }
})().catch(() => {console.error('Browser acceptance failed; inspect the disposable fixture.');process.exitCode=1;});
