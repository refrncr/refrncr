// Controller tests use a small DOM double; native browser modality is a separate manual check.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
class Element {
  constructor(attrs = {}) { this.attrs = attrs; this.listeners = {}; this.isConnected = true; this.disabled = false; }
  get dataset() { return Object.fromEntries(Object.entries(this.attrs).filter(([k]) => k.startsWith('data-')).map(([k,v]) => [k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase()), v])); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  hasAttribute(k) { return k in this.attrs; }
  setAttribute(k,v) { this.attrs[k] = v; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(k,fn) { (this.listeners[k] ||= []).push(fn); }
  async fire(k, event = {}) { for (const fn of this.listeners[k] || []) await fn({target:this, preventDefault(){this.prevented = true;}, ...event}); }
  focus() { document.activeElement = this; }
  closest(selector) { if (selector === '[hidden]') return this.hidden ? this : null; if (selector === 'dialog') return this.dialog; return null; }
  matches() { return false; }
  getClientRects() { return this.hidden ? [] : [{}]; }
  querySelectorAll() { return []; }
  querySelector() { return null; }
}
class Dialog extends Element {
  constructor(id) {
    super({'data-modal-side-panel':'', 'aria-labelledby':`${id}-title`});
    this.id = id; this.open = false; this.title = new Element();
    this.closeButton = new Element({'data-dialog-close':''}); this.closeButton.dialog = this;
    this.input = new Element(); this.hiddenInput = new Element(); this.hiddenInput.hidden = true;
    this.host = new Element({'data-panel-host':''}); this.host.dialog = this;
    this.host.replaceChildren = (...children) => { this.children = children; this.candidates = children[0]?.candidates || []; };
    this.candidates = [this.hiddenInput, this.input]; this.modalCalls = 0;
  }
  showModal() { this.open = true; this.modalCalls++; }
  close() { this.open = false; return this.fire('close'); }
  querySelector(selector) { return selector === '[data-dialog-close]' ? this.closeButton : selector === '[data-panel-host]' ? this.host : null; }
  querySelectorAll(selector) { return selector.startsWith('[data-error-focus]') ? this.candidates : []; }
}
const first = new Dialog('first'), second = new Dialog('second');
const opener = (id) => new Element({'data-dialog-open':id});
const a = opener('first'), b = opener('first'), c = opener('second');
a.setAttribute('data-row-primary', '');
let rowTextSelected = false;
const remote = opener('second'); remote.setAttribute('data-panel-url',''); remote.href = 'https://localhost/form';
const main = new Element();
const datatype = new Element({'data-field-datatype':''});
const identifier = new Element(); identifier.checked = true;
datatype.selectedOptions = [{dataset:{identifierEligible:'true'}}];
datatype.closest = s => s === 'form' ? {querySelector:()=>identifier} : null;
const selector = new Element(); selector.value = '';
const grantButton = new Element(); grantButton.matches = () => true;
const grantForm = new Element({'data-requires-selection':'', 'data-panel-refresh':'/export-access', 'data-panel-success':'Export access granted.'});
grantForm.action = 'https://localhost/export-access'; grantForm.elements = [grantButton];
grantForm.querySelector = s => s === 'select[required]' ? selector : s === "button[type='submit']" ? grantButton : null;
grantForm.prepend = e => { grantForm.error = e; };
const nodes = {first, second, 'first-title':first.title, 'second-title':second.title, content:main};
const document = {
  activeElement: null,
  querySelectorAll(selector) {
    return ({'[data-field-datatype]':[datatype], 'form[data-requires-selection]':[grantForm], 'dialog[data-modal-side-panel]':[first,second], '[data-dialog-open]':[a,b,c,remote], '[data-dialog-close]':[first.closeButton,second.closeButton], '[data-panel-host]':[second.host]})[selector] || [];
  },
  querySelector(selector) { return selector === 'dialog[data-modal-side-panel][open]' ? [first,second].find(d => d.open) : null; },
  getElementById(id) { return nodes[id]; }, addEventListener() {}, importNode(node) { return node; }, createElement(tag) { const e = new Element(); e.tagName = tag.toUpperCase(); return e; },
};
let fetchImpl;
const requests = [], destinations = [];
const context = {document, window:{getSelection:()=>({isCollapsed:!rowTextSelected}),location:{assign(url){destinations.push(url);}}},
  URL,
  fetch:async (...args) => {requests.push(args); return fetchImpl(...args);},
  DOMParser:class {parseFromString(page) { return {querySelector(){return page;}};}},
  FormData:class {constructor(form,submitter) {this.form=form;this.submitter=submitter;}},
};
const source = fs.readFileSync(process.argv[2], 'utf8');
vm.runInNewContext(source, context);
vm.runInNewContext(source, context);
const settle = async () => { for(let i=0;i<15;i++) await Promise.resolve(); };
const response = (content, status=200) => ({ok:status===200,status,redirected:false,text:async()=>content});
(async()=>{
  assert.equal(identifier.disabled,false);
  assert.equal(identifier.checked,true);
  datatype.selectedOptions = [{dataset:{identifierEligible:'false'}}];
  await datatype.fire('change');
  assert.equal(identifier.disabled,true,'Float cannot be selected as an identifier');
  assert.equal(identifier.checked,false,'changing to Float clears membership');
  datatype.selectedOptions = [{dataset:{identifierEligible:'true'}}];
  await datatype.fire('change');
  assert.equal(identifier.disabled,false,'eligible types re-enable membership');
  assert.equal(identifier.checked,false,'membership is never silently restored');
  assert.equal(datatype.listeners.change.length,1,'datatype initialization is idempotent');
  assert.equal(grantButton.disabled,true,'enhanced selection form starts disabled');
  selector.value='user:example'; await selector.fire('change'); assert.equal(grantButton.disabled,false);
  grantForm.setAttribute('aria-busy','true'); await selector.fire('change'); assert.equal(grantButton.disabled,true);
  grantForm.removeAttribute('aria-busy'); selector.value=''; await selector.fire('change'); assert.equal(grantButton.disabled,true);
  assert.equal(selector.listeners.change.length,1,'selection initialization is idempotent');
  assert.equal(a.listeners.click.length, 1, 'controller registration is idempotent');
  rowTextSelected = true;
  await a.fire('click', {detail:1});
  assert.equal(first.modalCalls,0, 'selecting primary button text does not open a panel');
  rowTextSelected = false;
  await a.fire('click', {defaultPrevented:true});
  await a.fire('click', {ctrlKey:true});
  assert.equal(first.modalCalls,0, 'handled or modified row events do not open a panel');
  await a.fire('click', {detail:0});
  assert.equal(first.modalCalls,1, 'uses showModal, which supplies native inertness and focus containment');
  assert.equal(document.activeElement,first.input,'focus skips hidden controls and close button');
  await c.fire('click'); assert.equal(second.open,false,'another modal cannot open over this panel');
  await first.closeButton.fire('click'); assert.equal(first.open,false); assert.equal(document.activeElement,a);
  await b.fire('click');
  // Native Escape closes the dialog and dispatches close; no custom key handler cancels it.
  assert.equal(first.listeners.cancel,undefined); assert.equal(first.listeners.keydown,undefined);
  await first.close(); assert.equal(document.activeElement,b,'restores the particular opener');
  first.candidates=[]; await a.fire('click'); assert.equal(document.activeElement,first.closeButton); await first.close();
  await c.fire('click'); assert.equal(document.activeElement,second.input); await second.close(); assert.equal(document.activeElement,c);
  const field = new Element(); const content = {dataset:{panelTitle:'Edit field'},candidates:[field]};
  const loadedType = new Element({'data-field-datatype':''});
  const loadedIdentifier = new Element(); loadedIdentifier.checked=true;
  loadedType.selectedOptions = [{dataset:{identifierEligible:'false'}}];
  loadedType.closest = s => s === 'form' ? {querySelector:()=>loadedIdentifier} : null;
  second.host.querySelectorAll = s => s === '[data-field-datatype]' ? [loadedType] : [];
  fetchImpl=async()=>response(content);
  let prevented=false;
  await remote.fire('click',{metaKey:true,preventDefault(){prevented=true;}});
  assert.equal(prevented,false); assert.equal(second.open,false,'modified clicks preserve normal links');
  await remote.fire('click',{preventDefault(){prevented=true;}}); await settle();
  assert.equal(prevented,true); assert.equal(second.title.textContent,'Edit field'); assert.equal(document.activeElement,field);
  assert.equal(loadedIdentifier.disabled,true,'loaded Field panels initialize datatype controls');
  assert.equal(loadedIdentifier.checked,false);
  const cancel=new Element(); cancel.closest=(selector)=>selector==='[data-panel-cancel]'?cancel:null;
  await second.host.fire('click',{target:cancel}); assert.equal(document.activeElement,remote); assert.equal(second.open,false);
  await remote.fire('click'); await settle();
  const error=new Element({'data-error-focus':''});
  const invalid=new Element({'aria-invalid':'true'});
  fetchImpl=async()=>response({dataset:{panelTitle:'Edit field'},candidates:[invalid,error]},400);
  const form=new Element(); form.action='https://localhost/fields/code'; form.prepend=(e)=>{form.error=e;};
  const submit=new Element(); form.elements=[submit]; submit.matches=()=>true;
  await second.host.fire('submit',{target:form,submitter:submit});
  assert.equal(document.activeElement,error,'server error summary takes focus before invalid field');
  assert.equal(requests.at(-1)[1].method,'POST');
  assert.equal(requests.at(-1)[1].body.form,form,'original form data includes its CSRF field');
  assert.equal(submit.disabled,false);
  fetchImpl=async()=>({redirected:true,url:'https://localhost/datasets/example'});
  await second.host.fire('submit',{target:form,submitter:submit});
  assert.equal(destinations.at(-1),'https://localhost/datasets/example','success preserves server redirect');
  fetchImpl=async()=>response(null,400);
  await second.host.fire('submit',{target:form,submitter:submit});
  assert.match(form.error.textContent,/could not be saved/); assert.equal(document.activeElement,form.error);
  fetchImpl=async()=>{throw new Error('network');};
  await second.host.fire('submit',{target:form,submitter:submit});
  assert.match(form.error.textContent,/could not be confirmed/);
  await second.close();
  let finish;
  fetchImpl=()=>new Promise(resolve=>{finish=resolve;});
  await remote.fire('click'); await second.close();
  finish(response(content)); await settle();
  assert.equal(second.open,false,'a late GET never reopens a closed panel');
  assert.equal(second.children.length,0,'a stale GET does not populate the host');
  fetchImpl=async()=>{throw new Error('network');};
  await remote.fire('click'); await settle();
  assert.equal(destinations.at(-1),remote.href,'GET failure falls back to the full page');
  await second.close();
  fetchImpl=async()=>response(content); await remote.fire('click'); await settle();
  const status = new Element({'data-panel-status':''}); status.hidden=true;
  second.host.querySelector = s => s === '[data-panel-status]' ? status : null;
  const heading = new Element({'data-panel-section-heading':''}); heading.tagName='H2';
  let nestedHeading; heading.replaceWith = e => { nestedHeading=e; };
  second.host.querySelectorAll = s => s.startsWith('[data-panel-section-heading]') ? [heading] : [];
  selector.value='user:example'; await selector.fire('change');
  const navigationCount = destinations.length;
  fetchImpl=async()=>({...response(content),redirected:true,url:grantForm.action});
  await second.host.fire('submit',{target:grantForm,submitter:grantButton});
  assert.equal(destinations.length,navigationCount,'opt-in same-page success keeps dialog context');
  assert.equal(second.open,true); assert.equal(status.hidden,false);
  assert.equal(status.textContent,'Export access granted.'); assert.equal(status.getAttribute('role'),'status');
  assert.equal(document.activeElement,status); assert.equal(nestedHeading.tagName,'H3');
  await second.close(); assert.equal(document.activeElement,remote,'refresh preserves original opener');
  fetchImpl=async()=>response(content); await remote.fire('click'); await settle();
  fetchImpl=async()=>({...response(content),redirected:true,url:'https://localhost/login'});
  await second.host.fire('submit',{target:grantForm,submitter:grantButton});
  assert.equal(destinations.at(-1),'https://localhost/login','authentication redirects are never imported');
  let completeMutation;
  fetchImpl=()=>new Promise(resolve=>{completeMutation=resolve;});
  const pending=second.host.fire('submit',{target:grantForm,submitter:grantButton});
  const pendingRequests=requests.length;
  await second.host.fire('submit',{target:grantForm,submitter:grantButton});
  assert.equal(requests.length,pendingRequests,'one request per pending panel');
  await second.close(); const closedDestinations=destinations.length;
  completeMutation({...response(content),redirected:true,url:grantForm.action}); await pending;
  assert.equal(destinations.length,closedDestinations,'closed opt-in panel ignores late success');
  assert.equal(second.open,false);
  console.log('Side-panel controller checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
