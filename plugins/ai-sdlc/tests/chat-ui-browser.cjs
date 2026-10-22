// Optional real-browser check. Host messages are recorded, never sent to Codex.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

async function main() {
  const fragment = fs.readFileSync(process.argv[2], 'utf8');
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const wrapper = `<!doctype html><html><head><meta charset="utf-8"></head><body>${fragment}</body></html>`;
    await page.setContent(wrapper);
    assert.equal(await page.locator('#sdlc-status').count(), 1);
    const action = page.locator('button[data-prompt^="sdlc ui unlock-tests "]');
    const prompt = await action.getAttribute('data-prompt');
    assert.ok(prompt);

    // A missing host bridge must expose a usable command, with no success state.
    await action.click();
    assert.equal(await page.locator('[data-command] code').innerText(), prompt);
    assert.equal(await page.locator('[data-command]').isVisible(), true);
    assert.match(await page.locator('[data-feedback]').innerText(), /клієнт/);

    // A host rejection is visible, keeps the original token, and re-enables controls.
    await page.evaluate(() => {
      window.requests = [];
      window.openai = { sendFollowUpMessage: async request => {
        window.requests.push(request);
        throw new Error('User cancelled');
      } };
    });
    await action.click();
    await page.waitForFunction(() => !document.getElementById('sdlc-status').hasAttribute('aria-busy'));
    assert.equal(await page.locator('[data-command] code').innerText(), prompt);
    assert.match(await page.locator('[data-feedback]').innerText(), /не підтверджено/);
    assert.equal(await action.isEnabled(), true);

    // Rendering sends nothing. One deliberate click forwards the exact guarded
    // prompt; while the host confirmation is pending, double-clicks cannot send it twice.
    await page.evaluate(() => {
      window.requests = [];
      window.openai.sendFollowUpMessage = request => {
        window.requests.push(request);
        return new Promise(resolve => { window.finishRequest = resolve; });
      };
    });
    assert.equal(await page.evaluate(() => window.requests.length), 0);
    await action.click();
    assert.equal(await action.isDisabled(), true);
    await action.dispatchEvent('click');
    assert.equal(await page.evaluate(() => window.requests.length), 1);
    assert.equal(await page.evaluate(() => window.requests[0].prompt), prompt);
    await page.evaluate(() => window.finishRequest());
    await page.waitForFunction(() => !document.getElementById('sdlc-status').hasAttribute('aria-busy'));
    assert.equal(await action.isEnabled(), true);
    assert.equal(await page.locator('[data-command]').isHidden(), true);
    assert.equal(await page.locator('button:has-text("Розблокувати тести")').count(), 1);

    for (const width of [320, 736]) {
      await page.setViewportSize({ width, height: 850 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true,
        `Layout overflows at ${width}px`);
      if (process.env.SDLC_UI_SCREENSHOTS) {
        fs.mkdirSync(process.env.SDLC_UI_SCREENSHOTS, { recursive: true });
        await page.screenshot({ path: path.join(process.env.SDLC_UI_SCREENSHOTS, `sdlc-${width}.png`), fullPage: true });
      }
    }
    assert.deepEqual(errors, []);
    console.log('Browser checks passed: exact prompt, pending, rejection, missing bridge, 320px and 736px.');
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
