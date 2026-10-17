// REF-159 real Chrome acceptance; uses the disposable source-format fixture.
const {chromium} = require(process.env.REFRNCR_PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const base = process.env.REFRNCR_BROWSER_URL || 'http://127.0.0.1:5154';
if (!['127.0.0.1','localhost','[::1]'].includes(new URL(base).hostname)) throw Error('Use a disposable loopback fixture');
(async () => {
  const browser = await chromium.launch({headless:true,executablePath:process.env.REFRNCR_BROWSER_EXECUTABLE});
  try {
    console.log('Chrome '+browser.version());
    for (const width of [320,390,768,1280,1440]) {
      for (const javaScriptEnabled of [true,false]) {
        const context = await browser.newContext({viewport:{width,height:1000},javaScriptEnabled,reducedMotion:'reduce'});
        const page = await context.newPage();
        const errors=[];
        page.on('pageerror',e=>errors.push(e.message));
        page.on('console',m=>{if(m.type()==='error') errors.push(m.text());});
        await page.goto(base+'/login');
        await page.getByLabel('Email address').fill('admin@example.com');
        await page.getByLabel('Password',{exact:true}).fill('correct horse battery staple');
        await page.getByRole('button',{name:'Sign in',exact:true}).click();
        await page.waitForURL(base+'/');
        const upload = async (content) => {
          await page.goto(base+'/datasets/imports/records/import');
          await page.getByLabel('CSV or Excel file').setInputFiles({name:'verdict.csv',mimeType:'text/csv',buffer:Buffer.from(content)});
          await page.getByRole('button',{name:'Continue',exact:true}).click();
          await page.waitForURL(/\/step\/3$/);
        };
        const row = i=>page.locator(`[data-source-formats="${i}"]`);
        const settled = ()=>page.waitForFunction(()=>!document.querySelector('[data-import-mapping]').hasAttribute('aria-busy'));
        const review = async (action) => {
          const response=page.waitForResponse(r=>r.url().endsWith('/formats') && r.status()===200);
          await action();await response;await settled();
        };
        await upload('code;amount;day;moment\nA;1.234,56;2026-02-01;01/02/2026 12:34:56\nB;2.345,67;2026-03-01;02/03/2026 12:34:56\n');
        assert.equal(await row(0).locator('[data-format-details]').count(),0);
        assert.equal(await row(1).getAttribute('data-state'),'transformed');
        assert.equal(await row(2).getAttribute('data-state'),'clean');
        assert.equal(await row(3).getAttribute('data-state'),'undecided');
        assert.match(await row(1).locator('.format-verdict').textContent(),/1.234,56 is stored as 1234.56/);
        assert.equal((await row(1).locator('.format-verdict').textContent()).match(/ is stored as /g).length,1);
        assert.match(await row(2).locator('.format-verdict').textContent(),/all 2 values parsed, stored exactly as written/);
        assert.equal(await row(2).locator('.source-format-examples').isVisible(),false);
        assert.equal(await row(3).locator('.format-reading').count(),2);
        assert.equal(await row(3).locator('select[name="source_3"]').count(),0);
        assert.equal(await row(3).locator('[data-format-details]').getAttribute('open'),'');
        assert.deepEqual(await row(3).locator('.format-reading-label').allTextContents(),[
          'Day first · DD/MM/YYYY + 24-hour, seconds · HH:mm:ss[.ffffff]',
          'Month first · MM/DD/YYYY + 24-hour, seconds · HH:mm:ss[.ffffff]'
        ]);
        assert.equal(await row(3).locator('.error-text').count(),0);
        assert.equal(await row(3).locator('[data-effective-format]').count(),1);
        assert.equal(await row(3).locator('[data-effective-format]').textContent(),'');
        assert.equal((await row(3).innerText()).match(/Choose how this column is interpreted\./g).length,1);
        const assertSelected = async () => {
          const selected = row(3).locator('.format-reading').filter({has:page.locator('input:checked')});
          const colors = await selected.evaluate(card=>{
            document.activeElement.blur();
            const style=getComputedStyle(card);
            return {border:style.borderColor,shadow:style.boxShadow,primary:style.getPropertyValue('--color-action-primary').trim(),focused:card.contains(document.activeElement)};
          });
          assert.equal(colors.focused,false);
          // Resolve the semantic token through a DOM style, rather than hardcoding its RGB value.
          const primary=await page.evaluate(value=>{
            const probe=document.createElement('span');probe.style.color=value;document.body.append(probe);
            const color=getComputedStyle(probe).color;probe.remove();return color;
          },colors.primary);
          assert.equal(colors.border,primary);
          assert.notEqual(colors.shadow,'none');
        };
        if(javaScriptEnabled) {
          const layout=await row(2).locator('.format-verdict').evaluate(verdict=>{
            const text=verdict.children[1].getBoundingClientRect(),button=verdict.querySelector('button').getBoundingClientRect();
            return {gap:parseFloat(getComputedStyle(verdict).columnGap),distance:button.left-text.right,sameLine:button.top<text.bottom,below:button.top>=text.bottom};
          });
          if(width===1280) {assert.equal(layout.sameLine,true);assert.ok(Math.abs(layout.distance-layout.gap)<1);}
          if(width===390) assert.ok(layout.below || layout.distance>=0,'Change must not overlap its sentence');
          console.log(JSON.stringify({width,verdictLayout:layout}));
        }
        const choiceBoxes=await row(3).locator('.format-reading').evaluateAll(es=>es.map(e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};}));
        if(width<=390) { assert.equal(choiceBoxes[0].x,choiceBoxes[1].x);assert.ok(choiceBoxes[1].y>choiceBoxes[0].y); }
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
        await page.screenshot({path:`/tmp/ref159-verdict-${width}-${javaScriptEnabled?'js':'nojs'}.png`,fullPage:true});
        if(javaScriptEnabled) {
          assert.equal(await page.evaluate(()=>document.activeElement.id),'source-3-0');
          await page.getByRole('button',{name:'Show all formats',exact:true}).click();
          assert.equal(await page.locator('[data-format-details][open]').count(),3);
          await page.getByRole('button',{name:'Hide all formats',exact:true}).click();
          assert.equal(await page.locator('[data-format-details][open]').count(),1);
          assert.equal(await row(3).locator('[data-format-details]').getAttribute('open'),'');
          const change=row(2).getByRole('button',{name:'Change',exact:true});
          await change.focus();await page.keyboard.press('Enter');
          assert.equal(await page.evaluate(()=>document.activeElement.id),'source-2-trigger');
          const done=row(2).getByRole('button',{name:'Done',exact:true});
          assert.equal(await done.getAttribute('aria-expanded'),'true');
          assert.equal(await done.getAttribute('aria-controls'),'source-details-2');
          await done.focus();await page.keyboard.press('Space');
          assert.equal(await change.getAttribute('aria-expanded'),'false');
          await page.getByRole('button',{name:'Back',exact:true}).click();
          await page.waitForURL(/\/step\/1$/);
          await page.getByRole('link',{name:'Continue with current file',exact:true}).click();
          await page.waitForURL(/\/step\/3$/);
          assert.equal(await row(3).getAttribute('data-state'),'undecided');
          await page.locator('[data-mapping-continue]').click();
          assert.match(page.url(),/\/step\/3$/);
          assert.equal(await page.evaluate(()=>document.activeElement.id),'source-3-0');
          await page.evaluate(()=>{window.refrncrSourceFormats();window.refrncrSourceFormats();});
          let requests=0;
          const count=r=>{if(r.url().endsWith('/formats')) requests++;};
          page.on('request',count);
          await row(3).locator('input[value="v1-datetime-dmy-slash-hms"]').focus();
          let release, reached;
          const gate=new Promise(resolve=>{release=resolve;});
          const requestReached=new Promise(resolve=>{reached=resolve;});
          const hold=async route=>{const response=await route.fetch();reached();await gate;await route.fulfill({response});};
          await page.route('**/formats',hold);
          await review(async()=>{
            await page.keyboard.press('Space');await requestReached;
            try {
              await assertSelected();
              // Restore input focus before permitting fragment replacement.
              await row(3).locator('input:checked').focus();
            } finally {release();}
          });
          await page.unroute('**/formats',hold);
          assert.equal(requests,1);
          page.off('request',count);
          assert.equal(await row(3).getAttribute('data-state'),'transformed');
          assert.equal(await page.evaluate(()=>document.activeElement.name),'source_3');
          assert.match(await page.locator('[data-format-status]').textContent(),/Stored as updated/);
          // Replaced markup binds its own toggle and keeps keyboard focus visible.
          await row(3).getByRole('button',{name:'Done',exact:true}).click();
          await row(3).getByRole('button',{name:'Change',exact:true}).click();
          assert.equal(await page.evaluate(()=>document.activeElement.id),'source-3');
          await row(2).getByRole('button',{name:'Change',exact:true}).click();
          await review(()=>page.getByLabel('Date format for day',{exact:true}).locator('xpath=self::select | following-sibling::select[1]').selectOption('v1-date-dmy-slash'));
          assert.equal(await row(2).getAttribute('data-state'),'failing');
          assert.match(await row(2).locator('.format-verdict').textContent(),/2 of 2 values do not fit DD\/MM\/YYYY/);
          await page.getByRole('button',{name:'Show all formats',exact:true}).click();
          await page.getByRole('button',{name:'Hide all formats',exact:true}).click();
          assert.equal(await row(2).locator('[data-format-details]').getAttribute('open'),'');
          assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
          await page.screenshot({path:`/tmp/ref159-failing-${width}.png`,fullPage:true});
          await review(()=>page.getByLabel('Date format for day',{exact:true}).locator('xpath=self::select | following-sibling::select[1]').selectOption('v1-date-ymd-dash'));
        } else {
          assert.equal(await row(2).locator('[data-format-toggle]').isVisible(),false);
          const summary=row(2).locator('[data-format-details] > summary');
          assert.equal(await summary.isVisible(),true);
          await summary.focus();await page.keyboard.press('Enter');
          assert.equal(await page.getByLabel('Source type for day',{exact:true}).isVisible(),true);
          await summary.focus();await page.keyboard.press('Enter');
          await row(3).locator('input[value="v1-datetime-dmy-slash-hms"]').focus();
          await page.keyboard.press('Space');
          await assertSelected();
        }
        await page.locator('[data-mapping-continue]').focus();await page.keyboard.press('Enter');
        await page.waitForURL(/\/step\/4$/);
        await page.getByText('2026-02-01T12:34:56',{exact:true}).waitFor();
        await page.getByRole('link',{name:'Back',exact:true}).click();
        await page.waitForURL(/\/step\/3$/);
        await page.reload();
        assert.equal(await row(3).getAttribute('data-state'),'transformed');
        assert.equal(await page.getByLabel('Date format for moment',{exact:true}).inputValue(),'v1-date-dmy-slash');
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
        if (!javaScriptEnabled) {
          await row(2).locator('[data-format-details] > summary').click();
          await page.getByLabel('Date format for day',{exact:true}).locator('xpath=self::select | following-sibling::select[1]').selectOption('v1-date-dmy-slash');
          await page.locator('[data-mapping-continue]').click();
          await page.waitForURL(/\/step\/4$/);
          assert.equal(await page.getByRole('button',{name:'View error report',exact:true}).isVisible(),true);
          await page.getByRole('link',{name:'Back',exact:true}).click();
          await page.waitForURL(/\/step\/3$/);
          assert.equal(await row(2).getAttribute('data-state'),'failing');
          assert.match(await row(2).locator('.format-verdict').textContent(),/2 of 2 values do not fit DD\/MM\/YYYY/);
          assert.equal(await page.getByLabel('Date format for day',{exact:true}).isVisible(),true);
          assert.equal(await row(2).locator('[data-format-details] > summary').isVisible(),true);
          assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
        }
        assert.deepEqual(errors,[]);
        console.log(JSON.stringify({width,javaScriptEnabled,choiceBoxes,overflow:false,errors}));
        await page.getByRole('button',{name:'Cancel',exact:true}).click();
        await context.close();
      }
    }
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
