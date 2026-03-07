const { chromium } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  page.on('console', msg => {
    console.log(`[console ${msg.type()}]`, msg.text());
  });
  page.on('pageerror', err => {
    console.log('[pageerror]', err);
  });
  try {
    const resp = await page.goto('http://127.0.0.1:3000/chat', { waitUntil: 'domcontentloaded', timeout: 15000 });
    console.log('status', resp.status());
    await page.waitForTimeout(8000);
  } catch (e) {
    console.log('goto error', e);
  }
  await browser.close();
})();
