const test = require('node:test');
const assert = require('node:assert/strict');
const create = require('../web/static/api-client.js');
test('same request is coalesced, reused, expires, and results cannot mutate the cache', async () => {
  let calls=0, clock=0;
  const api=create(async()=>{calls++; return {ok:true,json:async()=>({n:1})};},'http://localhost',()=>clock);
  const [a,b]=await Promise.all([api('/search',{q:'AI',page:1}),api('/search',{page:1,q:'AI'})]);
  assert.equal(calls,1); a.n=9; assert.equal(b.n,1);
  assert.equal((await api('/search',{q:'AI',page:1})).n,1); assert.equal(calls,1);
  clock=30001; await api('/search',{q:'AI',page:1}); assert.equal(calls,2);
});
test('failed requests can retry and different filters do not share results', async()=>{
  let calls=0;
  const api=create(async()=>({ok:++calls>1,status:503,json:async()=>({calls})}),'http://localhost');
  await assert.rejects(api('/search',{q:'AI'}));
  assert.equal((await api('/search',{q:'AI'})).calls,2);
  await api('/search',{q:'data'}); assert.equal(calls,3);
});
