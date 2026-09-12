// Dependency-free controller checks; rendered replacement/layout is covered in Chrome.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const document = {activeElement:null, querySelectorAll:()=>[], querySelector:()=>null, addEventListener(){}};
function node(extra={}) {
  return Object.assign({dataset:{},handlers:{},attributes:{},
    addEventListener(name, fn){(this.handlers[name] ||= []).push(fn);},
    setAttribute(name,value){this.attributes[name]=value;},
    getAttribute(name){return this.attributes[name];},
    hasAttribute(name){return name in this.attributes;},
    removeAttribute(name){delete this.attributes[name];},
    focus(){document.activeElement=this;},
    querySelector(){return null;},querySelectorAll(){return [];},closest(){return null;},
  },extra);
}
const makeSelect = (name, value, labels, patterns={}) => Object.defineProperty(node({name,value,disabled:false}), 'selectedOptions', {get(){
  const option = {textContent:labels[this.value]};
  if (this.value in patterns) option.dataset = {pattern:patterns[this.value]};
  return [option];
}});
const source=makeSelect('source_0','text',{text:'Text',native:'Native Excel datetime','':'Choose source type'});
const date=makeSelect('date_0','v1-date-dmy-slash',
  {'v1-date-dmy-slash':'Day first · DD/MM/YYYY','v1-date-ymd-dash':'Year first · YYYY-MM-DD'},
  {'v1-date-dmy-slash':'DD/MM/YYYY','v1-date-ymd-dash':'YYYY-MM-DD'});
const time=makeSelect('time_0','v1-time-hms',
  {'v1-time-hms':'24-hour, seconds · HH:mm:ss[.ffffff]','v1-time-hm':'24-hour, minutes · HH:mm'},
  {'v1-time-hms':'HH:mm:ss[.ffffff]','v1-time-hm':'HH:mm'});
const parts=node({querySelectorAll:()=>[date,time]});
const effective=node();
const summary=node();
const row=node({dataset:{sourceFormats:'0'},querySelector(selector){
  return {'[data-source-type]':source,'[data-text-formats]':parts,'[data-effective-format]':effective}[selector] || null;
}});
const details=node({id:'source-details-0',open:false,closest:()=>row,querySelector(selector){
  return {'summary':summary,'select:not([disabled])':source,'[data-effective-format]':effective}[selector] || null;
}});
const advanced=node();
const status=node();
const form=node({dataset:{sourceFormatsUrl:'/formats'},elements:{namedItem:name=>({source_0:source,date_0:date,time_0:time})[name]},
  closest:()=>({querySelector:selector=>({'[data-advanced-formats]':advanced,'[data-mapping-status]':status}[selector] ?? status)}),
  querySelectorAll:selector=>selector==='[data-format-details]'?[details]:[],
  querySelector(selector){return {'[data-advanced-formats]':advanced,'[data-source-formats="0"]':row}[selector] || null;},
});
document.querySelectorAll=selector=>selector==='[data-source-formats-url]'?[form]:[];
const window={addEventListener(){}};
let requests=[];
class FormData {
  [Symbol.iterator](){return this.values[Symbol.iterator]();}
  constructor(){this.values=[source,date,time].filter(control=>!control.disabled).map(control=>[control.name,control.value]);}
}
vm.runInNewContext(fs.readFileSync(process.argv[2],'utf8'), {document,window,FormData,URLSearchParams,AbortController,
  fetch:async(url, options)=>{requests.push([...options.body]);return {ok:false};},
});
const emit=async(element,event,target=element)=>{
  for(const fn of element.handlers[event] || []) await fn({target,preventDefault(){},key:event==='keydown'?'Escape':undefined});
};
(async()=>{
  // The mode toggle is always available, so the controller never sets `hidden`.
  assert.equal(advanced.hidden,undefined);
  // The effective format composes bare patterns, not the descriptive option labels.
  assert.equal(effective.textContent,'DD/MM/YYYY HH:mm:ss[.ffffff]');
  window.refrncrSourceFormats();window.refrncrSourceFormats();
  assert.equal(form.handlers.change.length,1);
  assert.equal(summary.handlers.click.length,1);
  await emit(advanced,'click');assert.equal(details.open,true);
  await emit(advanced,'click');assert.equal(details.open,false);
  await emit(summary,'click');assert.equal(details.open,true);assert.equal(document.activeElement,source);
  await emit(details,'keydown');assert.equal(details.open,false);assert.equal(document.activeElement,summary);
  time.value='v1-time-hm';await emit(form,'change',time);
  assert.equal(effective.textContent,'DD/MM/YYYY HH:mm');
  assert.equal(requests.length,1);
  assert.match(status.textContent,/could not be refreshed/);
  source.value='native';await emit(form,'change',source);
  assert.equal(parts.hidden,true);assert.equal(date.disabled,true);assert.equal(time.disabled,true);
  assert.equal(effective.textContent,'Native Excel datetime');
  assert.deepEqual(requests.at(-1),[['source_0','native']]);
  // A server render may reset disabled text controls; transient text choices restore them.
  date.value='v1-date-ymd-dash';time.value='v1-time-hms';
  source.value='text';await emit(form,'change',source);
  assert.equal(date.value,'v1-date-dmy-slash');assert.equal(time.value,'v1-time-hm');
  assert.equal(parts.hidden,false);assert.equal(time.disabled,false);
  assert.equal(requests.length,3);
  const replacement=node({...form,dataset:{sourceFormatsUrl:'/formats'},handlers:{}});
  window.refrncrSourceFormats({querySelectorAll:()=>[replacement]});
  window.refrncrSourceFormats({querySelectorAll:()=>[replacement]});
  assert.equal(replacement.handlers.change.length,1);
})().catch(error=>{console.error(error);process.exitCode=1;});
