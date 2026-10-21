// Optional real-browser acceptance checks. Start tests/browser_table_rows_app.py first.
// Reuses the project's existing external-Playwright convention; no dependency is added.
const {chromium} = require(process.env.REFRNCR_PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const base = process.env.REFRNCR_BROWSER_URL || 'http://127.0.0.1:5144';
const output = process.env.REFRNCR_BROWSER_OUTPUT;
if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname)) throw Error('Use the disposable loopback fixture');
if (output) fs.mkdirSync(output, {recursive:true});
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.REFRNCR_BROWSER_EXECUTABLE});
  const context = await browser.newContext({viewport:{width:1440,height:900}, permissions:['clipboard-read','clipboard-write']});
  const page = await context.newPage();
  const errors = [], checks = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', async dialog => { errors.push(`Unexpected ${dialog.type()}: ${dialog.message()}`); await dialog.dismiss(); });
  const record = message => { checks.push(message); console.log(`PASS: ${message}`); };
  const shot = async name => { if (output) await page.screenshot({path:`${output}/${name}.png`,fullPage:false}); };
  const emptySelection = () => page.evaluate(() => getSelection().removeAllRanges());
  const overflow = async () => assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `page overflow at ${page.viewportSize().width}`);
  const openedPanel = () => page.locator('dialog[data-modal-side-panel][open]');
  const closePanel = async (primary, viaPointer = false) => {
    // Native dialog.close() queues its close event after removing [open].
    // Wait for the shared controller's completed restoration before reopening.
    const dialog = openedPanel();
    await dialog.evaluate(node => {
      node.dataset.testClosed = 'pending';
      node.addEventListener('close', () => { node.dataset.testClosed = 'done'; }, {once:true});
    });
    if (viaPointer) await dialog.locator('[data-dialog-close]').click();
    else await page.keyboard.press('Escape');
    assert.equal(await openedPanel().count(), 0);
    await page.waitForFunction(() => !!document.querySelector('dialog[data-test-closed="done"]'));
    assert(await primary.evaluate(e => e === document.activeElement), 'focus returns to the primary button');
    const row = primary.locator('xpath=ancestor::tr[1]');
    assert.equal(await row.evaluate(e => getComputedStyle(e).outlineWidth), '0px');
    const edgeShadows = await row.locator('td').evaluateAll(cells => [cells[0], cells.at(-1)].map(cell => getComputedStyle(cell).boxShadow));
    assert.equal(edgeShadows.every(shadow => shadow !== 'none'), !viaPointer, 'focus ring reaches both edges without a pointer ring');
    if (!viaPointer) {
      assert.equal(edgeShadows.every(shadow => shadow.split(',').length === 3), true, 'focus ring wins over status stripes on the first cell');
    }
    assert.equal(await primary.evaluate(e => getComputedStyle(e).outlineWidth), '0px', 'focus ring is drawn once, on the row');
    await page.locator('dialog[data-test-closed="done"]').evaluateAll(nodes => nodes.forEach(node => delete node.dataset.testClosed));
  };
  const dragText = async locator => {
    await locator.scrollIntoViewIfNeeded();
    const box = await locator.boundingBox();
    await page.mouse.move(box.x+2, box.y+box.height/2);
    await page.mouse.down(); await page.mouse.move(box.x+55,box.y+box.height/2,{steps:12}); await page.mouse.up();
    assert(await page.evaluate(() => getSelection().toString().length > 0), 'actual pointer drag selects text');
  };
  try {
    await page.goto(`${base}/login`);
    await page.getByLabel('Email address').fill('admin@example.com');
    await page.getByLabel('Password',{exact:true}).fill('correct horse battery staple');
    await page.getByRole('button',{name:'Sign in',exact:true}).click();
    await page.waitForURL(`${base}/`);
    const cases = [
      // Datasets gained a Version column (REF-165); rows no longer carry a chevron.
      {path:'/datasets',kind:'link',columns:5}, {path:'/teams',kind:'link',columns:3},
      {path:'/users',kind:'button',columns:5}, {path:'/datasets/row-check/records',kind:'button',columns:3},
    ];
    for (const item of cases) {
      for (const width of [1440,768,320]) {
        await page.setViewportSize({width,height:900}); await page.goto(base+item.path); await page.evaluate(() => document.fonts.ready);
        await overflow();
        const rows = page.locator('tbody [data-row-action]');
        assert(await rows.count() > 1);
        assert.equal(await page.locator('thead th').count(),item.columns);
        assert.equal(await page.getByRole('columnheader',{name:'Actions',exact:true}).count(),0);
        // Rows carry no caret: hover, underline and focus convey that they are clickable.
        const layout = await rows.evaluateAll(nodes => nodes.map(row => ({
          tag: row.querySelector('[data-row-primary]').tagName,
          primary: row.querySelectorAll('[data-row-primary]').length,
          tab: row.hasAttribute('tabindex'), role: row.getAttribute('role'),
          chevrons: row.querySelectorAll('.row-chevron').length,
        })));
        for (const r of layout) {
          assert.equal(r.primary,1); assert.equal(r.tag,item.kind==='link'?'A':'BUTTON');
          assert.equal(r.tab,false); assert.notEqual(r.role,'button');
          assert.equal(r.chevrons,0,'rows no longer render a chevron');
        }
        const primary=rows.first().locator('[data-row-primary]');
        await rows.first().hover();
        assert.equal(await rows.first().locator('.row-action-label').evaluate(e=>getComputedStyle(e).textDecorationLine),'underline','hover is not conveyed by colour alone');
        const activeCell=await rows.first().locator('td').first().boundingBox();
        await page.mouse.move(activeCell.x+4,activeCell.y+4); await page.mouse.down();
        assert.equal(await rows.first().locator('.row-action-label').evaluate(e=>getComputedStyle(e).textDecorationStyle),'double','active state uses double underline');
        await page.mouse.move(0,0); await page.mouse.up(); await emptySelection();
        // Datasets rows carry their own extra controls (Concept badge, configuration
        // link); elsewhere the row's primary control is the only tab stop in the row.
        if (item.path !== '/datasets') {
          await primary.focus(); await page.keyboard.press('Tab');
          assert(await rows.nth(1).locator('[data-row-primary]').evaluate(e=>e===document.activeElement),`one primary tab stop per row (${item.path} @${width})`);
          await page.keyboard.press('Shift+Tab');
          assert(await primary.evaluate(e=>e===document.activeElement));
        }
        await primary.focus();
        assert.equal(await rows.first().evaluate(e=>getComputedStyle(e).outlineWidth),'0px');
        assert.notEqual(await rows.first().locator('td').first().evaluate(e=>getComputedStyle(e).boxShadow),'none');
        await shot(`${item.path.replaceAll('/','-')}-${width}-focus`);
        if(item.kind==='button') {
          for(const key of ['Enter','Space']) {await page.keyboard.press(key); assert.equal(await openedPanel().count(),1); await overflow(); await closePanel(primary);}
        } else {
          const href=await primary.getAttribute('href'); await page.keyboard.press('Enter'); await page.waitForURL(base+href); await page.goBack();
        }
        record(`${item.path} at ${width}px: semantics, columns, chevron, focus, keyboard, no page overflow`);
      }
      await page.setViewportSize({width:1440,height:900}); await page.goto(base+item.path);
      let row=page.locator('[data-row-action]').first(), primary=row.locator('[data-row-primary]');
      await dragText(primary.locator('.row-action-label'));
      assert.equal(page.url(),base+item.path); assert.equal(await openedPanel().count(),0);
      await page.keyboard.press('ControlOrMeta+C');
      const selection=await page.evaluate(()=>getSelection().toString());
      assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),selection,'selected text can be copied');
      await emptySelection();
      const destination=await primary.getAttribute('href');
      const cell=row.locator('td').last(); await cell.click({position:{x:8,y:8}});
      if(item.kind==='link') {await page.waitForURL(base+destination); await page.goBack();}
      else {assert.equal(await openedPanel().count(),1); await closePanel(primary);}
      if(item.kind==='link') {
        await page.goto(base+item.path); row=page.locator('[data-row-action]').first(); primary=row.locator('[data-row-primary]');
        const href=await primary.getAttribute('href');
        for(const click of [{modifiers:['ControlOrMeta']},{button:'middle'}]) {
          const [popup]=await Promise.all([context.waitForEvent('page'),primary.click(click)]);
          await popup.waitForLoadState(); assert.equal(popup.url(),base+href); await popup.close();
          assert.equal(page.url(),base+item.path);
        }
        await page.evaluate(()=>document.addEventListener('contextmenu',e=>{window.rowContext={prevented:e.defaultPrevented,href:e.target.closest('a')?.href};},{once:true}));
        await primary.click({button:'right'});
        assert.deepEqual(await page.evaluate(()=>window.rowContext),{prevented:false,href:base+href});
        await page.keyboard.press('Escape');
        const nojs=await browser.newContext({javaScriptEnabled:false,storageState:await context.storageState()});
        const fallback=await nojs.newPage(); await fallback.goto(base+item.path); await fallback.locator('[data-row-primary]').first().click();
        assert.equal(fallback.url(),base+href); await nojs.close();
      }
      if (item.kind === 'button') {
        await primary.click();
        await closePanel(primary, true);
      }
      record(`${item.path}: pointer row activation, primary text selection/copy${item.kind==='link'?', native new tabs/contextmenu event and no-JS link':''}`);
    }
    await page.goto(base+'/datasets/row-check/records');
    let row=page.locator('[data-row-action]').first(), primary=row.locator('[data-row-primary]');
    await dragText(row.locator('td').first().locator('.cell-content'));
    assert.equal(await openedPanel().count(),0); await emptySelection();
    // Add representative nested controls to an actual rendered row, leaving production handlers intact.
    await row.locator('td').first().evaluate(td=>{
      td.innerHTML='<a href="#nested">Nested link</a><button type="button">Nested button</button><label><input type="checkbox">Nested checkbox</label><input aria-label="Nested input"><select aria-label="Nested select"><option>A</option><option>B</option></select><details><summary>Nested menu</summary><button type="button">Menu action</button></details><span contenteditable="true">Editable text</span><span role="button" tabindex="0">Custom action</span><span data-handled>Handled event</span>';
      td.querySelector('[data-handled]').addEventListener('click',e=>e.preventDefault());
    });
    for(const selector of ['a','button','label','input[aria-label]','select','summary','[contenteditable]','[role="button"]','[data-handled]']) {
      await row.locator(selector).first().click(); assert.equal(await openedPanel().count(),0,selector+' must not activate the row');
    }
    assert(await row.locator('input[type="checkbox"]').isChecked());
    await row.locator('input[aria-label]').fill('Typed value');
    await row.locator('select').selectOption('B');
    await row.locator('details button').click(); assert.equal(await openedPanel().count(),0);
    for(const modifier of ['Control','Meta','Shift','Alt']) {await row.locator('td').last().click({modifiers:[modifier],position:{x:8,y:8}}); assert.equal(await openedPanel().count(),0);}
    await row.evaluate(e=>{e.classList.add('is-selected');e.style.backgroundColor='var(--color-warning-surface)';});
    const stateBefore=await row.evaluate(e=>({background:getComputedStyle(e).backgroundColor,marker:getComputedStyle(e).boxShadow}));
    await row.hover();
    assert.deepEqual(await row.evaluate(e=>({background:getComputedStyle(e).backgroundColor,marker:getComputedStyle(e).boxShadow})),stateBefore,'hover preserves warning background and selected marker');
    await primary.evaluate(e=>{e.disabled=true;});
    await row.locator('td').last().click({position:{x:8,y:8}}); assert.equal(await openedPanel().count(),0,'disabled primary cannot be activated from row space');
    await primary.evaluate(e=>{e.disabled=false;});
    await primary.click(); assert.equal(await openedPanel().count(),1); await closePanel(primary);
    await page.getByRole('link',{name:'Next',exact:true}).click(); assert.equal(await page.locator('[data-row-action]').count(),2);
    await page.getByRole('link',{name:'Previous',exact:true}).click(); assert.equal(await page.locator('[data-row-action]').count(),50);
    record('Nested native/custom controls, handled events, selection in secondary cells, modifiers and pagination');
    await page.goto(base+'/datasets/row-check/concept/records');
    const changedRow = page.locator('.record-row:has(> .record-system-status .badge-draft)').first();
    assert.equal(await changedRow.count(), 1, 'fixture includes a changed record with a status stripe');
    const changedPrimary = changedRow.locator('[data-row-primary]');
    await changedPrimary.focus(); await page.keyboard.press('Enter'); await page.keyboard.press('Escape');
    await page.waitForFunction(() => document.activeElement === document.querySelector('.record-row:has(> .record-system-status .badge-draft) [data-row-primary]'));
    let firstCellShadow = await changedRow.locator('td').first().evaluate(cell => getComputedStyle(cell).boxShadow);
    assert.equal(firstCellShadow.split('inset').length - 1, 3, 'keyboard ring replaces the changed-row stripe and covers the first cell edges');
    await changedPrimary.click();
    await page.locator('dialog[data-modal-side-panel][open] .side-panel-close').click();
    await page.waitForFunction(() => document.activeElement === document.querySelector('.record-row:has(> .record-system-status .badge-draft) [data-row-primary]'));
    firstCellShadow = await changedRow.locator('td').first().evaluate(cell => getComputedStyle(cell).boxShadow);
    assert.equal(firstCellShadow.split('inset').length - 1, 1, 'pointer close restores focus without the keyboard ring');
    assert(!firstCellShadow.includes('0px 3px') && !firstCellShadow.includes('0px -3px'));
    record('Changed-row status stripe yields to keyboard ring and remains without ring after pointer close');
    await page.goto(base+'/datasets/empty-check/records'); assert.equal(await page.locator('[data-row-action]').count(),0);
    await page.getByRole('heading',{name:'No records yet'}).click(); assert.equal(await openedPanel().count(),0);
    await page.goto(base+'/datasets/row-check'); assert.equal(await page.locator('[data-row-action]').count(),0);
    assert.equal(await page.getByRole('columnheader',{name:'Actions',exact:true}).count(),1);
    record('Empty records and configuration excluded; legitimate Actions column preserved');
    await page.goto(base+'/datasets/row-check/concept/records');
    const identifier = await page.locator('.record-row:has(> .record-system-status .badge-draft) [data-row-primary]').first().getAttribute('aria-label');
    const recordId = identifier.match(/\(([^)]+)\)$/)[1];
    for (const mode of ['pointer', 'keyboard']) {
      await page.goto(`${base}/datasets/row-check/concept/records`);
      const primary = page.locator(`#record-row-${recordId} [data-row-primary]`);
      await primary.click();
      const editForm = page.locator(`form[data-focus-return-record-id='${recordId}']`);
      await editForm.locator("input[name='description']").fill(`Edited from panel by ${mode}`);
      if (mode === 'pointer') await editForm.getByRole('button', {name:'Save record'}).click();
      else await editForm.locator("input[name='description']").press('Enter');
      await page.waitForURL(url => url.pathname === '/datasets/row-check/concept/records');
      const restored = page.locator(`#record-row-${recordId}`);
      await page.waitForFunction(id => document.activeElement === document.querySelector(`#record-row-${id} [data-row-primary]`), recordId);
      assert.equal(await restored.locator('[data-row-primary]').getAttribute('data-keyboard-restored-focus') !== null, mode === 'keyboard');
      const firstCellShadow = await restored.locator('td').first().evaluate(cell => getComputedStyle(cell).boxShadow);
      assert.equal(firstCellShadow.includes('0px 3px'), mode === 'keyboard', `${mode} save focus ring at the first cell`);
      assert.equal(firstCellShadow.includes('0px -3px'), mode === 'keyboard', `${mode} save focus ring at the first cell`);
    }
    record('JavaScript panel Save returns focus with a ring only after keyboard submission');
    assert.deepEqual(errors,[]);
    if(output) fs.writeFileSync(`${output}/results.json`,JSON.stringify({browser:browser.version(),checks,errors},null,2));
    console.log(`PASS: ${checks.length} browser scenario groups; no JavaScript errors`);
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
