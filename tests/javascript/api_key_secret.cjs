const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[2], 'utf8');
async function scenario(fail) {
  const listeners = {}, windowListeners = {};
  const secret = {value:'synthetic-secret',textContent:'synthetic-secret',focus(){this.focused=true;},select(){this.selected=true;}};
  const button = {hidden:true,addEventListener(name,fn){assert(!listeners[name]);listeners[name]=fn;}};
  const status = {};
  const root = {dataset:{},querySelector(selector){return {'#api-key-secret':secret,'[data-copy-secret]':button,'[data-copy-status]':status}[selector];}};
  let copied;
  const context = {document:{querySelector:()=>root},window:{addEventListener(name,fn){windowListeners[name]=fn;}},
    navigator:{clipboard:{async writeText(value){if(fail)throw Error('denied');copied=value;}}}};
  vm.runInNewContext(source,context);
  vm.runInNewContext(source,context);
  assert.equal(button.hidden,false);
  await listeners.click();
  if (fail) {
    assert(secret.focused && secret.selected);
    assert.match(status.textContent,/manually/);
  } else {
    assert.equal(copied,'synthetic-secret');
    assert.match(status.textContent,/copied/);
  }
  windowListeners.pagehide();
  assert.equal(secret.value,'');
  assert.equal(secret.textContent,'');
  assert.equal(button.disabled,true);
  assert.match(status.textContent,/no longer available/);
}
(async()=>{await scenario(false);await scenario(true);console.log('PASS copy, fallback, idempotence and history clearing');})().catch(e=>{console.error(e);process.exitCode=1;});
