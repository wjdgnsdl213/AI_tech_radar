const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('web/static/app.js', 'utf8');
const fn = source.slice(source.indexOf('function viewFor('), source.indexOf('/* 휠 줌'));
function zoom(k, fx = .5, fy = .5) {
  const scope = {BASE:{w:1000,h:600},VIEW:{x:0,y:0,w:1000,h:600}};
  vm.runInNewContext(fn, scope);
  return scope.viewFor(k,fx,fy);
}
test('graph can zoom out to 20 percent while preserving its center',()=>{
  const result = zoom(.1);
  assert.equal(result.w,5000);
  assert.equal(result.h,3000);
  assert.equal(result.x,-2000);
  assert.equal(result.y,-1200);
});
test('zoom preserves cursor anchor and existing zoom-in limit',()=>{
  const result = zoom(.5,.25,.75);
  assert.equal(result.x + result.w * .25,250);
  assert.equal(result.y + result.h * .75,450);
  assert.equal(zoom(100).w,120);
});
