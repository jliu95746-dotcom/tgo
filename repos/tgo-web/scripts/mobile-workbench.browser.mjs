// Usage: PLAYWRIGHT_MODULE_PATH=/path/to/playwright/index.mjs node scripts/mobile-workbench.browser.mjs
// Reuses an installed Playwright runtime and Chrome; never copies real login state.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const artifacts = path.resolve(root, '../../output/playwright/mobile-workbench');
await fs.mkdir(artifacts, { recursive: true });
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE_PATH ? pathToFileURL(process.env.PLAYWRIGHT_MODULE_PATH).href : 'playwright');
const port = process.env.MOBILE_PREVIEW_PORT || await new Promise((resolve, reject) => {
  const probe = createServer();
  probe.once('error', reject);
  probe.listen(0, '127.0.0.1', () => {
    const address = probe.address();
    probe.close(() => resolve(String(address.port)));
  });
});
const origin = `http://127.0.0.1:${port}`;
const preview = spawn(process.execPath, [path.join(root, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', port, '--strictPort'], { cwd: root, env: { ...process.env, TGO_DEV_TYPECHECK: '0', VITE_API_BASE_URL: '/__mobile_fixture_api' }, stdio: ['ignore', 'pipe', 'pipe'] });
let serverOutput = '';
preview.stdout.on('data', data => { serverOutput += data; });
preview.stderr.on('data', data => { serverOutput += data; });
let browser;
let page;
const results = [];
try {
  for (let i = 0; i < 150; i++) {
    if (preview.exitCode !== null) throw new Error(serverOutput);
    try { if ((await fetch(origin)).ok) break; } catch {}
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  browser = await chromium.launch({ channel: 'chrome', headless: true });
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, locale: 'zh-CN', userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 MicroMessenger/8.0.50 wxwork/4.1.30', hasTouch: true });
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (!url.pathname.startsWith('/__mobile_fixture_api') && !url.pathname.startsWith('/api/')) return route.continue();
    let body = { enabled: false, items: [], visitors: [], skills: [], buttons: [], plugins: [], messages: [], channels: [], conversations: [], count: 0, total: 0 };
    if (url.pathname.endsWith('/subscription')) body = { status: 'active' };
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  page = await context.newPage();
  page.setDefaultTimeout(60000);
  const errors = [];
  let failBrowser;
  const fatalBrowserError = new Promise((_, reject) => { failBrowser = reject; });
  fatalBrowserError.catch(() => {});
  page.on('pageerror', error => { errors.push(error.message); console.error('BROWSER_ERROR', error.stack); failBrowser(error); });
  page.on('console', message => { if (message.type() === 'error') console.error('BROWSER_CONSOLE', message.text().slice(0, 500)); });
  page.on('requestfailed', request => console.error('REQUEST_FAILED', new URL(request.url()).pathname, request.failure()?.errorText));
  const entry = `${origin}/scripts/fixtures/mobile-workbench.html`;
  await page.goto(entry, { waitUntil: 'domcontentloaded', timeout: 120000 });
  if (errors.length) throw new Error(errors.join('\n'));
  await Promise.race([page.locator('textarea').waitFor({ timeout: 30000 }), fatalBrowserError]);
  // The fixture uses the original /chat deep-link routes in an isolated origin.
  await page.getByRole('textbox').last().waitFor();
  for (const width of [320, 360, 390, 414, 899, 900, 1199, 1200, 1440]) {
    await page.setViewportSize({ width, height: width < 900 ? 844 : 1000 });
    await page.waitForTimeout(150);
    const boxes = await page.evaluate(() => {
      const workspace = document.querySelector('.chat-workspace');
      const overflowing = [...document.querySelectorAll('.chat-workspace button, .chat-workspace textarea, .chat-workspace header')].filter(el => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && (r.left < -1 || r.right > innerWidth + 1);
      }).map(el => ({ tag: el.tagName, label: el.textContent?.trim().slice(0, 50), right: el.getBoundingClientRect().right }));
      return { pageWidth: document.documentElement.scrollWidth, width: innerWidth, workspace: Boolean(workspace), overflowing };
    });
    assert.ok(boxes.workspace, 'Fixture must use the actual chat workspace layout');
    assert.ok(boxes.pageWidth <= width + 1, JSON.stringify(boxes));
    assert.deepEqual(boxes.overflowing, [], JSON.stringify(boxes));
    results.push({ width, horizontalOverflow: false });
    if ([390, 1440].includes(width)) await page.screenshot({ path: path.join(artifacts, `${width}.png`), fullPage: true });
  }
  await page.setViewportSize({ width: 320, height: 700 });
  const modeRow = await page.getByRole('button', { name: '辅助', exact: true }).boundingBox();
  const replyOptions = await page.locator('.chat-reply-options > summary').boundingBox();
  assert.ok(replyOptions.y >= modeRow.y + modeRow.height, 'Advanced settings must follow the dedicated service-mode row');
  await page.getByRole('button', { name: 'AI客服', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('textarea')?.disabled);
  await page.getByRole('button', { name: '人工', exact: true }).click();
  await page.waitForFunction(() => !document.querySelector('textarea')?.disabled);
  await page.getByRole('button', { name: '辅助', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('textarea')?.value.startsWith('目前有蓝色'));
  const table = page.locator('.chat-table-scroll');
  assert.ok(await table.evaluate(element => element.scrollWidth > element.clientWidth), 'Wide tables must scroll inside the message');
  assert.ok(await page.getByAltText('测试图片').evaluate(image => image.getBoundingClientRect().width <= innerWidth), 'Message images must fit');
  await page.getByRole('button', { name: '更多会话操作', exact: true }).click();
  await page.getByRole('button', { name: '结束聊天', exact: true }).waitFor();
  const popup = await page.locator('.chat-header-action-list').boundingBox();
  assert.ok(popup.x >= 0 && popup.x + popup.width <= 321, 'Actions must fit the viewport');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '打开功能菜单', exact: true }).click();
  await page.getByRole('dialog', { name: '打开功能菜单' }).waitFor();
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '打开表情选择器' }).click();
  const emoji = await page.getByRole('dialog', { name: 'Emoji picker' }).boundingBox();
  assert.ok(emoji.x >= 0 && emoji.x + emoji.width <= 321, 'Emoji panel must fit 320px');
  await page.getByRole('button', { name: '插入 😀', exact: true }).click();
  const textarea = page.locator('textarea');
  await textarea.fill('人工已修改：蓝色现货，请确认。');
  assert.deepEqual(await page.evaluate(() => window.mobileLayoutFixture.sends), [], 'Assist drafts must not auto-send');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  assert.deepEqual(await page.evaluate(() => window.mobileLayoutFixture.sends), ['人工已修改：蓝色现货，请确认。']);
  await textarea.fill('横竖屏切换时保留草稿');
  await page.setViewportSize({ width: 844, height: 390 });
  assert.equal(await textarea.inputValue(), '横竖屏切换时保留草稿');
  await page.setViewportSize({ width: 390, height: 420 });
  await page.waitForFunction(() => Math.abs(document.querySelector('.chat-workspace').getBoundingClientRect().height - innerHeight) < 1);
  const composer = await page.locator('.chat-composer').boundingBox();
  assert.ok(composer.y + composer.height <= 421, `Composer must remain within a reduced viewport: ${JSON.stringify(composer)}`);
  const send = await page.getByRole('button', { name: '发送', exact: true }).boundingBox();
  assert.ok(send.y >= 0 && send.y + send.height <= 421, 'Send must be visible above a reduced viewport bottom without scrolling');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${entry}?surface=page`, { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: '返回会话列表', exact: true }).waitFor();
  await page.getByRole('button', { name: '访客信息', exact: true }).click();
  const visitor = page.getByRole('dialog', { name: '访客信息', exact: true });
  await visitor.waitFor();
  const visitorBox = await visitor.boundingBox();
  assert.ok(visitorBox.x >= 0 && visitorBox.x + visitorBox.width <= 391, 'Visitor drawer must fit the phone');
  await page.getByRole('button', { name: '关闭访客信息', exact: true }).last().click();
  await page.getByRole('button', { name: '返回会话列表', exact: true }).click();
  await page.getByText('测试顾客 30', { exact: true }).scrollIntoViewIfNeeded();
  const scrollBefore = await page.evaluate(() => Math.max(...[...document.querySelectorAll('.overflow-y-auto')].map(element => element.scrollTop)));
  await page.getByText('测试顾客 30', { exact: true }).click();
  await page.getByRole('button', { name: '返回会话列表', exact: true }).click();
  const scrollAfter = await page.evaluate(() => Math.max(...[...document.querySelectorAll('.overflow-y-auto')].map(element => element.scrollTop)));
  assert.ok(scrollBefore > 0 && Math.abs(scrollBefore - scrollAfter) < 5, 'List scroll position must survive conversation switching');
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Actual conversation list must not overflow');
  await page.screenshot({ path: path.join(artifacts, '390-list.png'), fullPage: true });
  for (const width of [320, 360, 390, 414, 899, 900, 1199, 1200, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.waitForTimeout(100);
    const overflow = await page.evaluate(() => [...document.querySelectorAll('.chat-workspace button, .chat-workspace textarea, .chat-workspace header')].filter(element => {
      if (element.closest('[aria-hidden="true"]')) return false;
      const r = element.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && (r.x < -1 || r.right > innerWidth + 1);
    }).map(element => element.textContent?.trim().slice(0, 60)));
    assert.deepEqual(overflow, [], `Actual workspace at ${width}px`);
  }
  assert.deepEqual(errors, [], 'No browser runtime errors');
  await fs.writeFile(path.join(artifacts, 'results.json'), JSON.stringify({ results, environment: 'Chromium with WeCom user agent, not a physical phone', interactions: ['more_actions', 'navigation', 'emoji', 'assist_edit_confirm', 'rotation_preserves_draft', 'reduced_height', 'visitor_drawer', 'deep_link', 'list_scroll_restore', 'service_modes', 'table_scroll', 'image_width'], errors }, null, 2));
  console.log(JSON.stringify({ result: 'PASS', artifacts, widths: results.length, interactions: 12 }));
} catch (error) {
  await fs.writeFile(path.join(artifacts, 'server.log'), serverOutput);
  if (page && !page.isClosed()) {
    await page.screenshot({ path: path.join(artifacts, 'failure.png'), fullPage: true }).catch(() => {});
    await fs.writeFile(path.join(artifacts, 'failure.html'), await page.content());
  }
  throw error;
} finally {
  await browser?.close();
  preview.kill();
}
