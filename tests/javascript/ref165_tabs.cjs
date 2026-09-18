// No browser mocks for layout: this harness only exercises transactional tab setup.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../src/refrncr/static/js/section-tabs.js'), 'utf8');
class Element extends EventTarget {
  constructor(id) { super(); this.id = id; this.attrs = {}; this.hidden = false; }
  setAttribute(name, value) { this.attrs[name] = value; }
  getAttribute(name) { return this.attrs[name] ?? null; }
  removeAttribute(name) { delete this.attrs[name]; }
  contains(target) { return target === this; }
  focus() {}
  scrollIntoView() {}
}
for (const failure of ['missing-panel', 'during-activation', null]) {
  const panels = [new Element('one'), new Element('two')];
  panels.forEach(p=>p.setAttribute('aria-labelledby',p.id+'-heading'));
  const tabs = panels.map(p => { const t = new Element(); t.dataset = {tabTarget:p.id}; return t; });
  const nav = new Element(); nav.querySelectorAll = () => tabs;
  const document = {querySelectorAll:()=>[nav], getElementById:id =>
    failure === 'missing-panel' && id === 'two' ? null : panels.find(p=>p.id===id)};
  let injected = false, hidden = false;
  if (failure === 'during-activation') Object.defineProperty(panels[1], 'hidden', {
    get:()=>hidden, set(value) { if (!injected) { injected=true; throw Error('setup failed'); } hidden=value; },
  });
  const window = new EventTarget();
  vm.runInNewContext(source, {window, document, AbortController, URL, location:{hash:'#two',href:'https://localhost/#two'}});
  if (failure) {
    assert(panels.every(p=>!p.hidden), `${failure}: all sections remain visible`);
    assert.equal(nav.getAttribute('role'), null);
    assert.deepEqual(panels.map(p=>p.getAttribute('aria-labelledby')), ['one-heading','two-heading']);
    const click = new Event('click', {cancelable:true}); tabs[0].dispatchEvent(click);
    assert.equal(click.defaultPrevented, false, 'failed enhancement leaves normal anchor navigation');
    if (failure === 'during-activation') {
      window.refrncrSectionTabs();
      assert.deepEqual(panels.map(p=>p.hidden), [true,false], 'a failed setup can retry');
    }
  } else {
    assert.deepEqual(panels.map(p=>p.hidden), [true,false]);
    assert.equal(nav.getAttribute('role'), 'tablist');
    window.refrncrSectionTabs();
    assert.deepEqual(panels.map(p=>p.hidden), [true,false]);
  }
}
console.log('PASS: missing target, partial initialization rollback, plain anchors, retry and normal tabs');
