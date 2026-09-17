const test = require('node:test');
const assert = require('node:assert/strict');
const create = require('../web/static/regulatory-loader.js');

const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
};

test('all-period view fetches only the visible list', async () => {
  const calls = [], lists = [];
  const loader = create(async (path, params) => {
    calls.push(params); return {total: 25, items: [{id: 1}]};
  }, {list: (data, days) => lists.push([data.total, days])});
  await loader.load(0);
  assert.deepEqual(calls, [{limit: 200, days: 0}]);
  assert.deepEqual(lists, [[25, 0]]);
});

test('visible list renders before cumulative count, and optional failure keeps it usable', async () => {
  const count = deferred(), lists = [], failures = [];
  const loader = create(async (path, params) => params.limit === 1 ? count.promise : {total: 3},
    {list: data => lists.push(data.total), error: err => failures.push(err)});
  const loading = loader.load(7);
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(lists, [3]);
  count.reject(new Error('count unavailable'));
  await loading;
  assert.deepEqual(lists, [3]);
  assert.equal(failures.length, 0);
});

test('late list and count cannot overwrite a newer filter or keyword view', async () => {
  const first = deferred(), total = deferred(), lists = [], counts = [];
  const loader = create(async (path, params) => {
    if (params.days === 7) return first.promise;
    if (params.limit === 1) return total.promise;
    return {total: params.days || 100};
  }, {list: data => lists.push(data.total), total: data => counts.push(data.total)});
  const old = loader.load(7);
  const recent = loader.load(30);
  await new Promise(resolve => setImmediate(resolve));
  first.resolve({total: 7});
  await old;
  assert.deepEqual(lists, [30]);
  loader.cancel();
  total.resolve({total: 100});
  await recent;
  assert.deepEqual(counts, []);
});

test('main request failure can be retried', async () => {
  let calls = 0;
  const errors = [], lists = [];
  const loader = create(async () => {
    if (++calls === 1) throw new Error('temporary');
    return {total: 2};
  }, {list: data => lists.push(data.total), error: err => errors.push(err.message)});
  await loader.load(0);
  await loader.load(0);
  assert.deepEqual(errors, ['temporary']);
  assert.deepEqual(lists, [2]);
});
