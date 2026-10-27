// REF-158 real Chrome regression. Start tests/browser_source_formats_app.py first.
const {chromium} = require('./browser_policy.cjs');
const assert = require('node:assert/strict');
const {spawnSync} = require('node:child_process');
const nativeWorkbook = spawnSync(process.env.REFRNCR_PYTHON || '.venv/bin/python', ['-c', `
import io, sys
from datetime import datetime, time
from openpyxl import Workbook
book = Workbook()
book.active.append(['code', 'amount', 'day', 'clock', 'moment'])
book.active.append(['Native', 1, datetime(2024, 2, 29), time(12, 34, 56), datetime(2024, 2, 29, 12, 34, 56)])
output = io.BytesIO()
book.save(output)
book.close()
sys.stdout.buffer.write(output.getvalue())
`]);
assert.equal(nativeWorkbook.status,0,nativeWorkbook.stderr.toString());
const base = process.env.REFRNCR_BROWSER_URL || 'http://127.0.0.1:5154';
if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname)) throw Error('Use the disposable loopback fixture');
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.REFRNCR_BROWSER_EXECUTABLE});
  try {
    console.log('Chrome ' + browser.version());
    for (const width of [320,768,1440]) {
      const context = await browser.newContext({viewport:{width,height:1000},reducedMotion:'reduce'});
      const page = await context.newPage();
      page.setDefaultTimeout(15000);
      page.setDefaultNavigationTimeout(15000);
      const errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      page.on('console',msg=>{if(msg.type()==='error') errors.push(msg.text());});
      await page.goto(base+'/login');
      await page.getByLabel('Email address').fill('admin@example.com');
      await page.getByLabel('Password',{exact:true}).fill('correct horse battery staple');
      await page.getByRole('button',{name:'Sign in',exact:true}).click();
      await page.waitForURL(base+'/');
      const upload = async (content) => {
        await page.goto(base+'/datasets/imports/records/import');
        await page.getByLabel('CSV or Excel file').setInputFiles({name:'source.csv',mimeType:'text/csv',buffer:Buffer.from(content)});
        await page.getByRole('button',{name:'Continue',exact:true}).click();
        await page.waitForURL(/\/step\/3$/);
      };
      await upload('code;unused\nA;12.50\n');
      assert.equal(await page.locator('[data-format-details]').count(),0);
      assert.equal(await page.locator('[data-advanced-formats]').isVisible(),false,'No inert Show all formats button');
      const mappedReview=page.waitForResponse(r=>r.url().endsWith('/formats') && r.status()===200);
      await page.getByLabel('Target field for unused',{exact:true}).locator('xpath=self::select | following-sibling::select[1]').selectOption('amount');
      await mappedReview;
      await page.waitForFunction(()=>!document.querySelector('[data-import-mapping]').hasAttribute('aria-busy'));
      assert.equal(await page.locator('[data-advanced-formats]').isVisible(),true);
      await page.getByRole('button',{name:'Show all formats',exact:true}).click();
      assert.equal(await page.locator('[data-format-details][open]').count(),1);
      assert.equal(await page.getByLabel('Choose source format for amount',{exact:true}).isVisible(),true);
      const ignoredReview=page.waitForResponse(r=>r.url().endsWith('/formats') && r.status()===200);
      await page.getByLabel('Target field for unused',{exact:true}).locator('xpath=self::select | following-sibling::select[1]').selectOption('__ignored__');
      await ignoredReview;
      await page.waitForFunction(()=>!document.querySelector('[data-import-mapping]').hasAttribute('aria-busy'));
      assert.equal(await page.locator('[data-advanced-formats]').isVisible(),false);
      await page.getByRole('button',{name:'Cancel import',exact:true}).click();
      await upload('code;amount;day\nA;1.234,56;2024-02-29\nB;2.345,67;2024-03-01\n');
      // The aggregate action controls the native format disclosures.
      const advanced=page.locator('[data-advanced-formats]');
      assert.equal(await page.locator('[data-format-details][open]').count(),0);
      await advanced.focus();
      await page.keyboard.press('Space');
      assert.equal(await page.locator('[data-format-details][open]').count(),2);
      assert.equal(await advanced.getAttribute('aria-expanded'),'true');
      assert.equal(await advanced.textContent(),'Hide all formats');
      await advanced.click();
      assert.equal(await page.locator('[data-format-details][open]').count(),0);
      const change=page.locator('[data-source-formats="1"] [data-format-toggle]');
      await change.focus();
      await page.keyboard.press('Enter');
      assert.equal(await page.locator('[data-format-details][open]').count(),1);
      const select=page.getByLabel('Choose source format for amount',{exact:true});
      assert.equal(await select.evaluate(e=>document.activeElement===e),true);
      assert.equal(await select.locator('xpath=self::select | following-sibling::select[1]').inputValue(),'v1-eu');
      const pairs=page.locator('[data-source-formats="1"] .source-format-pair');
      assert.deepEqual(await pairs.locator('code').allTextContents(),['1.234,56','1234.56','2.345,67','2345.67']);
      assert.equal(await pairs.first().isVisible(),true,'Individual disclosure shows its examples');
      assert.equal(await page.locator('[data-import-mapping]').getAttribute('data-format-mode'),null);
      const boxes=await pairs.first().locator(':scope > div').evaluateAll(es=>es.map(e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width};}));
      if(width===320) {
        assert.ok(boxes[1].y>boxes[0].y && boxes[1].x===boxes[0].x);
        assert.ok((await pairs.first().locator('span').first().boundingBox()).height > 1,'Mobile source/stored labels are readable');
      }
      else {assert.equal(boxes[1].y,boxes[0].y); assert.ok(boxes[1].x>boxes[0].x);}
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('[data-format-details][open]').count(),0);
      assert.equal(await change.evaluate(e=>document.activeElement===e),true);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
      await page.getByRole('button',{name:'Cancel import',exact:true}).click();
      await upload('code;amount;day\nC;1.234;2024-02-29\nD;2.345;2024-03-01\n');
      assert.equal(await page.locator('[data-format-details][open]').count(),1);
      assert.equal(await page.evaluate(()=>document.activeElement.id),'source-1-0');
      const refresh=page.waitForResponse(r=>r.url().endsWith('/formats') && r.status()===200);
      await page.locator('input[name="source_1"][value="v1-eu"]').check();
      await refresh;
      await page.waitForFunction(()=>!document.querySelector('[data-import-mapping]').hasAttribute('aria-busy'));
      assert.deepEqual(await page.locator('[data-source-formats="1"] .source-format-pair code').allTextContents(),['1.234','1234','2.345','2345']);
      await page.screenshot({path:`/tmp/ref159-regression-numeric-${width}.png`,fullPage:true});
      await page.locator('[data-mapping-continue]').click();
      await page.waitForURL(/\/step\/4$/);
      assert.match(await page.locator('main').innerText(),/1\.234/);
      assert.match(await page.locator('main').innerText(),/2\.345/);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
      assert.deepEqual(errors,[]);
      await page.getByRole('button',{name:'Cancel import',exact:true}).click();
      await upload('code;amount;day;stamp\nE;12.50;2024-02-29;2024-02-29 12:00:00\n');
      let releaseReview, reviewReached, reviewFinished;
      const gate=new Promise(resolve=>{releaseReview=resolve;});
      const reached=new Promise(resolve=>{reviewReached=resolve;});
      const finished=new Promise(resolve=>{reviewFinished=resolve;});
      const holdReview=async route=>{
        const response=await route.fetch();
        reviewReached();
        await gate;
        try {await route.fulfill({response});} finally {reviewFinished();}
      };
      await page.route('**/formats',holdReview);
      await page.getByLabel('Source type for stamp',{exact:true}).locator('xpath=self::select | following-sibling::select[1]').selectOption('text');
      await reached;
      const zone=page.getByLabel('IANA source timezone (required for local timestamps)',{exact:true});
      await zone.fill('Europe/London');
      releaseReview();
      await finished;
      await page.waitForFunction(()=>!document.querySelector('[data-import-mapping]').hasAttribute('aria-busy'));
      assert.equal(await zone.inputValue(),'Europe/London','A late review must preserve newly typed timezone text');
      assert.equal(await zone.evaluate(e=>document.activeElement===e),true);
      await page.unroute('**/formats',holdReview);
      await page.getByRole('button',{name:'Cancel import',exact:true}).click();
      const changeControl = async (label, value) => {
        const response = page.waitForResponse(r=>r.url().endsWith('/formats') && r.status()===200);
        await page.getByLabel(label,{exact:true}).focus();
        await page.getByLabel(label,{exact:true}).locator('xpath=self::select | following-sibling::select[1]').selectOption(value);
        const reviewed = await response;
        assert.match(reviewed.request().headers()['content-type'],/^application\/x-www-form-urlencoded/);
        await page.waitForFunction(()=>!document.querySelector('[data-import-mapping]').hasAttribute('aria-busy'));
      };
      await upload('code;amount;moment;clock;measure\nF;12.5;2024-02-29T12:34:56;12:34:56;102.1059\nG;13.5;2024-03-01T12:34:56;12:34:56;6.7e+07\n');
      assert.equal(await page.locator('[data-format-details][open]').count(),0);
      await page.getByRole('button',{name:'Show all formats',exact:true}).click();
      assert.equal(await page.locator('[data-format-details][open]').count(),4);
      // Its accessible name describes the next aggregate action.
      assert.equal(await page.locator('[data-advanced-formats]').textContent(),'Hide all formats');
      assert.equal(await page.getByLabel('Date format for moment',{exact:true}).locator('option').count(),8);
      assert.equal(await page.getByLabel('Time format for moment',{exact:true}).locator('option').count(),4);
      assert.deepEqual(await page.locator('[data-source-formats="4"] .source-format-pair code').allTextContents(),['102.1059','102.1059','6.7e+07','67000000.0']);
      await changeControl('Date format for moment','v1-date-dmy-dash');
      assert.match(await page.locator('[data-source-formats="2"] [data-effective-format]').textContent(),/DD-MM-YYYY/);
      assert.equal(await page.evaluate(()=>document.activeElement.name),'date_2');
      assert.equal(await page.getByLabel('Date format for moment',{exact:true}).getAttribute('aria-invalid'),'true');
      assert.match(await page.locator('#source-error-2').textContent(),/do not fit/);
      await changeControl('Date format for moment','v1-date-ymd-dash');
      await changeControl('Time format for moment','v1-time-hm');
      assert.equal(await page.locator('[data-source-formats="2"] [data-effective-format]').textContent(),'Year first · YYYY-MM-DD + 24-hour, minutes · HH:mm');
      assert.equal(await page.evaluate(()=>document.activeElement.name),'time_2');
      await changeControl('Time format for moment','v1-time-hms');
      assert.deepEqual(await page.locator('[data-source-formats="2"] .source-format-pair code').allTextContents(),['2024-02-29T12:34:56','2024-02-29T12:34:56','2024-03-01T12:34:56','2024-03-01T12:34:56']);
      const textValues = ['v1-date-ymd-dash','v1-time-hms'];
      await changeControl('Source type for moment','native');
      assert.equal(await page.getByLabel('Date format for moment',{exact:true}).isVisible(),false);
      assert.equal(await page.getByLabel('Date format for moment',{exact:true}).isDisabled(),true);
      assert.deepEqual(await page.locator('[data-import-mapping]').evaluate(form=>[...new FormData(form).keys()].filter(k=>['date_2','time_2'].includes(k))),[]);
      assert.equal(await page.locator('[data-source-formats="2"] [data-effective-format]').textContent(),'Native Excel datetime');
      await changeControl('Source type for moment','text');
      assert.deepEqual(await Promise.all(['Date','Time'].map(part=>page.getByLabel(part+' format for moment',{exact:true}).locator('xpath=self::select | following-sibling::select[1]').inputValue())),textValues);
      // Reinitializing the same form twice must not duplicate listeners or requests.
      await page.evaluate(()=>{window.refrncrSourceFormats();window.refrncrSourceFormats();});
      let reviews = 0;
      const countReview = request=>{if(request.url().endsWith('/formats')) ++reviews;};
      page.on('request',countReview);
      await changeControl('Time format for moment','v1-time-hms12');
      assert.equal(reviews,1);
      page.off('request',countReview);
      await changeControl('Time format for moment','v1-time-hms');
      const partBoxes = await page.locator('[data-source-formats="2"] [data-text-formats] > div').evaluateAll(es=>es.map(e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width};}));
      if(width===320) assert.ok(partBoxes[1].y>partBoxes[0].y && partBoxes[1].x===partBoxes[0].x);
      else assert.equal(partBoxes[0].y,partBoxes[1].y);
      const layout = await page.evaluate(()=>({viewport:innerWidth,document:document.documentElement.scrollWidth}));
      assert.ok(layout.document<=width);
      console.log(JSON.stringify({width,layout,partBoxes}));
      await page.getByLabel('Date format for moment',{exact:true}).focus();
      const focusStyle = await page.getByLabel('Date format for moment',{exact:true}).evaluate(e=>({outline:getComputedStyle(e).outlineStyle,width:getComputedStyle(e).outlineWidth,shadow:getComputedStyle(e).boxShadow}));
      assert.ok((focusStyle.outline !== 'none' && focusStyle.width !== '0px') || focusStyle.shadow !== 'none');
      await page.keyboard.press('Tab');
      assert.equal(await page.evaluate(()=>document.activeElement.name),'time_2');
      for (const key of ['1','2','2']) {
        const keyboardReview = page.waitForResponse(r=>r.url().endsWith('/formats') && r.status()===200);
        // Native type-ahead works without depending on OS popup arrow handling.
        await page.keyboard.press(key);
        await keyboardReview;
        await page.waitForFunction(()=>!document.querySelector('[data-import-mapping]').hasAttribute('aria-busy'));
        assert.equal(await page.evaluate(()=>document.activeElement.name),'time_2');
      }
      assert.equal(await page.getByLabel('Time format for moment',{exact:true}).locator('xpath=self::select | following-sibling::select[1]').inputValue(),'v1-time-hms');
      await page.screenshot({path:`/tmp/ref159-regression-${width}.png`,fullPage:true});
      await page.locator('[data-mapping-continue]').focus();
      await page.keyboard.press('Enter');
      await page.waitForURL(/\/step\/4$/);
      await page.getByText('67000000.0',{exact:true}).waitFor();
      await page.getByRole('link',{name:'Back',exact:true}).focus();
      await page.keyboard.press('Enter');
      await page.waitForURL(/\/step\/3$/);
      assert.deepEqual(await Promise.all(['Date','Time'].map(part=>page.getByLabel(part+' format for moment',{exact:true}).locator('xpath=self::select | following-sibling::select[1]').inputValue())),textValues);
      await page.reload();
      await page.waitForURL(/\/step\/3$/);
      assert.deepEqual(await Promise.all(['Date','Time'].map(part=>page.getByLabel(part+' format for moment',{exact:true}).locator('xpath=self::select | following-sibling::select[1]').inputValue())),textValues);
      await page.getByRole('button',{name:'Cancel import',exact:true}).click();
      await upload('code;amount;moment\nH;1;01/02/2024 12:34:56\n');
      assert.equal(await page.locator('[data-format-details][open]').count(),1);
      assert.equal(await page.evaluate(()=>document.activeElement.id),'source-2-0');
      const choiceReview = page.waitForResponse(r=>r.url().endsWith('/formats') && r.status()===200);
      await page.locator('input[name="source_2"][value="v1-datetime-dmy-slash-hms"]').check();
      await choiceReview;
      await page.waitForFunction(()=>!document.querySelector('[data-import-mapping]').hasAttribute('aria-busy'));
      assert.equal(await page.getByLabel('Date format for moment',{exact:true}).locator('xpath=self::select | following-sibling::select[1]').inputValue(),'v1-date-dmy-slash');
      assert.equal(await page.getByLabel('Time format for moment',{exact:true}).locator('xpath=self::select | following-sibling::select[1]').inputValue(),'v1-time-hms');
      assert.match(await page.locator('[data-source-formats="2"] .source-format-pair').textContent(),/2024-02-01T12:34:56/);
      await page.getByRole('button',{name:'Cancel import',exact:true}).click();
      assert.deepEqual(errors,[]);
      await page.goto(base+'/datasets/imports/records/import');
      await page.getByLabel('CSV or Excel file').setInputFiles({name:'native.xlsx',mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',buffer:nativeWorkbook.stdout});
      await page.getByRole('button',{name:'Continue',exact:true}).click();
      await page.waitForURL(/\/step\/3$/);
      await page.getByRole('button',{name:'Show all formats',exact:true}).click();
      for(const key of ['day','clock','moment']) {
        assert.equal(await page.getByLabel('Source type for '+key,{exact:true}).locator('xpath=self::select | following-sibling::select[1]').inputValue(),'native');
        await changeControl('Source type for '+key,'text');
        await changeControl('Source type for '+key,'native');
      }
      assert.match(await page.locator('[data-format-status]').textContent(),/Stored as updated/);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
      await page.locator('[data-mapping-continue]').click();
      await page.waitForURL(/\/step\/4$/);
      await page.getByText('2024-02-29T12:34:56',{exact:true}).waitFor();
      await page.getByRole('button',{name:'Cancel import',exact:true}).click();
      await upload('code;amount;stamp\nZ;1;2024-02-29T12:00:00Z\nOffset;1;2024-02-29T13:00:00+01:00\n');
      await page.getByRole('button',{name:'Show all formats',exact:true}).click();
      assert.equal(await page.getByLabel('Source type for stamp',{exact:true}).locator('xpath=self::select | following-sibling::select[1]').inputValue(),'offset');
      assert.equal(await page.getByLabel('IANA source timezone (required for local timestamps)',{exact:true}).isVisible(),false);
      assert.deepEqual(await page.locator('[data-source-formats="2"] .source-format-pair code').allTextContents(),['2024-02-29T12:00:00Z','2024-02-29T12:00:00Z','2024-02-29T13:00:00+01:00','2024-02-29T12:00:00Z']);
      await changeControl('Source type for stamp','text');
      assert.equal(await page.getByLabel('IANA source timezone (required for local timestamps)',{exact:true}).isVisible(),true);
      const zoneResponse=page.waitForResponse(r=>r.url().endsWith('/formats') && r.status()===200);
      await page.getByLabel('IANA source timezone (required for local timestamps)',{exact:true}).fill('Europe/Amsterdam');
      await page.keyboard.press('Tab');
      await zoneResponse;
      await page.waitForFunction(()=>!document.querySelector('[data-import-mapping]').hasAttribute('aria-busy'));
      await changeControl('Source type for stamp','offset');
      assert.equal(await page.getByLabel('IANA source timezone (required for local timestamps)',{exact:true}).inputValue(),'');
      assert.equal(await page.getByLabel('Repeated local times',{exact:true}).locator('xpath=self::select | following-sibling::select[1]').inputValue(),'');
      assert.equal(await page.getByLabel('IANA source timezone (required for local timestamps)',{exact:true}).isVisible(),false);
      await page.getByRole('button',{name:'Cancel import',exact:true}).click();
      assert.deepEqual(errors,[]);
      console.log(`PASS ${width}: disclosure, labels, paired conversion, keyboard/Escape/focus, ambiguity, Preview, no overflow`);
      await context.close();
      const plain = await browser.newContext({javaScriptEnabled:false,viewport:{width,height:1000}});
      const plainPage = await plain.newPage();
      plainPage.setDefaultTimeout(15000);
      plainPage.setDefaultNavigationTimeout(15000);
      await plainPage.goto(base+'/login');
      await plainPage.getByLabel('Email address').fill('admin@example.com');
      await plainPage.getByLabel('Password',{exact:true}).fill('correct horse battery staple');
      await plainPage.getByRole('button',{name:'Sign in',exact:true}).click();
      await plainPage.waitForURL(base+'/');
      await plainPage.goto(base+'/datasets/imports/records/import');
      await plainPage.getByLabel('CSV or Excel file').setInputFiles({name:'plain.csv',mimeType:'text/csv',buffer:Buffer.from('code;amount;moment\nNoJS;12.5;2024-02-29T12:34:56\n')});
      await plainPage.getByRole('button',{name:'Continue',exact:true}).click();
      await plainPage.waitForURL(/\/step\/3$/);
      assert.equal(await plainPage.locator('noscript p').isVisible(),true);
      assert.match(await plainPage.locator('noscript p').textContent(),/JavaScript is disabled/);
      assert.equal(await plainPage.locator('[data-advanced-formats]').isVisible(),false);
      await plainPage.locator('[data-mapping-continue]').click();
      await plainPage.waitForURL(/\/step\/4$/);
      await plainPage.getByText('2024-02-29T12:34:56',{exact:true}).waitFor();
      await plainPage.getByRole('button',{name:'Cancel import',exact:true}).click();
      console.log(`PASS no-JavaScript ${width}: detected temporal Preview and explanatory fallback`);
      await plain.close();
    }
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
