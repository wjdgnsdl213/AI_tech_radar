const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const state = require(path.join(__dirname, '../web/static/navigation-state.js'));

test('task routes preserve recommendation period and redirect the former workspace board', () => {
  const route=state.parseRoute('#tasks/recommendations?period=2026-W38','');
  assert.equal(route.tab,'tasks');
  assert.equal(route.period,'2026-W38');
  assert.equal(state.routeHash(route),'tasks/recommendations?period=2026-W38');
  assert.equal(state.routeHash(state.parseRoute('#workspace/tasks','')),'tasks/board');
});

test('explore memory restores committed query, filters, view and page across menu navigation and reload', () => {
  const data=new Map(),storage={getItem:k=>data.get(k),setItem:(k,v)=>data.set(k,v)};
  const memory=state.createExploreMemory(storage);
  assert.equal(memory.read(),null);
  const context={q:'공공데이터',axis:'ai',since:'2026-09-01',until:'2026-09-17',order:'oldest'};
  memory.remember({tab:'explore',mode:'timeline',context},3);
  memory.remember({tab:'workspace',mode:'team'});
  const restored=state.createExploreMemory(storage).read();
  assert.equal(restored.mode,'timeline');
  assert.equal(restored.page,3);
  assert.deepEqual(restored.context,context);
  restored.context.q='changed';
  assert.equal(memory.read().context.q,'공공데이터');
  memory.remember({tab:'explore',mode:'articles',context:{q:'새 검색'}});
  assert.equal(memory.read().page,1);
});

test('explore memory tolerates unavailable storage and corrupted saved routes', () => {
  const memory=state.createExploreMemory({getItem:()=>'{broken',setItem:()=>{throw Error('unavailable');}});
  assert.equal(memory.read(),null);
  memory.remember({tab:'explore',mode:'articles',context:{q:'AI'}},2);
  assert.equal(memory.read().page,2);
  assert.equal(state.createExploreMemory({getItem:()=>JSON.stringify({hash:'workspace/team'})}).read(),null);
});

test('graph center uses the article search even when the keyword index changes spelling', () => {
  const context = {q:'ai',axis:'ai',since:'2026-09-01',until:'2026-09-01'};
  assert.deepEqual(state.keywordArticleRequest(context, 'AI', 'AI'), {
    path:'/api/search', scope:'search',
    params:{q:'ai',axis:'ai',since:'2026-09-01',until:'2026-09-01',kept_only:1,order:'relevance',page:1,size:25}
  });
  assert.equal(state.keywordArticleRequest(context, 'ai', null).path, '/api/search');
});

test('a related node retains its indexed keyword and the selected dates and axis', () => {
  assert.deepEqual(state.keywordArticleRequest({q:'AI',axis:'ai',since:'2026-09-01',until:'2026-09-02'}, '데이터', 'AI'), {
    path:'/api/keyword/%EB%8D%B0%EC%9D%B4%ED%84%B0',scope:'keyword',
    params:{limit:40,axis:'ai',since:'2026-09-01',until:'2026-09-02'}
  });
});

test('timeline defaults to newest and preserves oldest across view links', () => {
  assert.equal(state.normalizeContext({}).order, 'newest');
  assert.equal(state.normalizeContext({order:'invalid'}).order, 'newest');
  const route = state.parseRoute('#explore/timeline?q=AI&order=oldest', '');
  assert.equal(route.context.order, 'oldest');
  route.mode = 'graph';
  assert.equal(state.routeHash(route), 'explore/graph?q=AI&order=oldest');
  assert.equal(state.parseRoute('#' + state.routeHash(route), '').context.order, 'oldest');
});

test('empty and former home routes open the weekly briefing', () => {
  assert.deepEqual(state.parseRoute('', ''), {tab:'briefing', mode:'weekly', period:'', context:state.emptyContext()});
  assert.deepEqual(state.parseRoute('#home', ''), {tab:'briefing', mode:'weekly', period:'', context:state.emptyContext()});
});

test('the legacy digest route preserves the emailed week query', () => {
  assert.deepEqual(state.parseRoute('#digest', '?week=2026-W35'), {
    tab:'briefing', mode:'weekly', period:'2026-W35', context:state.emptyContext()
  });
});

test('legacy review and month routes retain their selected period', () => {
  assert.equal(state.parseRoute('#reviews/weekly:2026-W37', '').period, '2026-W37');
  assert.deepEqual(state.parseRoute('#month/2026-08', ''), {
    tab:'briefing', mode:'monthly', period:'2026-08', context:state.emptyContext()
  });
});

test('legacy explore routes map to the matching view and retain the issue query', () => {
  assert.equal(state.parseRoute('#search', '').mode, 'articles');
  assert.equal(state.parseRoute('#graph', '').mode, 'graph');
  assert.deepEqual(state.parseRoute('#issues/%EA%B3%B5%EA%B3%B5%EB%8D%B0%EC%9D%B4%ED%84%B0', ''), {
    tab:'explore', mode:'timeline', period:'',
    context:{q:'공공데이터', axis:'', since:'', until:'', order:'newest'}
  });
  assert.equal(state.parseRoute('#analysis/orgs', '').mode, 'discovery:orgs');
});

test('canonical explore routes round-trip one keyword and date context across views', () => {
  const context = {q:'생성형 AI', axis:'ai', since:'2026-08-01', until:'2026-09-11', order:'newest'};
  const hash = state.routeHash({tab:'explore', mode:'timeline', context});
  assert.equal(hash, 'explore/timeline?q=%EC%83%9D%EC%84%B1%ED%98%95+AI&axis=ai&since=2026-08-01&until=2026-09-11');
  assert.deepEqual(state.parseRoute('#' + hash, ''), {tab:'explore', mode:'timeline', period:'', context});
});

test('invalid explore dates and unsupported values are discarded', () => {
  assert.deepEqual(state.normalizeContext({q:'  AI  ', axis:'unknown space', since:'09/01/2026', until:'2026-99-99'}), {
    q:'AI', axis:'', since:'', until:'', order:'newest'
  });
});

test('canonical briefing hashes retain kind and valid period', () => {
  assert.equal(state.routeHash({tab:'briefing',mode:'monthly',period:'2026-08'}), 'briefing/monthly:2026-08');
  assert.deepEqual(state.parseRoute('#briefing/monthly:2026-08', ''), {
    tab:'briefing', mode:'monthly', period:'2026-08', context:state.emptyContext()
  });
});

test('a reversed date range is rejected before an API request', () => {
  assert.equal(state.contextError({since:'2026-09-11',until:'2026-09-01'}), '시작일은 종료일보다 늦을 수 없습니다.');
  assert.equal(state.contextError({since:'2026-09-01',until:'2026-09-11'}), '');
});

test('a deferred response cannot replace the latest request result', async () => {
  const guard = state.createRequestGuard();
  let releaseOld;
  const oldResponse = new Promise(resolve => { releaseOld = resolve; });
  const applied = [];
  async function run(context, response) {
    const ticket = guard.begin(context);
    const value = await response;
    if (guard.isCurrent(ticket, context)) applied.push(value);
  }
  const oldRun = run({q:'old',since:'2026-08-01'}, oldResponse);
  await run({q:'new',since:'2026-09-01'}, Promise.resolve('new result'));
  releaseOld('old result');
  await oldRun;
  assert.deepEqual(applied, ['new result']);
});

test('invalidating a view prevents its pending response from applying', () => {
  const guard = state.createRequestGuard();
  const ticket = guard.begin({q:'AI'});
  guard.invalidate();
  assert.equal(guard.isCurrent(ticket, {q:'AI'}), false);
});

test('startup failure markup is visible, retryable, and escapes the error', () => {
  const html = state.initErrorHTML(new Error('/api/meta <offline>'));
  assert.match(html, /초기 정보를 불러오지 못했습니다/);
  assert.match(html, /data-init-retry/);
  assert.match(html, /&lt;offline&gt;/);
  assert.doesNotMatch(html, /<offline>/);
});
