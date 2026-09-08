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
const selects = ['code', '', '__ignored__'].map((value, index) => {
  const groups = Object.fromEntries(['suggested', 'available', 'used'].map(key => [key, {
    dataset:{optionGroup:key}, children:[],
    append(option) {
      if (option.parent) option.parent.children.splice(option.parent.children.indexOf(option), 1);
      this.children.push(option); option.parent = this;
    },
  }]));
  const options = ['code', 'amount', 'active'].map((key, order) => ({value:key,
    dataset:{optionLabel:key, optionOrder:String(order), optionGroup:order===0?'suggested':'available'},
  }));
  options.forEach(option => groups[option.dataset.optionGroup].append(option));
  return element({value, groups, options,
    querySelectorAll: selector => selector.startsWith('option[') ? options : Object.values(groups),
    closest: () => ({querySelector: () => badges[index]})});
});
const summary = element();
const missingLabels = ['code', 'amount'].map(key => element({dataset:{missingKey:key}}));
const missing = element({querySelectorAll: () => missingLabels});
const advance = element({disabled:false});
const status = element();
const mapping = element({dataset:{requiredKeys:'["code","amount"]'},
  querySelectorAll: () => selects, querySelector: selector => ({"[data-mapping-status]":status, "[data-mapping-summary]":summary, "[data-mapping-missing]":missing})[selector]});
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
    return ({'[data-mapping-tooltip]':[tip], '[data-import-mapping]':[mapping], '[data-import-confirm]':[confirm],
      'dialog[data-modal-report]':[dialog], '[data-dialog-open]':[opener],
      '[data-dialog-close]':[close]})[selector] || [];
  },
  querySelector: selector => selector === '[data-mapping-continue]' ? advance : null,
  getElementById: id => id === 'report' ? dialog : null,
  addEventListener(type, callback) { (this.handlers[type] ||= []).push(callback); },
};
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), {document,window:{innerWidth:320,innerHeight:480,addEventListener(){}}});
assert.equal(advance.disabled,true);
assert.equal(badges[0].textContent, 'Mapped');
assert.equal(selects[0].value, 'code');
assert.equal(selects[0].options[0].parent, selects[0].groups.suggested);
assert.equal(selects[1].options[0].parent, selects[1].groups.used);
assert.equal(selects[1].groups.suggested.hidden, true);
assert.equal(badges[1].textContent, 'Needs a choice');
assert.equal(badges[2].textContent, 'Do not import');
assert.equal(missing.hidden, false);
assert.equal(missingLabels[0].hidden, true);
assert.equal(missingLabels[1].hidden, false);
assert.match(summary.textContent, /1 of 2 required fields mapped · 1 choices remaining · 1 columns ignored/);
selects[1].value='amount'; mapping.handlers.change();
assert.equal(advance.disabled,false);
assert.equal(missing.hidden, true);
selects[2].value='amount'; mapping.handlers.change();
assert.equal(badges[1].textContent, 'Mapping error');
assert.match(badges[2].className, /badge-error/);
assert.equal(advance.disabled,true);
selects[2].value='__ignored__'; selects[0].value=''; mapping.handlers.change();
assert.equal(selects[1].options[0].parent, selects[1].groups.suggested);
assert.equal(selects[1].groups.suggested.hidden, false);
assert.equal(selects[1].value, 'amount');
assert.equal(selects[2].value, '__ignored__');
assert.equal(selects[2].options[1].parent, selects[2].groups.used);
assert.equal(advance.disabled,true);
selects[0].value='code'; mapping.handlers.change();
assert.equal(advance.disabled,false);
assert.match(status.textContent,/Ready to continue/);
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
