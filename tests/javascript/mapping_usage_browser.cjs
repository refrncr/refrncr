// Checks mapping usage summaries, source and target navigation, and responsive presentation.
const {chromium} = require('./browser_policy.cjs');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({executablePath:process.env.REFRNCR_BROWSER_EXECUTABLE, headless:true});
  const page = await browser.newPage({viewport:{width:1280,height:900}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const base = 'http://127.0.0.1:5194';
  const settings = `${base}/datasets/mapping-browser-source?tab=dataset-details#used-by-mappings`;
  const checkDestination = async () => {
    await page.waitForFunction(() => document.activeElement?.id === 'used-by-mappings-heading');
    const position = await page.evaluate(() => {
      const main = document.querySelector('main.app-main');
      const block = document.getElementById('used-by-mappings').getBoundingClientRect();
      const viewport = main.getBoundingClientRect();
      return {scroll:main.scrollTop, top:block.top, bottom:block.bottom, min:viewport.top, max:viewport.bottom};
    });
    assert(position.scroll > 0, 'main scroll container moved');
    assert(position.top >= position.min && position.bottom <= position.max, 'whole usage block is visible');
    assert(await page.getByRole('button', {name:'Delete dataset', exact:true}).isDisabled());
    assert(await page.getByText("This dataset can't be deleted because 2 mappings are based on it.", {exact:true}).isVisible());
    assert(await page.getByText('Mappings based on this dataset, with the source version they use.', {exact:true}).isVisible());
    assert(await page.getByRole('link', {name:'Draft uses V2', exact:true}).isVisible());
  };
  try {
    await page.goto(`${base}/login`);
    await page.getByLabel('Email address').fill('admin@example.com');
    await page.getByLabel('Password', {exact:true}).fill('correct horse battery staple');
    await page.getByRole('button', {name:'Sign in', exact:true}).click();
    await page.waitForURL(`${base}/`);
    await page.goto(`${base}/datasets/mapping-browser-source/records`);
    const metadata = await page.locator('.metadata').innerText();
    assert(metadata.includes('•') && !metadata.includes('·'), 'metadata separators are consistent');
    await page.getByRole('link', {name:'Used by 2 mappings', exact:true}).click();
    await page.waitForURL(settings);
    await checkDestination();
    // A deep link must also reveal the hidden Settings panel on a fresh load.
    await page.reload();
    await checkDestination();
    // Keyboard activation of the same-page link also restores the anchor and focus.
    await page.getByRole('tab', {name:'Fields', exact:true}).click();
    await page.getByRole('link', {name:'Used by 2 mappings', exact:true}).focus();
    await page.keyboard.press('Enter');
    await page.waitForURL(settings);
    await checkDestination();
    if (process.env.REFRNCR_BROWSER_OUTPUT) {
      await page.screenshot({path:process.env.REFRNCR_BROWSER_OUTPUT});
    }
    assert.deepEqual(errors, []);
    console.log('PASS: 1280x900 anchor scrolling, heading focus, reload, keyboard, deletion block and metadata');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
