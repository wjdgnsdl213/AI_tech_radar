/* 목록을 먼저 보여주고, 누적 건수는 나중에 보완한다. */
(function (root) {
  'use strict';
  function create(api, handlers) {
    let generation = 0;
    return {
      cancel() { generation++; },
      async load(days) {
        const ticket = ++generation;
        let data;
        try {
          data = await api('/api/regulatory', {limit: 200, days});
        } catch (error) {
          if (ticket === generation && handlers.error) handlers.error(error);
          return;
        }
        if (ticket !== generation) return;
        handlers.list(data, days);
        if (!days) return;
        try {
          const all = await api('/api/regulatory', {limit: 1});
          if (ticket === generation && handlers.total) handlers.total(all, data, days);
        } catch (_) { /* 부가 정보 실패가 이미 표시한 목록을 가리지 않는다. */ }
      }
    };
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = create;
  else root.createRegulatoryLoader = create;
})(typeof globalThis !== 'undefined' ? globalThis : this);
