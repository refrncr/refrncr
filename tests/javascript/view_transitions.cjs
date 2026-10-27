// Exercise promise outcomes through the controller, including errors that must escape.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[2], 'utf8');
function transition() {
  let reject;
  const ready = new Promise((resolve, fail) => { reject = fail; });
  const result = {skips: 0, observers: 0, reject, skipTransition() { this.skips++; }};
  result.ready = {catch(fn) { result.observers++; result.handled = ready.catch(fn); }};
  return result;
}
function surface() {
  const listeners = {};
  return {
    addEventListener(name, fn) { (listeners[name] ||= []).push(fn); },
    fire(name, event = {}) { for (const fn of listeners[name] || []) fn(event); },
  };
}
function page({active, reduced = false} = {}) {
  const document = Object.assign(surface(), {activeViewTransition: active, hidden: false});
  const window = Object.assign(surface(), {matchMedia: query => {
    assert.equal(query, '(prefers-reduced-motion: reduce)'); return {matches: reduced};
  }});
  vm.runInNewContext(source, {document, window});
  return {document, window};
}
const failure = (name, message) => Object.assign(new Error(message), {name});
(async () => {
  const incoming = transition();
  const p = page({active: incoming});
  assert.equal(incoming.observers, 1, 'incoming ready is observed before pagereveal');
  p.window.fire('pagereveal', {viewTransition: incoming});
  assert.equal(incoming.observers, 1, 'a transition is observed once');
  p.window.fire('pagereveal', {viewTransition: null});
  incoming.reject(failure('InvalidStateError', 'Transition was aborted because of invalid state. ViewTransition opt-in disabled'));
  await incoming.handled;
  assert.equal(incoming.skips, 0, 'normal navigation keeps the no-flash transition');

  const outgoing = transition();
  p.window.fire('pageswap', {viewTransition: outgoing});
  outgoing.reject(failure('AbortError', 'Transition was skipped'));
  await outgoing.handled;
  assert.equal(outgoing.skips, 0);

  const hidden = transition();
  p.window.fire('pagereveal', {viewTransition: hidden});
  p.document.hidden = true;
  hidden.reject(failure('AbortError', 'Transition was skipped'));
  await hidden.handled;
  p.document.hidden = false;

  for (const error of [failure('InvalidStateError', 'Duplicate view-transition-name'),
    failure('AbortError', 'Unexpected incoming abort'), new Error('Application error')]) {
    const broken = transition();
    p.window.fire('pagereveal', {viewTransition: broken});
    broken.reject(error);
    await assert.rejects(broken.handled, value => value === error);
  }
  for (const eventName of ['click', 'submit']) {
    const element = {hasAttribute: name => name === 'data-no-view-transition'};
    element.closest = () => element;
    p.document.fire(eventName, {target: element});
    const skipped = transition();
    p.window.fire('pageswap', {viewTransition: skipped});
    assert.equal(skipped.skips, 1, `${eventName} opts out before a non-HTML/sensitive destination`);
    skipped.reject(failure('AbortError', 'Transition was skipped'));
    await skipped.handled;
    p.window.fire('pageshow');
    const next = transition();
    p.window.fire('pageswap', {viewTransition: next});
    assert.equal(next.skips, 0, 'history restore resets the opt-out');
  }
  const reduced = transition();
  page({active: reduced, reduced: true});
  assert.equal(reduced.skips, 1);
  reduced.reject(failure('AbortError', 'Transition was skipped'));
  await reduced.handled;
  const unsupported = page();
  unsupported.window.fire('pageswap', {});
  unsupported.window.fire('pagereveal', {});
  console.log('View transitions: early capture, expected cancellation, explicit opt-out, reduced motion, fallback and visible unexpected errors');
})().catch(error => { console.error(error); process.exitCode = 1; });
