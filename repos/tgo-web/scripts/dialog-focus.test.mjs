import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function fixture() {
  const listeners = new Map();
  const document = { activeElement: null,
    addEventListener: (type, callback) => { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(callback); },
    removeEventListener: (type, callback) => listeners.get(type)?.delete(callback) };
  let order = 0;
  class Element {
    constructor({ hidden = false, disabled = false, tabIndex = 0 } = {}) {
      this.hidden = hidden; this.disabled = disabled; this.tabIndex = tabIndex;
      this.isConnected = true; this.order = order++; this.children = []; this.attributes = new Map();
    }
    append(...children) { children.forEach(child => { child.parent = this; this.children.push(child); }); return this; }
    contains(element) { return element === this || this.children.some(child => child.contains(element)); }
    compareDocumentPosition(element) { return this.order < element.order ? 4 : 2; }
    querySelectorAll() { return this.children.flatMap(child => [child, ...child.querySelectorAll()]); }
    getClientRects() { return this.hidden ? [] : [{}]; }
    matches(selector) { return selector === ':disabled' && this.disabled; }
    closest() { return this.inert || this.parent?.inert ? this : null; }
    getAttribute(name) { return this.attributes.get(name) ?? null; }
    hasAttribute(name) { return this.attributes.has(name); }
    setAttribute(name, value) { this.attributes.set(name, value); if (name === 'tabindex') this.tabIndex = Number(value); }
    removeAttribute(name) { this.attributes.delete(name); }
    focus() { document.activeElement = this; emit('focusin', { target: this }); }
  }
  const emit = (type, event) => { for (const handler of [...(listeners.get(type) ?? [])]) handler(event); };
  let activeHooks;
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/hooks/useDialogFocus.ts', import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, { exports, document, HTMLElement: Element, Node: { DOCUMENT_POSITION_FOLLOWING: 4 },
    window: { getComputedStyle: element => ({ visibility: element.visibility ?? 'visible' }) },
    require: name => {
      assert.equal(name, 'react');
      return { useRef: initial => {
        const index = activeHooks.refIndex++;
        return activeHooks.refs[index] ??= { current: initial };
      }, useEffect: (callback, deps) => {
        const index = activeHooks.effectIndex++;
        const previous = activeHooks.effects[index];
        if (!previous || deps.some((value, key) => value !== previous.deps[key])) {
          activeHooks.queue.push(() => { previous?.cleanup?.(); activeHooks.effects[index] = { deps, cleanup: callback() }; });
        }
      } };
    } });
  const mount = (element, close = () => {}) => {
    const hooks = { refs: [], effects: [], queue: [], refIndex: 0, effectIndex: 0 };
    const render = (callback = close, enabled = true) => {
      activeHooks = hooks; hooks.refIndex = 0; hooks.effectIndex = 0;
      exports.useDialogFocus(callback, enabled).current = element;
      hooks.queue.splice(0).forEach(effect => effect());
    };
    render();
    return { render, unmount: () => hooks.effects.forEach(effect => effect.cleanup?.()) };
  };
  const key = (value, shiftKey = false) => {
    const event = { key: value, shiftKey, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
    emit('keydown', event); return event;
  };
  return { document, Element, mount, key, listeners };
}

test('Tab wraps using only visible enabled controls, including negative tabindex exclusions', () => {
  const h = fixture();
  const first = new h.Element(); const last = new h.Element();
  const dialog = new h.Element().append(first, last, new h.Element({ hidden: true }), new h.Element({ disabled: true }), new h.Element({ tabIndex: -1 }));
  h.mount(dialog); last.focus();
  assert.equal(h.key('Tab').defaultPrevented, true);
  assert.equal(h.document.activeElement, first);
  h.key('Tab', true); assert.equal(h.document.activeElement, last);
});

test('Escape and Tab belong only to the top dialog; child close restores its opener', () => {
  const h = fixture(); let parentClosed = 0; let childClosed = 0;
  const opener = new h.Element(); const parent = new h.Element().append(opener);
  h.mount(parent, () => parentClosed++);
  const first = new h.Element(); const last = new h.Element();
  const child = new h.Element().append(first, last);
  const nested = h.mount(child, () => childClosed++);
  h.key('Escape'); assert.equal(childClosed, 1); assert.equal(parentClosed, 0);
  last.focus(); h.key('Tab'); assert.equal(h.document.activeElement, first);
  child.isConnected = false; nested.unmount(); assert.equal(h.document.activeElement, opener);
});

test('an empty or fully disabled dialog keeps focus on its container', () => {
  const h = fixture(); const dialog = new h.Element().append(new h.Element({ disabled: true }));
  h.mount(dialog); assert.equal(h.document.activeElement, dialog);
  assert.equal(dialog.getAttribute('tabindex'), '-1');
  assert.equal(h.key('Tab').defaultPrevented, true);
});

test('background focus cannot escape a dialog, and callback updates do not reset entered focus', () => {
  const h = fixture(); const first = new h.Element(); const last = new h.Element();
  const dialog = new h.Element().append(first, last); let oldClose = 0; let newClose = 0;
  const mounted = h.mount(dialog, () => oldClose++); last.focus();
  mounted.render(() => newClose++); assert.equal(h.document.activeElement, last);
  h.key('Escape'); assert.equal(newClose, 1); assert.equal(oldClose, 0);
  new h.Element().focus(); assert.equal(h.document.activeElement, first);
});

test('disposing an underlying dialog must not steal focus from another active dialog', () => {
  const h = fixture(); const trigger = new h.Element(); trigger.focus();
  const first = h.mount(new h.Element().append(new h.Element()));
  const childControl = new h.Element(); h.mount(new h.Element().append(childControl));
  first.unmount(); assert.equal(h.document.activeElement, childControl);
});

test('closing restores a connected opener and removes all listeners and temporary tabindex', () => {
  const h = fixture(); const trigger = new h.Element(); trigger.focus(); const dialog = new h.Element();
  const mounted = h.mount(dialog); mounted.unmount();
  assert.equal(h.document.activeElement, trigger);
  assert.equal(dialog.getAttribute('tabindex'), null);
  assert.equal([...h.listeners.values()].reduce((sum, values) => sum + values.size, 0), 0);
});

test('a child mounted before its parent retains ownership, including its initial safe choice', () => {
  const h = fixture(); const primary = new h.Element(); primary.setAttribute('data-dialog-initial-focus', '');
  const child = new h.Element().append(new h.Element(), primary);
  const parent = new h.Element().append(new h.Element(), child); let childClosed = 0; let parentClosed = 0;
  h.mount(child, () => childClosed++); h.mount(parent, () => parentClosed++);
  assert.equal(h.document.activeElement, primary);
  h.key('Escape'); assert.equal(childClosed, 1); assert.equal(parentClosed, 0);
});

test('disconnected underlying dialogs do not restore focus into a surviving dialog', () => {
  const h = fixture(); const parent = new h.Element().append(new h.Element());
  const first = h.mount(parent); const selected = new h.Element(); h.mount(new h.Element().append(new h.Element(), selected));
  selected.focus(); parent.isConnected = false; first.unmount();
  assert.equal(h.document.activeElement, selected);
});
