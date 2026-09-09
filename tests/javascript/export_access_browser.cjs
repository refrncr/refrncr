// Optional browser acceptance: start tests/browser_table_rows_app.py with fresh fixture data.
const {chromium} = require(process.env.REFRNCR_PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const base = process.env.REFRNCR_BROWSER_URL || 'http://127.0.0.1:5144';
const output = process.env.REFRNCR_BROWSER_OUTPUT;
if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname)) throw Error('Use the disposable loopback fixture');
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.REFRNCR_BROWSER_EXECUTABLE});
  const context = await browser.newContext({viewport:{width:1440,height:900}, reducedMotion:'reduce'});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('dialog', async d => { errors.push('Unexpected script dialog'); await d.dismiss(); });
  if (output) fs.mkdirSync(output, {recursive:true});
  const shot = async name => { if (output) await page.screenshot({path:`${output}/${name}.png`}); };
  const panel = () => page.locator('dialog[open]');
  const trigger = () => page.getByRole('link', {name:'Access management', exact:true});
  const select = () => panel().getByLabel('User or team', {exact:true});
  const grant = () => panel().getByRole('button', {name:'Grant access', exact:true});
  const open = async () => {
    await trigger().focus(); await page.keyboard.press('Enter'); await select().waitFor();
    await page.evaluate(() => document.fonts.ready);
  };
  const close = async (action) => {
    await panel().evaluate(e => { e.dataset.testClosed='pending'; e.addEventListener('close', () => {e.dataset.testClosed='done';}, {once:true}); });
    await action();
    await page.waitForFunction(() => document.querySelector('dialog[data-test-closed=done]') && !document.querySelector('dialog[open]'));
    assert(await trigger().evaluate(e => e === document.activeElement));
  };
  const bounds = async () => {
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    assert(await panel().evaluate(e => e.scrollWidth <= e.clientWidth));
    assert(await panel().locator('.side-panel-body').evaluate(e => e.scrollWidth <= e.clientWidth));
  };
  const status = async text => {
    await panel().getByRole('status').filter({hasText:text}).waitFor();
    await page.waitForFunction(text => {const e=document.querySelector('dialog[open] [role=status]'); return e?.textContent===text && e===document.activeElement;}, text);
    assert.equal(page.url(), `${base}/datasets/row-check`);
  };
  try {
    await page.goto(`${base}/login`);
    await page.getByLabel('Email address').fill('admin@example.com');
    await page.getByLabel('Password',{exact:true}).fill('correct horse battery staple');
    await page.getByRole('button',{name:'Sign in',exact:true}).click();
    await page.waitForURL(`${base}/`);
    await page.goto(`${base}/datasets/row-check/owner`);
    const longTeam = await page.getByLabel('Owning team',{exact:true}).locator('option').evaluateAll(options => options.find(o => o.textContent.includes('<script>')).textContent);
    await page.getByLabel('Owning team',{exact:true}).selectOption({label:longTeam});
    await page.getByRole('button',{name:'Save owning team',exact:true}).click();
    await page.waitForURL(`${base}/datasets/row-check`);
    for (const width of [1440,768,320]) {
      await page.setViewportSize({width,height:900});
      await page.goto(`${base}/datasets/row-check`); await open();
      assert(await grant().isDisabled());
      assert.equal(await select().evaluate(e => getComputedStyle(e.labels[0]).textTransform), 'none');
      await panel().getByRole('heading',{name:'Access management',level:2,exact:true}).waitFor();
      await panel().getByRole('heading',{name:'Grant export access',level:3,exact:true}).waitFor();
      await panel().getByRole('heading',{name:'Additional export access',level:3,exact:true}).waitFor();
      await panel().getByRole('heading',{name:'No additional access',level:4,exact:true}).waitFor();
      assert((await panel().locator('.panel-sections > p').first().textContent()).includes(longTeam));
      assert.equal(await panel().locator('script, img').count(),0);
      assert.equal(await panel().locator('.button-primary').count(),1);
      assert(await panel().evaluate(e => {
        const body=e.querySelector('.side-panel-body'), intro=body.firstElementChild;
        return Math.abs(intro.getBoundingClientRect().top-e.querySelector('.side-panel-header').getBoundingClientRect().bottom-parseFloat(getComputedStyle(body).paddingTop))<2;
      }));
      const inputBox = await select().boundingBox(), buttonBox = await grant().boundingBox();
      if (width === 320) assert(buttonBox.y >= inputBox.y+inputBox.height+8);
      else assert(Math.abs(inputBox.y-buttonBox.y)<2 && buttonBox.x>=inputBox.x+inputBox.width+8);
      await bounds(); await shot(`empty-${width}`);
      // Native keyboard selection enables the action; keyboard submission is exercised below.
      await select().focus(); await page.keyboard.press('r'); await page.keyboard.press('Tab');
      assert(!(await grant().isDisabled()));
      for (let i=0;i<8;i++) { await page.keyboard.press('Tab'); assert(await page.evaluate(() => !document.hasFocus() || !!document.activeElement.closest('dialog[open]'))); }
      await close(() => page.keyboard.press('Escape'));
      await open();
      await select().selectOption({label:'Row user (row@example.com)'});
      await grant().focus(); await page.keyboard.press('Enter'); await status('Export access granted.');
      assert.equal(await panel().locator('.access-list-row').count(),1);
      assert.equal(await panel().locator('.access-list-type').textContent(),'User');
      await bounds(); await shot(`user-${width}`);
      // Duplicate rejection keeps the selected value, field error and error-summary focus.
      await select().selectOption({label:'Row user (row@example.com)'}); await grant().click();
      await panel().locator('[data-error-focus]').waitFor();
      assert(await panel().locator('[data-error-focus]').evaluate(e => e === document.activeElement));
      assert.equal(await select().getAttribute('aria-invalid'),'true');
      assert.equal(await select().getAttribute('aria-describedby'),'export-subject-error');
      assert((await panel().locator('#export-subject-error').textContent()).includes('already has export access'));
      await bounds(); await shot(`validation-${width}`);
      await select().selectOption({label:'Row team'}); await grant().click(); await status('Export access granted.');
      assert.deepEqual(await panel().locator('.access-list-type').allTextContents(),['User','Team']);
      await panel().getByRole('button',{name:'Remove export access for Team Row team',exact:true}).waitFor();
      assert.equal(await panel().locator('.access-list-subject svg[aria-hidden=true][focusable=false]').count(),2);
      await bounds(); await shot(`populated-${width}`);
      const longUserLabel = await select().locator('option').evaluateAll(options => options.find(o => o.textContent.includes('long@example.com')).textContent);
      const longUser = longUserLabel.replace(' (long@example.com)','');
      await select().selectOption({label:longUserLabel}); await grant().click(); await status('Export access granted.');
      await bounds(); await shot(`long-names-${width}`);
      for (const name of [longUser, 'Row user', 'Team Row team']) {
        await panel().getByRole('button',{name:`Remove export access for ${name}`,exact:true}).click();
        await status('Export access removed.');
      }
      await panel().getByRole('heading',{name:'No additional access',exact:true}).waitFor();
      await panel().getByRole('link',{name:'Close',exact:true}).focus(); await close(() => page.keyboard.press('Enter'));
      assert.equal(await panel().count(),0); assert(await trigger().evaluate(e => e === document.activeElement));
      await open(); await close(() => panel().getByRole('button',{name:'Close panel',exact:true}).click());
      assert(await trigger().evaluate(e => e === document.activeElement));
      console.log(`PASS: ${width}px empty/user/team/long/error states; keyboard, disabled, semantics, context and focus restoration`);
    }
    // 200% zoom equivalent CSS viewport, then increased text size at mobile width.
    await page.setViewportSize({width:720,height:450}); await open(); await bounds(); await shot('zoom-200');
    await close(() => page.keyboard.press('Escape')); await page.setViewportSize({width:320,height:900});
    await page.evaluate(() => { document.documentElement.style.fontSize='200%'; });
    await open(); await bounds(); await shot('text-200-320');
    await page.keyboard.press('Escape');
    const plain = await browser.newContext({javaScriptEnabled:false,storageState:await context.storageState()});
    const fallback = await plain.newPage(); await fallback.goto(`${base}/datasets/row-check/export-access`);
    assert(!(await fallback.getByRole('button',{name:'Grant access',exact:true}).isDisabled()));
    await fallback.getByRole('button',{name:'Grant access',exact:true}).click();
    await fallback.locator('#export-subject-error').waitFor();
    await plain.close();
    assert.equal(errors.length,0,errors.join('\n'));
    console.log('PASS: zoom-equivalent viewport, 200% text, no-JS server validation, no script errors');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode=1; });
