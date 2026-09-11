(function(root, factory) {
  if(typeof module === 'object' && module.exports) module.exports = factory;
  else root.createApiClient = factory;
})(globalThis, function(fetcher, origin, now = Date.now) {
  const cache = new Map(), pending = new Map();
  return function api(path, params) {
    const url = new URL(path, origin);
    Object.entries(params || {}).sort(([a],[b]) => a.localeCompare(b)).forEach(([k,v]) => {
      if(v !== '' && v != null) url.searchParams.set(k,v);
    });
    const key = url.href, saved = cache.get(key);
    if(saved && saved.until > now()) return Promise.resolve(structuredClone(saved.data));
    if(pending.has(key)) return pending.get(key).then(structuredClone);
    const request = (async () => {
      const response = await fetcher(url);
      if(!response.ok) throw new Error(`${path} ${response.status}`);
      const data = await response.json();
      cache.delete(key);
      cache.set(key, {data, until:now() + 30000});
      while(cache.size > 80) cache.delete(cache.keys().next().value);
      return data;
    })();
    pending.set(key, request);
    return request.then(structuredClone).finally(() => pending.delete(key));
  };
});
