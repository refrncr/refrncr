// REF-73 creation/maintenance matrix against the disposable browser_table_rows_app fixture.
const {chromium} = require(process.env.REFRNCR_PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const base = process.env.REFRNCR_BROWSER_URL || 'http://127.0.0.1:5144';
const output = process.env.REFRNCR_BROWSER_OUTPUT;
if (!['127.0.0.1','localhost','[::1]'].includes(new URL(base).hostname)) throw Error('Use the disposable loopback fixture');
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.REFRNCR_BROWSER_EXECUTABLE});
  if (output) fs.mkdirSync(output,{recursive:true});
  try {
    for (const kind of ['row','single','multiple']) {
      const context = await browser.newContext({reducedMotion:'reduce'});
      const page = await context.newPage();
      const errors=[]; page.on('pageerror', e=>errors.push(e.message));
      await page.goto(`${base}/login`);
      await page.getByLabel('Email address').fill(`${kind}@example.com`);
      await page.getByLabel('Password',{exact:true}).fill('correct horse battery staple');
      await page.getByRole('button',{name:'Sign in',exact:true}).click();
      await page.waitForURL(`${base}/`);
      for (const width of [1440,768,320]) {
        await page.setViewportSize({width,height:900});
        await page.goto(`${base}/datasets`);
        const opener=page.getByRole('link',{name:'+ New dataset',exact:true});
        await opener.focus(); await page.keyboard.press('Enter');
        const panel=page.locator('dialog[open]');
        await panel.locator('[data-side-panel-content]').waitFor();
        if (kind==='row') {
          await panel.getByText('Team membership is required',{exact:false}).waitFor();
          assert.equal(await panel.getByRole('button',{name:'Create dataset',exact:true}).count(),0);
          await panel.getByRole('link',{name:'Go to Teams'}).click();
          await page.getByText('You are not a member of any team.').waitFor();
          assert.equal(await page.getByRole('button',{name:'Create team',exact:true}).count(),0);
        } else {
          const selector=panel.getByLabel('Owning team',{exact:true});
          if (kind==='single') {
            assert(await selector.inputValue());
            assert.equal(await selector.locator('option').count(),1);
          } else {
            assert.equal(await selector.inputValue(),'');
            await selector.selectOption({label:'Test data domain'});
          }
          await panel.getByLabel('Key',{exact:true}).fill(`review-${kind}-${width}`);
          await panel.getByLabel('Name',{exact:true}).fill(`Review ${kind} ${width}`);
          await panel.getByRole('button',{name:'Create dataset',exact:true}).click();
          await page.waitForURL(`${base}/datasets/review-${kind}-${width}`);
          await page.getByRole('link',{name:'Edit dataset',exact:true}).click();
          await panel.getByText('Owning team: Test data domain',{exact:true}).waitFor();
          assert.equal(await panel.locator('select[name=owner_team_id]').count(),0);
          await page.keyboard.press('Escape');
          await page.getByRole('link',{name:'View records',exact:true}).click();
          await page.getByRole('button',{name:'Add record',exact:true}).waitFor();
          assert.equal(await page.locator('.export-menu').count(),1);
        }
        assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
        if(output) await page.screenshot({path:`${output}/${kind}-${width}.png`});
        // Existing universally readable Dataset: no administrator actions for any of these actors.
        await page.goto(`${base}/datasets/empty-check`);
        assert.equal(await page.getByRole('link',{name:'Access management',exact:true}).count(),0);
        assert.equal(await page.getByRole('link',{name:'Edit dataset',exact:true}).count(),kind==='row'?0:1);
        await page.getByRole('link',{name:'View records',exact:true}).click();
        assert.equal(await page.locator('.export-menu').count(),kind==='row'?0:1);
        assert.equal(await page.getByRole('button',{name:'Add record',exact:true}).count(),kind==='row'?0:1);
        console.log(`PASS: ${kind} at ${width}px: creation eligibility, persisted owner, read/maintenance controls, no overflow`);
      }
      assert.deepEqual(errors,[]);
      await context.close();
    }
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
