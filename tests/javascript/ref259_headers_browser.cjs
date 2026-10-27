const {chromium} = require('./browser_policy.cjs');
const assert = require('node:assert/strict');
const base = process.env.REFRNCR_BROWSER_URL || 'http://127.0.0.1:5162';
if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname)) throw Error('Loopback fixture required');

(async () => {
  const browser = await chromium.launch({headless: true, executablePath: process.env.REFRNCR_BROWSER_EXECUTABLE});
  try {
    const page = await browser.newPage({viewport: {width: 1024, height: 800}, hasTouch: true, isMobile: true});
    await page.goto(base + '/login');
    await page.getByLabel('Email address').fill('admin@example.com');
    await page.getByLabel('Password', {exact: true}).fill('correct horse battery staple');
    await page.getByRole('button', {name: 'Sign in', exact: true}).click();
    await page.waitForURL(base + '/');
    await page.goto(base + '/datasets/polish-320/concept/records');

    const scroll = page.locator('.table-scroll:has(.record-table)');
    const labels = await page.locator('.record-table thead .column-heading-label').allTextContents();
    assert(labels.includes('E_NUMBER') && labels.includes('EU_STATUS_ADDITION'), JSON.stringify(labels));
    assert.equal(await page.locator('.record-table thead .column-heading-label').evaluateAll(nodes => nodes.some(node => getComputedStyle(node).textOverflow === 'ellipsis' || getComputedStyle(node).overflow === 'hidden')), false);
    for (const width of [390, 1024, 1440]) {
      await page.setViewportSize({width, height: 800});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `page overflow at ${width}px`);
      if (width === 390) {
        const dimensions = await scroll.evaluate(node => ({clientWidth: node.clientWidth, scrollWidth: node.scrollWidth}));
        assert(dimensions.scrollWidth > dimensions.clientWidth, `missing table scroll ${JSON.stringify(dimensions)}`);
        await scroll.evaluate(node => { node.scrollLeft = node.scrollWidth; });
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'table scroll caused page overflow');
      }
      if (width === 1024) {
        const offset = await scroll.evaluate(node => {
          node.scrollTop = 240;
          return node.querySelector('thead th').getBoundingClientRect().top - node.getBoundingClientRect().top;
        });
        assert(offset >= 0 && offset <= 3, `sticky heading offset ${offset}px`);
        await scroll.evaluate(node => { node.scrollTop = 0; });
      }
      if (width >= 1024) {
        const headingHeight = await page.locator('.record-table thead').evaluate(node => node.getBoundingClientRect().height);
        assert(headingHeight <= 42, `header wrapped to ${headingHeight}px at ${width}px`);
      }
    }

    await page.setViewportSize({width: 1024, height: 800});
    const buttons = page.locator('.record-table thead .field-metadata-trigger');
    const first = buttons.first(), last = buttons.last();
    const sizes = await first.evaluate(button => {
      const trigger = button.getBoundingClientRect(), icon = button.querySelector('.button-icon').getBoundingClientRect();
      return {width: trigger.width, height: trigger.height, iconWidth: icon.width, iconHeight: icon.height};
    });
    assert.deepEqual(sizes, {width: 24, height: 24, iconWidth: 13, iconHeight: 13});
    const contrast = async (button) => button.evaluate(element => {
      const rgb = value => value.match(/[\d.]+/g).slice(0, 3).map(Number);
      const luma = value => {
        const channels = rgb(value).map(channel => { channel /= 255; return channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4; });
        return .2126 * channels[0] + .7152 * channels[1] + .0722 * channels[2];
      };
      const a = luma(getComputedStyle(element).color), b = luma(getComputedStyle(element.closest('th')).backgroundColor);
      return (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
    });
    assert(await contrast(first) >= 3, 'light-theme idle icon contrast is below 3:1');
    await first.hover();
    await page.waitForFunction(() => document.querySelector('.record-table thead .field-metadata-trigger').getAttribute('aria-expanded') === 'true');
    await page.mouse.move(5, 5);
    await page.waitForFunction(() => document.querySelector('.record-table thead .field-metadata-trigger').getAttribute('aria-expanded') === 'false');
    await first.focus();
    assert.equal(await first.getAttribute('aria-expanded'), 'true', 'keyboard focus opens metadata');
    await first.click();
    await page.mouse.move(5, 5);
    assert.equal(await first.getAttribute('aria-expanded'), 'true', 'click pins metadata open');
    await page.keyboard.press('Escape');
    assert.equal(await first.getAttribute('aria-expanded'), 'false');
    assert.equal(await first.evaluate(element => element === document.activeElement), true, 'Escape restores focus');
    await first.click();
    const bounds = async () => page.locator('.record-table thead .field-metadata-popover:popover-open').evaluate(element => {
      const rect = element.getBoundingClientRect();
      return {left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom};
    });
    const withinViewport = rect => rect.left >= 0 && rect.right <= 1024 && rect.top >= 0 && rect.bottom <= 800;
    const popover = page.locator('.record-table thead .field-metadata-popover:popover-open');
    const content = await popover.innerText();
    assert.match(content, /E_NUMBER\s+e_number/);
    assert.match(content, /String/);
    assert.match(content, /A readable identifier/);
    assert.match(content, /Unit: days/);
    assert.doesNotMatch(content, /Definition:|Required: No|Unique: No/);
    assert.match(content, /Identifier\s+Required\s+Unique/);
    assert(await popover.evaluate(element => element.getBoundingClientRect().width < 448), 'short metadata should not force a wide popover');
    const identifierChip = popover.locator('.fmp-chip-identifier');
    const textContrast = locator => locator.evaluate(element => {
      const luminance = value => {
        const channels = value.match(/[\d.]+/g).slice(0, 3).map(channel => {
          channel = Number(channel) / 255;
          return channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4;
        });
        return .2126 * channels[0] + .7152 * channels[1] + .0722 * channels[2];
      };
      const foreground = luminance(getComputedStyle(element).color);
      const background = luminance(getComputedStyle(element).backgroundColor);
      return (Math.max(foreground, background) + .05) / (Math.min(foreground, background) + .05);
    });
    assert(await textContrast(identifierChip) >= 4.5, 'light identifier chip text contrast is below 4.5:1');
    assert(withinViewport(await bounds()), 'first-column popover escapes viewport');
    await last.click();
    assert.equal(await page.locator('.record-table thead .field-metadata-popover:popover-open').count(), 1, 'multiple popovers open');
    assert(withinViewport(await bounds()), 'last-column popover escapes viewport');
    await page.mouse.click(5, 5);
    assert.equal(await page.locator('.record-table thead .field-metadata-popover:popover-open').count(), 0, 'outside click failed to close popover');
    await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
    assert(await contrast(first) >= 3, 'dark-theme idle icon contrast is below 3:1');
    await first.click({force: true});
    const darkPopover = page.locator('.record-table thead .field-metadata-popover:popover-open');
    assert(await darkPopover.evaluate(element => getComputedStyle(element).backgroundColor !== 'rgba(0, 0, 0, 0)'));
    assert(await textContrast(darkPopover.locator('.fmp-chip-identifier')) >= 4.5, 'dark identifier chip text contrast is below 4.5:1');
    await page.setViewportSize({width: 1024, height: 320});
    await first.evaluate(button => window.scrollTo(0, window.scrollY + button.getBoundingClientRect().top - 250));
    await page.waitForTimeout(50);
    const shortViewportBounds = await bounds();
    const viewportHeight = await page.evaluate(() => window.innerHeight);
    assert(shortViewportBounds.top >= 0 && shortViewportBounds.bottom <= viewportHeight, `popover escapes short viewport ${viewportHeight}: ${JSON.stringify(shortViewportBounds)}`);
    console.log('REF-259 table headers: responsive scroll, sticky header, contrast, sizing and popover checks passed.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
