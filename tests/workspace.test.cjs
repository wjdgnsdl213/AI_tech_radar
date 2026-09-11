const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const file = path.join(__dirname, '../web/static/workspace-store.js');
function library() {
  assert.ok(fs.existsSync(file), 'personal workspace store is missing');
  return require(file);
}
function memory() {
  const data = new Map();
  return {getItem: k => data.get(k) ?? null, setItem: (k,v) => data.set(k,v)};
}
const article = {key: 'article:12', kind: 'article', title: 'AI 상권', url: 'https://example.com/12', text: '요약', topic: ''};
test('resaving the same article preserves personal notes across reload', () => {
  const {createStore} = library(), storage = memory(), store = createStore(storage);
  store.saveScrap(article);
  store.editScrap(article.key, {note: '검토할 근거', topic: '상권'});
  store.saveScrap(article);
  const reloaded = createStore(storage).read();
  assert.equal(reloaded.scraps.length, 1);
  assert.equal(reloaded.scraps[0].note, '검토할 근거');
  assert.equal(reloaded.scraps[0].topic, '상권');
});
test('failed persistence leaves saved data intact', () => {
  const storage = memory(), store = library().createStore(storage);
  store.saveScrap(article);
  storage.setItem = () => {throw new Error('quota');};
  assert.throws(() => store.editScrap(article.key, {note:'lost'}), /quota/);
  assert.equal(store.read().scraps[0].note, '');
});
test('restore merges new records but never overwrites existing notes', () => {
  const store = library().createStore(memory());
  store.saveScrap(article);
  store.editScrap(article.key, {note:'keep'});
  store.restore(JSON.stringify({version:1, scraps:[{...article, note:'replace'}, {...article,key:'article:13',title:'새 기사'}], issues:[],tasks:[]}));
  assert.equal(store.read().scraps[0].note, 'keep');
  assert.equal(store.read().scraps.length, 2);
});
test('invalid restore is atomic and unsafe URL is removed', () => {
  const store = library().createStore(memory());
  store.saveScrap({...article,url:'javascript:alert(1)'});
  assert.equal(store.read().scraps[0].url, '');
  assert.throws(() => store.restore('{"version":42,"scraps":[]}'));
  assert.equal(store.read().scraps.length, 1);
});
test('task update preserves review history and does not duplicate candidates', () => {
  const store = library().createStore(memory());
  const task = {key:'2026-W37:상권',title:'상권',fact:'보도',mean:'해석',ask:'질문'};
  store.addTask(task); store.addTask(task);
  store.editTask(task.key, {status:'reviewing', owner:'담당 A', note:'데이터 확인'});
  assert.equal(store.read().tasks.length, 1);
  assert.equal(store.read().tasks[0].history.length, 2);
  assert.equal(store.read().tasks[0].status, 'reviewing');
  assert.throws(() => store.editTask(task.key, {status:'invalid'}));
});
test('export includes only selected scraps and escapes HTML and unsafe links', () => {
  const {createStore, exportBundle} = library(), store = createStore(memory());
  store.saveScrap({...article,title:'<script>alert(1)</script>',note:'메모'});
  store.saveScrap({...article,key:'article:99',title:'선택하지 않은 기사'});
  const out = exportBundle(store.read(), [article.key], '회의자료');
  assert.match(out.html, /&lt;script&gt;/);
  assert.doesNotMatch(out.html, /<script>/);
  assert.doesNotMatch(out.markdown, /선택하지 않은 기사/);
  assert.match(out.markdown, /https:\/\/example.com\/12/);
});
test('concurrent store instances merge writes from other tabs', () => {
  const storage = memory(), a = library().createStore(storage), b = library().createStore(storage);
  a.saveScrap(article); b.followIssue('공공데이터');
  a.editScrap(article.key, {note:'new'});
  assert.equal(a.read().issues.length, 1);
});
