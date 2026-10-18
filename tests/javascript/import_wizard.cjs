const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
function element(extra = {}) {
  return Object.assign({
    handlers: {}, attributes: {}, isConnected: true,
    addEventListener(type, callback) { this.handlers[type] = callback; },
    setAttribute(name, value) { this.attributes[name] = value; },
    getAttribute(name) { return this.attributes[name]; },
    hasAttribute(name) { return name in this.attributes; },
    closest() { return null; }, getClientRects() { return [{}]; },
    matches() { return false; }, focus() { document.activeElement = this; },
  }, extra);
}
const badges = [element(), element(), element()];
const labels = {code:'Code', amount:'Amount', active:'Active', subgroup:'Subgroup', subgroup_range:'Subgroup range', function:'Function'};
const requiredKeys = ['code','amount'];
const selects = ['code', '', '__ignored__'].map((value, index) => {
  const options = Object.entries(labels).map(([key, name], order) => ({value:key,
    hidden:false, dataset:{optionLabel:name, optionOrder:String(order), optionGroup:order===0?'suggested':'available'},
  }));
  return element({value, options,
    querySelectorAll: selector => selector.startsWith('option[') ? options : [],
    closest: () => ({querySelector: () => badges[index]})});
});
const advance = element({disabled:false});
const statusText = element();
const status = element({dataset:{},querySelector:selector => selector === '[data-mapping-status-text]' ? statusText : null});
const reason = element({hidden:true});
const mapping = element({dataset:{requiredKeys:'["code","amount"]'},
  querySelectorAll: () => selects, querySelector: selector => ({"[data-mapping-status]":status, "[data-mapping-reason]":reason})[selector]});
const submit = element({type:'submit',disabled:false});
const confirm = element({elements:[submit]});
const close = element();
const scrollArea = element();
const dialog = element({attributes:{'data-modal-report':''},
  querySelectorAll: () => [close,scrollArea],
  showModal() { this.open = true; close.focus(); },
  close() { this.open = false; this.handlers.close(); },
});
close.closest = selector => selector === "dialog" ? dialog : null;
const opener = element({dataset:{dialogOpen:'report'}});
const tipTrigger = element({getBoundingClientRect: () => ({left:300,bottom:150})});
const tipBody = element({style:{},getBoundingClientRect: () => ({width:250,height:80})});
const tip = element({dataset:{},open:false,
  querySelector: selector => selector === 'summary' ? tipTrigger : tipBody,
  contains: target => target === tipTrigger || target === tipBody,
});
const document = {
  handlers:{},
  activeElement:null,
  querySelectorAll(selector) {
    return ({'[data-mapping-tooltip]':[tip], '[data-import-mapping]':[mapping], '[data-import-confirm], [data-single-submit]':[confirm],
      'dialog[data-modal-report]':[dialog],
      'dialog[data-modal-report], dialog[data-modal-side-panel]':[dialog],
      '[data-dialog-open]':[opener],
      '[data-dialog-close]':[close]})[selector] || [];
  },
  querySelector: selector => selector === '[data-mapping-continue]' ? advance : null,
  getElementById: id => id === 'report' ? dialog : null,
  addEventListener(type, callback) { (this.handlers[type] ||= []).push(callback); },
};
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), {document,JSON:{parse:() => requiredKeys},window:{innerWidth:320,innerHeight:480,addEventListener(){}}});
assert.equal(advance.disabled,true);
assert.equal(badges[0].textContent, 'Mapped');
assert.equal(selects[0].value, 'code');
assert.equal(selects[0].options[0].hidden, false);
assert.equal(selects[1].options[0].hidden, true);
assert.equal(badges[1].textContent, 'Needs a choice');
assert.equal(badges[2].textContent, 'Do not import');
assert.equal(statusText.textContent, '1 column still needs a choice · 1 field not mapped: Amount');
assert.equal(status.dataset.state, 'pending');
assert.equal(reason.hidden, false);
assert.equal(status.hidden, undefined);
selects[1].value='amount'; mapping.handlers.change();
assert.equal(advance.disabled,false);
assert.equal(statusText.textContent, 'Ready to continue · 2 fields mapped, 1 column not imported');
assert.equal(status.dataset.state, 'ready');
assert.equal(reason.hidden,true);
selects[0].value=''; selects[2].value=''; mapping.handlers.change();
assert.equal(statusText.textContent, '2 columns still need a choice · 1 field not mapped: Code');
assert.equal(status.dataset.state, 'pending');
selects[0].value='__ignored__'; selects[1].value='__ignored__'; selects[2].value='__ignored__';
requiredKeys.push('active'); mapping.handlers.change();
assert.equal(statusText.textContent, '3 fields not mapped: Code, Amount, Active');
assert.equal(reason.hidden,false);
requiredKeys.pop(); selects[0].value='code'; selects[1].value='amount'; selects[2].value='__ignored__'; mapping.handlers.change();
selects[2].value='amount'; mapping.handlers.change();
assert.equal(badges[1].textContent, 'Mapping error');
assert.match(badges[2].className, /badge-error/);
assert.equal(advance.disabled,true);
selects[2].value='__ignored__'; selects[0].value=''; mapping.handlers.change();
assert.equal(selects[1].options[0].hidden, false);
assert.equal(selects[1].value, 'amount');
assert.equal(selects[2].value, '__ignored__');
assert.equal(selects[2].options[1].hidden, true);
assert.equal(advance.disabled,true);
selects[0].value='code'; mapping.handlers.change();
assert.equal(advance.disabled,false);
assert.equal(statusText.textContent, 'Ready to continue · 2 fields mapped, 1 column not imported');
tip.handlers.mouseenter();
assert.equal(tip.open,true);
assert.equal(tipBody.style.left,'62px');
tip.handlers.mouseleave(); assert.equal(tip.open,false);
tipTrigger.focus(); tipTrigger.handlers.focus();
assert.equal(tip.open,true);
tip.handlers.mouseleave(); assert.equal(tip.open,true);
document.handlers.keydown.forEach(handler => handler({key:'Escape'}));
assert.equal(tip.open,false);
tipTrigger.handlers.click({preventDefault(){}}); assert.equal(tip.open,true);
document.handlers.pointerdown.forEach(handler => handler({target:{}}));
assert.equal(tip.open,false);
tipTrigger.handlers.focus();
tip.handlers.focusout({relatedTarget:tipBody}); assert.equal(tip.open,true);
tipBody.focus();
document.handlers.keydown.forEach(handler => handler({key:'Escape'}));
assert.equal(document.activeElement,tipTrigger); assert.equal(tip.open,false);
tipTrigger.handlers.focus(); tip.handlers.focusout({relatedTarget:null}); assert.equal(tip.open,false);
let prevented=false;
confirm.handlers.submit({preventDefault(){prevented=true;}});
assert.equal(prevented,false); assert.equal(submit.disabled,true);
assert.equal(confirm.attributes['aria-busy'],'true');
confirm.handlers.submit({preventDefault(){prevented=true;}});
assert.equal(prevented,true);
opener.focus(); opener.handlers.click({});
assert.equal(dialog.open,true); assert.equal(document.activeElement,close);
prevented=false;
dialog.handlers.keydown({key:'Tab',shiftKey:true,preventDefault(){prevented=true;}});
assert.equal(prevented,true); assert.equal(document.activeElement,scrollArea);
dialog.handlers.keydown({key:'Tab',shiftKey:false,preventDefault(){}});
assert.equal(document.activeElement,close);
close.handlers.click(); assert.equal(dialog.open,false);
assert.equal(document.activeElement,opener);
opener.handlers.click({});
// Native Escape emits close: the controller restores the same opener.
dialog.close(); assert.equal(document.activeElement,opener);
console.log('Mapping, single submission, modal focus wrapping and restoration passed');
