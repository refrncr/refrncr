// Real Chrome acceptance against browser_rest_api_app.py. Synthetic keys never printed.
const {chromium} = require('./browser_policy.cjs');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const base = 'http://127.0.0.1:5147';
const output = process.env.REFRNCR_BROWSER_OUTPUT;
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.REFRNCR_BROWSER_EXECUTABLE});
  console.log('Browser ' + browser.version());
  try {
    for (const width of [320,768,1440]) {
      const context = await browser.newContext({viewport:{width,height:900}});
      const page = await context.newPage();
      page.setDefaultTimeout(10000);
      page.setDefaultNavigationTimeout(15000);
      await page.goto(base + '/login');
      await page.getByLabel('Email address').fill('user@example.com');
      await page.getByLabel('Password',{exact:true}).fill('correct horse battery staple');
      await page.getByRole('button',{name:'Sign in',exact:true}).click();
      await page.waitForURL(base + '/');
      assert.equal((await context.request.get(base + '/api/v1/datasets')).status(),401);
      assert.equal((await context.request.post(base + '/logout')).status(),403);
      await page.goto(base + '/settings/api-keys');
      await page.getByRole('link',{name:'Create API key',exact:true}).click();
      const name = 'Documentation ' + width + ' ' + Date.now();
      await page.getByLabel('Name',{exact:true}).fill(name);
      await page.getByRole('button',{name:'Create API key',exact:true}).click();
      await page.getByRole('heading',{name:'Copy your secret now'}).waitFor();
      const secret = await page.getByLabel('API key secret').inputValue();
      await page.getByRole('link',{name:'Done',exact:true}).click();
      const errors = [];
      const requests = [];
      page.on('pageerror',()=>errors.push('pageerror'));
      page.on('console',message=>{if(message.type()==='error')errors.push(message.text())});
      page.on('request',request=>requests.push(request));
      await page.goto(base + '/api/docs');
      await page.locator('.opblock').first().waitFor();
      assert.equal(await page.title(),'REST API documentation · REFRNCR');
      assert.equal(await page.locator('main').count(),1);
      assert.equal(await page.locator('h1').count(),1);
      for (const path of [
        '/api/v1/datasets',
        '/api/v1/datasets/{dataset_key}',
        '/api/v1/datasets/{dataset_key}/records',
        '/api/v1/datasets/{dataset_key}/records/{identifier}',
      ]) {
        assert.equal(await page.locator(`.opblock [data-path="${path}"]`).count(), 1);
      }
      assert.deepEqual(errors,[]);
      assert(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth));
      const authorize = page.getByRole('button',{name:'Authorize',exact:true}).first();
      await authorize.focus();
      assert.notEqual(await authorize.evaluate(e=>getComputedStyle(e).outlineStyle),'none');
      await page.keyboard.press('Enter');
      const modal = page.getByRole('dialog',{name:'API key authorization'});
      const input = modal.getByLabel('API key',{exact:true});
      await input.waitFor();
      assert(await input.evaluate(e=>document.activeElement===e));
      assert.equal(await input.getAttribute('type'),'password');
      for (let i=0;i<8;i++) {
        await page.keyboard.press('Tab');
        assert(await modal.evaluate(e=>e.contains(document.activeElement)));
      }
      if (output) {
        fs.mkdirSync(output,{recursive:true});
        await page.screenshot({path:output + '/authorize-' + width + '.png'});
      }
      await input.fill(secret);
      assert(!(await page.content()).includes(secret));
      await modal.getByRole('button',{name:'Authorize API key',exact:true}).click();
      await page.keyboard.press('Escape');
      await modal.waitFor({state:'hidden'});
      assert(await authorize.evaluate(e=>document.activeElement===e));
      const operation = page.locator('.opblock').filter({has:page.locator('[data-path="/api/v1/datasets"]')});
      await operation.locator('.opblock-summary-control').focus();
      await page.keyboard.press('Enter');
      await operation.getByRole('button',{name:'Try it out',exact:true}).click();
      async function execute(status) {
        const [response]=await Promise.all([
          page.waitForResponse(r=>r.url().startsWith(base+'/api/v1/datasets')),
          operation.getByRole('button',{name:'Execute',exact:true}).click(),
        ]);
        assert.equal(response.status(),status);
        await operation.locator('.live-responses-table').waitFor();
        return response;
      }
      console.log('CHECK '+width+': authorized; executing');
      const response=await execute(200);
      assert.equal(response.request().headers().authorization,'Bearer '+secret);
      assert(!response.request().headers().cookie);
      assert(!(await page.content()).includes(secret));
      assert.equal(await page.locator('.curl-command').count(),0);
      assert(await operation.locator('pre').evaluateAll(es=>es.every(e=>e.tabIndex===0)));
      assert.equal(await page.getByRole('button',{name:'authorization button locked',exact:true}).count(),4);
      assert(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth));
      assert.deepEqual(errors,[]);
      if (output) await page.screenshot({path:output+'/try-it-out-'+width+'.png',fullPage:true});
      assert(!(await (await context.request.get(base+'/api/openapi.json')).text()).includes(secret));
      assert(await page.evaluate(value=> !JSON.stringify({...localStorage,...sessionStorage}).includes(value),secret));
      assert(requests.every(r=>!r.url().includes(secret) && new URL(r.url()).origin===base));
      // History keeps only paths. Leaving the page clears authorization and DOM.
      await page.goto(base+'/settings/api-keys');
      console.log('CHECK '+width+': response privacy passed; history');
      await page.goBack();
      await page.locator('.opblock').first().waitFor();
      assert(!(await page.content()).includes(secret));
      assert.equal(await page.getByRole('button',{name:'authorization button locked',exact:true}).count(),0);
      assert.equal(await page.getByRole('button',{name:'authorization button unlocked',exact:true}).count(),4);
      await page.goto(base+'/settings/api-keys');
      await page.getByRole('link',{name:'Revoke '+name,exact:true}).click();
      await page.getByLabel('I confirm permanent revocation').check();
      await page.getByRole('button',{name:'Revoke API key',exact:true}).click();
      await page.getByText('API key permanently revoked.',{exact:true}).waitFor();
      await page.goto(base+'/api/docs');
      await page.locator('.opblock').first().waitFor();
      for (const value of [secret,'invalid-test-key']) {
        await authorize.click();
        await input.fill(value);
        await modal.getByRole('button',{name:'Authorize API key',exact:true}).click();
        await modal.getByRole('button',{name:'Close',exact:true}).click();
        if (!await operation.getByRole('button',{name:'Execute',exact:true}).isVisible()) {
          if (await operation.locator('.opblock-summary-control').getAttribute('aria-expanded') === 'false')
            await operation.locator('.opblock-summary-control').click();
          await operation.getByRole('button',{name:'Try it out',exact:true}).click();
        }
        await execute(401);
        assert((await operation.innerText()).includes('invalid_api_credentials'));
        assert(!(await page.content()).includes(secret));
        await authorize.click();
        await modal.getByRole('button',{name:'Logout API key',exact:true}).click();
        await modal.getByRole('button',{name:'Close',exact:true}).click();
      }
      // Expected 401 network diagnostics are the only console errors after denied trials.
      assert(errors.every(e=>e.includes('401')));
      assert(requests.every(r=>!r.url().includes(secret) && new URL(r.url()).origin===base));
      await context.close();
      const plain=await browser.newContext({javaScriptEnabled:false,viewport:{width,height:900}});
      const fallback=await plain.newPage();
      await fallback.goto(base+'/api/docs');
      assert(await fallback.locator('noscript p').isVisible());
      assert((await fallback.locator('noscript p').innerText()).includes('Swagger UI requires JavaScript.'));
      assert(await fallback.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      await fallback.getByRole('link',{name:'OpenAPI JSON — accessible alternative'}).click();
      assert.equal(new URL(fallback.url()).pathname,'/api/openapi.json');
      assert((await fallback.locator('body').innerText()).includes('"openapi"'));
      await plain.close();
      console.log('PASS '+width+': public docs, focus/trap, Bearer Try it out, 401/revocation, privacy/history, CSP, no-JS');
    }
  } finally {await browser.close();}
})().catch(error=>{console.error('Documentation browser check failed:',error.message.replace(/rfr_[A-Za-z0-9_.-]+/g,'[redacted]'));process.exitCode=1;});
