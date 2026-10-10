import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const moduleRequire = createRequire(import.meta.url);
const locale = JSON.parse(readFileSync(new URL('../src/i18n/locales/zh.json', import.meta.url), 'utf8'));
const translate = (key, options = {}) => {
  const value = key.split('.').reduce((entry, field) => entry?.[field], locale) || key;
  return Object.entries(options).reduce((text, [name, replacement]) => text.replaceAll(`{{${name}}}`, replacement), value);
};

test('queue timeout preserves its text and refreshes the actual channel status', () => {
  const types = loadSource('types/index.ts');
  assert.equal(types.isChannelRefreshSystemMessage(1005), true);
  assert.equal(types.isSystemMessageType(1005), true);
  assert.equal(types.isChannelRefreshSystemMessage(1004), true);
  const { default: SystemMessage } = loadSource('components/chat/messages/SystemMessage.tsx', {
    '@/types': types,
    'react-i18next': { useTranslation: () => ({ t: key => key }) },
  });
  const content = '等候超时，这次排队已结束。还需要人工的话，可以重新申请。';
  const html = renderToStaticMarkup(React.createElement(SystemMessage, {
    payload: { type: 1005, content, extra: [] },
  }));
  assert.ok(html.includes(content));
  assert.ok(!html.includes('sessionClosed'));
});

function loadSource(path, imports = {}, globals = {}) {
  const source = readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
  const scope = { exports: {}, require: name => imports[name] ?? moduleRequire(name), ...globals };
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
    jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  } }).outputText, scope);
  return scope.exports;
}

function makeService(permission = 'granted') {
  const notifications = [];
  const inApp = [];
  let sounds = 0;
  let time = 10000;
  const auth = { isAuthenticated: true, user: { id: 'staff-a', project_id: 'project-a' } };
  class BrowserNotification {
    static permission = permission;
    static fail = false;
    static requestPermission = async () => BrowserNotification.permission;
    constructor(title, options) {
      if (BrowserNotification.fail) throw new Error('desktop API unavailable');
      this.title = title; this.options = options; notifications.push(this);
    }
    close() { this.closed = true; }
  }
  const document = { hidden: false };
  const { notificationService: service, DEFAULT_NOTIFICATION_PREFERENCES: preferences } = loadSource(
    'services/notificationService.ts', {
      '@/constants': { MESSAGE_SENDER_TYPE: { SYSTEM: 'system' } },
      '@/types': { MessagePayloadType: { HUMAN_HANDOFF_REQUESTED: 1004 } },
      '@/stores/authStore': { useAuthStore: { getState: () => auth } },
      '@/i18n': { __esModule: true, default: { t: translate } },
    }, {
      window: { Notification: BrowserNotification, focus() {} }, document,
      Notification: BrowserNotification, Audio: class { addEventListener() {} },
      Date: class extends Date { static now() { return time; } },
      setTimeout() {}, console: { warn() {}, error() {}, log() {} },
    },
  );
  service.playNotificationSound = async () => { sounds++; };
  const notify = (raw = { project_id: 'project-a', waiting_count: 2 }, overrides = {}, onClick = () => {}) =>
    service.checkAndNotifyQueue(raw, { ...preferences, ...overrides }, onClick, (title, body) => inApp.push({ title, body }));
  return { service, preferences, notifications, inApp, notify, document, auth,
    BrowserNotification, sounds: () => sounds, advance: () => { time += 1500; } };
}

test('queue events produce an actionable Chinese alert without inventing a visitor identity', () => {
  const fixture = makeService();
  let clicked = false;
  fixture.notify(undefined, {}, () => { clicked = true; });
  assert.equal(fixture.notifications.length, 1);
  assert.equal(fixture.sounds(), 1);
  assert.match(fixture.notifications[0].title, /访客/);
  assert.match(fixture.notifications[0].options.body, /未分配/);
  fixture.notifications[0].onclick();
  assert.equal(clicked, true);
  assert.equal(fixture.notifications[0].closed, true);
});

test('human handoff alerts staff even while viewing that conversation', () => {
  const fixture = makeService();
  const message = { type: 'system', payloadType: 1004, fromUid: 'system',
    channelId: 'visitor-vtr', channelType: 251, content: '客户申请人工客服' };
  const active = { channelId: 'visitor-vtr', channelType: 251 };
  assert.equal(fixture.service.shouldNotify(message, active, fixture.preferences), true);
  assert.equal(fixture.service.shouldPlaySound(message, active, fixture.preferences), true);
  assert.equal(fixture.service.shouldNotify({ ...message, payloadType: 1001 }, active, fixture.preferences), false);
});

test('human handoff provides an in-app alert when desktop permission is unavailable', () => {
  const fixture = makeService('denied');
  const message = { type: 'system', payloadType: 1004, fromUid: 'system',
    channelId: 'visitor-vtr', channelType: 251, content: '客户申请人工客服' };
  fixture.service.checkAndNotify(message, null, fixture.preferences, undefined,
    (title, body) => fixture.inApp.push({ title, body }));
  assert.equal(fixture.inApp.length, 1);
  assert.equal(fixture.sounds(), 1);
});

test('malformed, empty and foreign-project queue events do not alert', () => {
  const fixture = makeService();
  for (const raw of [null, {}, [], 'bad', { project_id: 'project-b', waiting_count: 1 },
    { project_id: 'project-a', waiting_count: '1' }, { project_id: 'project-a', waiting_count: 0 },
    { project_id: 'project-a', waiting_count: 1.5 }, { project_id: 'project-a', waiting_count: Infinity }]) {
    fixture.notify(raw);
  }
  fixture.auth.isAuthenticated = false;
  fixture.notify();
  assert.equal(fixture.notifications.length, 0);
  assert.equal(fixture.inApp.length, 0);
  assert.equal(fixture.sounds(), 0);
});

test('queue toggle, master controls and background preference are respected', () => {
  const fixture = makeService();
  fixture.notify(undefined, { notifyOnNewVisitor: false });
  fixture.notify(undefined, { notificationEnabled: false, notificationSound: false });
  fixture.notify({ project_id: 'project-a', waiting_count: 1, reason: 'updated' });
  fixture.document.hidden = true;
  fixture.notify(undefined, { notifyOnBackground: false });
  assert.equal(fixture.notifications.length, 0);
  assert.equal(fixture.sounds(), 0);
});

test('permission denial falls back to an in-app queue alert and sound can be disabled', () => {
  const fixture = makeService('denied');
  fixture.notify(undefined, { notificationSound: false });
  assert.equal(fixture.notifications.length, 0);
  assert.equal(fixture.inApp.length, 1);
  assert.equal(fixture.sounds(), 0);
});

test('a throwing desktop notification falls back to the page instead of losing the alert', () => {
  const fixture = makeService();
  fixture.BrowserNotification.fail = true;
  fixture.notify();
  assert.equal(fixture.inApp.length, 1);
});

test('queue bursts are debounced within this session but a later event can notify', () => {
  const fixture = makeService();
  fixture.notify();
  fixture.notify();
  assert.equal(fixture.notifications.length, 1);
  fixture.advance();
  fixture.notify();
  assert.equal(fixture.notifications.length, 2);
});

test('a failed permission prompt preserves the actual browser state', async () => {
  const fixture = makeService('default');
  fixture.BrowserNotification.requestPermission = async () => { throw new Error('browser prompt unavailable'); };
  assert.equal(await fixture.service.requestPermission(), 'default');
});

test('test notification reports failure honestly and respects sound preference', () => {
  const fixture = makeService('denied');
  assert.equal(fixture.service.sendTestNotification(false), false);
  fixture.BrowserNotification.permission = 'granted';
  assert.equal(fixture.service.sendTestNotification(false), true);
  assert.equal(fixture.sounds(), 0);
});

test('stale queue notifications cannot navigate after the signed-in account changes', () => {
  const fixture = makeService();
  let clicks = 0;
  fixture.notify(undefined, {}, () => { clicks++; });
  fixture.auth.user = { id: 'staff-b', project_id: 'project-b' };
  fixture.notifications[0].onclick();
  assert.equal(clicks, 0);
  assert.equal(fixture.notifications[0].closed, true);
});

test('the mounted queue manager forwards real subscription events with current preferences and cleans up', () => {
  const effects = [];
  const calls = [];
  const toasts = [];
  let subscriber;
  let cleanupCount = 0;
  const auth = { token: 'local-test-token', user: { id: 'staff-a', project_id: 'project-a' } };
  const ui = { preferences: { notifyOnNewVisitor: true } };
  const globals = { window: { location: { href: '' } } };
  const { QueueNotificationManager } = loadSource('components/QueueNotificationManager.tsx', {
    react: { useEffect: effect => effects.push(effect), useContext: () => ({ showToast: (...args) => toasts.push(args) }) },
    '@/stores/authStore': { useAuthStore: select => select(auth) },
    '@/stores/uiStore': { useUIStore: { getState: () => ui } },
    '@/services/wukongimWebSocket': { wukongimWebSocketService: { onQueueUpdated: handler => {
      subscriber = handler; return () => { cleanupCount++; };
    } } },
    '@/services/notificationService': { DEFAULT_NOTIFICATION_PREFERENCES: {}, notificationService: {
      checkAndNotifyQueue: (...args) => calls.push(args),
    } },
    '@/components/ui/ToastContainer': { ToastContext: {} },
  }, globals);
  assert.equal(QueueNotificationManager(), null);
  const cleanup = effects[0]();
  const raw = { project_id: 'project-a', waiting_count: 1 };
  subscriber({ raw });
  assert.equal(calls[0][0], raw);
  assert.equal(calls[0][1].notifyOnNewVisitor, true);
  ui.preferences.notifyOnNewVisitor = false;
  subscriber({ raw });
  assert.equal(calls[1][1].notifyOnNewVisitor, false);
  calls[0][2]();
  assert.equal(globals.window.location.href, '/chat?tab=unassigned');
  calls[0][3]('title', 'body');
  assert.deepEqual(toasts[0], ['info', 'title', 'body', 8000]);
  cleanup();
  assert.equal(cleanupCount, 1);
});

test('permission notice is inline, links to settings, and never requests permission automatically', () => {
  const state = { permission: 'default', isSupported: true, preferences: { notificationEnabled: true } };
  const { default: Notice } = loadSource('components/notifications/NotificationPermissionNotice.tsx', {
    'react-i18next': { useTranslation: () => ({ t: translate }) },
    'react-router-dom': { Link: ({ to, ...props }) => React.createElement('a', { href: to, ...props }) },
    '@/hooks/useNotification': { useNotification: () => state },
  });
  const render = () => renderToStaticMarkup(React.createElement(Notice));
  assert.match(render(), /桌面通知还没开启/);
  assert.match(render(), /href="\/settings\/notifications"/);
  assert.match(render(), /暂不提醒/);
  assert.doesNotMatch(render(), /fixed|absolute/);
  state.permission = 'denied';
  assert.match(render(), /浏览器已关闭/);
  state.isSupported = false;
  assert.match(render(), /不支持桌面通知/);
  state.isSupported = true;
  state.permission = 'granted';
  assert.equal(render(), '');
  state.permission = 'default';
  state.preferences.notificationEnabled = false;
  assert.equal(render(), '');
});

test('permission hook refreshes on browser focus and removes both listeners on unmount', () => {
  const effects = [];
  const refreshed = [];
  const listeners = new Map();
  const removed = [];
  let permission = 'default';
  const listen = surface => ({
    addEventListener: (name, handler) => listeners.set(`${surface}:${name}`, handler),
    removeEventListener: (name, handler) => {
      assert.equal(handler, listeners.get(`${surface}:${name}`)); removed.push(`${surface}:${name}`);
    },
  });
  const { useNotification } = loadSource('hooks/useNotification.ts', {
    react: { useState: initial => [initial, value => refreshed.push(value)],
      useCallback: callback => callback, useEffect: effect => effects.push(effect) },
    '@/stores/uiStore': { useUIStore: select => select({ preferences: {}, updatePreferences() {} }) },
    '@/services/notificationService': { DEFAULT_NOTIFICATION_PREFERENCES: {}, notificationService: {
      isSupported: () => true, getPermission: () => permission,
    } },
  }, { window: listen('window'), document: listen('document') });
  useNotification();
  const cleanup = effects[0]();
  permission = 'granted';
  listeners.get('window:focus')();
  assert.equal(refreshed.at(-1), 'granted');
  cleanup();
  assert.deepEqual(removed, ['document:visibilitychange', 'window:focus']);
});

function settingsFixture() {
  const changes = [];
  const state = {
    permission: 'default', isSupported: true, isRequesting: false,
    preferences: { notificationEnabled: true, notificationSound: false,
      notifyOnBackground: true, notifyOnOtherConversation: true, notifyOnNewVisitor: true },
    requestPermission: () => { throw new Error('must not request permission during render'); },
    updatePreferences: patch => changes.push(patch), sendTestNotification: () => true,
  };
  const { default: Toggle } = loadSource('components/ui/Toggle.tsx');
  const { default: Settings } = loadSource('components/settings/NotificationSettings.tsx', {
    'react-i18next': { useTranslation: () => ({ t: translate }) },
    '@/hooks/useNotification': { useNotification: () => state },
    '@/components/ui/Toggle': { __esModule: true, default: Toggle },
  });
  const render = () => {
    const html = renderToStaticMarkup(React.createElement(Settings));
    const desktop = html.match(/<button\b[^>]*aria-label="桌面通知"[^>]*>/)?.[0];
    assert.ok(desktop, 'desktop toggle must remain accessible');
    return { html, desktop };
  };
  return { state, changes, render };
}

for (const permission of ['default', 'denied']) {
  test(`desktop settings do not claim to be enabled with ${permission} permission`, () => {
    const fixture = settingsFixture();
    fixture.state.permission = permission;
    const { html, desktop } = fixture.render();
    assert.match(desktop, /aria-checked="false"/);
    assert.match(desktop, /disabled=""/);
    assert.match(html, /偏好已保留/);
    assert.equal(fixture.state.preferences.notificationEnabled, true);
    assert.equal(fixture.changes.length, 0);
  });
}

test('unsupported desktop notifications are distinct from permission denied', () => {
  const fixture = settingsFixture();
  fixture.state.isSupported = false;
  fixture.state.permission = 'denied';
  const { html, desktop } = fixture.render();
  assert.match(desktop, /aria-checked="false"/);
  assert.match(desktop, /disabled=""/);
  assert.match(html, /不支持桌面通知/);
  assert.doesNotMatch(html, /已拒绝|请在浏览器设置中允许通知权限|点击按钮请求通知权限/);
});

test('restored permission respects both enabled and explicitly disabled saved preferences', () => {
  const fixture = settingsFixture();
  fixture.render();
  fixture.state.permission = 'granted';
  let result = fixture.render();
  assert.match(result.desktop, /aria-checked="true"/);
  assert.doesNotMatch(result.desktop, /disabled=""/);
  assert.match(result.html, /桌面通知已开启/);
  fixture.state.preferences.notificationEnabled = false;
  result = fixture.render();
  assert.match(result.desktop, /aria-checked="false"/);
  assert.match(result.html, /桌面通知已关闭/);
  fixture.state.permission = 'denied';
  fixture.render();
  fixture.state.permission = 'granted';
  assert.match(fixture.render().desktop, /aria-checked="false"/);
  assert.equal(fixture.changes.length, 0);
});
