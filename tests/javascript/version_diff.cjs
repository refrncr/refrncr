/* BFCache restoration must revalidate; ordinary pageshow must not reopen/audit. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let handler, review=true, reloads=0;
vm.runInNewContext(fs.readFileSync(process.argv[2],'utf8'), {
  window:{addEventListener(name,fn){assert.equal(name,'pageshow');handler=fn;},location:{reload(){reloads++;}}},
  document:{querySelector(selector){assert.equal(selector,'[data-version-review]');return review;}}
});
handler({persisted:false}); assert.equal(reloads,0);
handler({persisted:true}); assert.equal(reloads,1);
review=false; handler({persisted:true}); assert.equal(reloads,1);
console.log('Review history controller checks passed');
