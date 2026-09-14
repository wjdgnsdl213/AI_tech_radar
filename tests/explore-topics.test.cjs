const test = require('node:test');
const assert = require('node:assert/strict');
const {createLoader, topicRoute} = require('../web/static/explore-topics.js');
const response = (week, keyword) => ({week,week_label:week,rows:[{keyword,count:12}]});

test('groups share one week and a slow second group does not hide the first', async () => {
  let finish;
  const calls = [], frames = [];
  const loader = createLoader(async (path, params) => {
    calls.push(params);
    if (params.axis === 'ai,bigdata') return response('2026-W37','AI');
    return new Promise(resolve => {finish=resolve;});
  }, state => frames.push(structuredClone(state)));
  const pending = loader.load();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(frames.at(-1).technology.status, 'ready');
  assert.equal(frames.at(-1).smallbiz.status, 'loading');
  assert.equal(calls[1].week,'2026-W37');
  finish(response('2026-W37','상권'));
  await pending;
  assert.equal(frames.at(-1).smallbiz.status,'ready');
});

test('one failed group leaves the other usable and retry does not repeat on view changes', async () => {
  let fail = true, count = 0;
  const frames = [];
  const loader = createLoader(async (_, p) => {
    count++;
    if (p.axis === 'ai,bigdata' && fail) throw new Error('offline');
    return response('2026-W37',p.axis);
  }, state => frames.push(structuredClone(state)));
  await loader.load();
  assert.equal(frames.at(-1).technology.status,'error');
  assert.equal(frames.at(-1).smallbiz.status,'ready');
  await loader.load();
  assert.equal(count,2);
  fail=false;
  await loader.load(true);
  assert.equal(frames.at(-1).technology.status,'ready');
  assert.equal(count,4);
});

test('refresh clears old-week data before showing the new-week group', async () => {
  let now=0, week='2026-W37';
  const frames=[];
  const loader=createLoader(async (_,p)=>response(week,p.axis),s=>frames.push(structuredClone(s)),()=>now);
  await loader.load();
  now=31000; week='2026-W38'; frames.length=0;
  await loader.load();
  const early=frames.find(s=>s.week==='2026-W38');
  assert.equal(early.smallbiz.status,'loading');
  assert.deepEqual(early.smallbiz.rows,[]);
});

test('concurrent navigation shares the pending request and empty week stops second query', async () => {
  let finish, calls=0;
  const frames=[];
  const loader=createLoader(()=>{calls++;return new Promise(r=>finish=r);},s=>frames.push(structuredClone(s)));
  const a=loader.load(), b=loader.load();
  finish({week:null,rows:[]});
  await Promise.all([a,b]);
  assert.equal(calls,1);
  assert.equal(frames.at(-1).technology.status,'empty');
  assert.equal(frames.at(-1).smallbiz.status,'empty');
});

test('a topic changes only the committed query and view, leaving filters and original state intact', () => {
  const context={q:'AI',axis:'ai',since:'2026-09-01',until:'2026-09-07',order:'oldest'};
  assert.deepEqual(topicRoute(context,'공공데이터'), {
    tab:'explore',mode:'articles',context:{...context,q:'공공데이터'}
  });
  assert.equal(context.q,'AI');
});
