// Small DOM doubles exercise the controller; real layout/modality is reviewed in Chromium.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[2], 'utf8');
function page(width = 1440, storage = new Map(), blocked = false) {
  let document;
  class Element {
    constructor() { this.listeners = {}; this.attrs = {}; this.children = []; this.classes = new Set(); this.classList = {toggle:(key, value) => value ? this.classes.add(key) : this.classes.delete(key)}; }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    fire(type, event = {}) { event = {target:this, preventDefault(){this.prevented = true;}, ...event}; for (const fn of this.listeners[type] || []) fn(event); return event; }
    setAttribute(key, value) { this.attrs[key] = value; }
    focus() { document.activeElement = this; }
    contains(element) { return element === this || this.children.includes(element); }
    getClientRects() { return [{}]; }
    append(element) { element.parentNode = this; }
    insertBefore(element, next) { assert.equal(next, drawer); element.parentNode = this; }
    closest(selector) { return selector === 'a[href]' && this.link ? this : null; }
  }
  const root = new Element(), shell = new Element(), nav = new Element(), open = new Element(), close = new Element(), drawer = new Element(), link = new Element(), logout = new Element();
  link.link = true; nav.children = [link, close, logout];
  nav.querySelectorAll = () => nav.children;
  nav.querySelector = () => close;
  shell.querySelector = (selector) => ({'[data-navigation]':nav, '[data-nav-open]':open, '[data-navigation-drawer]':drawer})[selector];
  nav.parentNode = shell; nav.nextSibling = drawer;
  drawer.open = false; drawer.modalCalls = 0;
  drawer.showModal = () => { drawer.open = true; drawer.modalCalls++; };
  drawer.close = () => { drawer.open = false; drawer.fire('close'); };
  document = new Element(); document.documentElement = root;
  document.querySelector = () => shell;
  const media = new Element(); media.matches = width >= 1024;
  const window = Object.assign(new Element(), {matchMedia:query => {assert.equal(query, '(min-width: 1024px)'); return media;}, sessionStorage:{getItem:key => {if (blocked) throw Error('blocked'); return storage.get(key);}, setItem:(key, value) => {if (blocked) throw Error('blocked'); storage.set(key, value);}}});
  vm.runInNewContext(source, {document, window});
  const beforePaint = root.classes.has('navigation-collapsed');
  document.fire('DOMContentLoaded');
  return {root, shell, nav, open, close, drawer, link, logout, document, window, beforePaint, resize(width) {media.matches = width >= 1024; media.fire('change');}};
}
const state = new Map();
let p = page(1024, state);
assert.equal(p.beforePaint, false);
// The head script marks scripted pages before the first paint (section tabs rely on it).
assert(p.root.classes.has('js'));
assert.equal(p.close.attrs['aria-expanded'], 'true');
assert.equal(p.close.attrs['aria-label'], 'Collapse navigation');
assert.equal(p.nav.parentNode, p.shell);
assert.equal(p.drawer.modalCalls, 0);
p.close.fire('click');
assert.equal(p.root.classes.has('navigation-collapsed'), true);
assert.equal(p.open.attrs['aria-expanded'], 'false');
assert.equal(p.open.title, 'Expand navigation');
assert.equal(p.document.activeElement, p.open);
p = page(1440, state);
assert.equal(p.beforePaint, true, 'stored collapse is applied before DOMContentLoaded');
assert.equal(p.close.attrs['aria-expanded'], 'false');
p.open.fire('click');
assert.equal(p.document.activeElement, p.close);
assert.equal(p.close.attrs['aria-expanded'], 'true');
assert.equal(p.drawer.modalCalls, 0, 'desktop never uses modal focus containment');
assert.equal(page(1024, state).beforePaint, false, 'expanded choice also survives navigation');
assert.equal(page().beforePaint, false, 'fresh session defaults to expanded');
const cached = page(1440, state);
p.close.fire('click'); cached.window.fire('pageshow', {persisted:true});
assert.equal(cached.open.attrs['aria-expanded'], 'false', 'back/forward cache reads the current session choice');
assert.equal(cached.root.classes.has('navigation-collapsed'), true);
for (const width of [320, 767, 768, 1023]) {
  for (const stored of ['expanded', 'collapsed']) {
    const storage = new Map([['refrncr.navigation.desktop', stored]]);
    const p = page(width, storage);
    assert.equal(p.nav.parentNode, p.drawer);
    assert.equal(p.drawer.open, false);
    assert.equal(p.open.attrs['aria-expanded'], 'false');
    assert.equal(p.open.attrs['aria-label'], 'Open navigation');
    assert.equal(p.close.title, 'Close navigation');
    p.open.fire('click');
    assert.equal(p.drawer.open, true);
    assert.equal(p.document.activeElement, p.close);
    assert.equal(p.open.attrs['aria-expanded'], 'true');
    p.logout.focus();
    assert.equal(p.drawer.fire('keydown', {key:'Tab'}).prevented, true);
    assert.equal(p.document.activeElement, p.link);
    assert.equal(p.drawer.fire('keydown', {key:'Tab', shiftKey:true}).prevented, true);
    assert.equal(p.document.activeElement, p.logout);
    assert.equal(p.drawer.fire('cancel').prevented, true);
    assert.equal(p.drawer.open, false);
    assert.equal(p.document.activeElement, p.open);
    p.open.fire('click'); p.close.fire('click');
    assert.equal(p.document.activeElement, p.open);
    p.open.fire('click');
    p.drawer.fire('pointerdown', {target:p.close}); p.drawer.fire('click');
    assert.equal(p.drawer.open, true, 'dragging from drawer to backdrop does not close');
    p.drawer.fire('pointerdown'); p.drawer.fire('click');
    assert.equal(p.drawer.open, false);
    assert.equal(p.document.activeElement, p.open);
    p.open.fire('click'); p.nav.fire('click', {target:p.link});
    assert.equal(p.drawer.open, false, 'navigation closes the drawer');
    p.open.fire('click'); p.resize(1024);
    assert.equal(p.drawer.open, false);
    assert.equal(p.root.classes.has('navigation-drawer-open'), false);
    assert.equal(p.nav.parentNode, p.shell);
    assert.equal(p.close.attrs['aria-expanded'], String(stored === 'expanded'));
    assert.equal(p.document.activeElement, stored === 'expanded' ? p.close : p.open);
    p.resize(width);
    assert.equal(p.drawer.open, false);
    assert.equal(p.document.activeElement, p.open);
    assert.equal(storage.get('refrncr.navigation.desktop'), stored, 'drawer never overwrites desktop choice');
  }
}
p = page(1440, new Map(), true);
p.close.fire('click'); assert.equal(p.open.attrs['aria-expanded'], 'false');
p.open.fire('click'); assert.equal(p.close.attrs['aria-expanded'], 'true');
console.log('Navigation state, storage, breakpoint and focus controller checks passed');
