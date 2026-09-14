(function(root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ExploreTopics = factory();
})(globalThis, function() {
  'use strict';
  const group = (status, rows = []) => ({status, rows});
  const resultGroup = data => group(data.rows?.length ? 'ready' : 'empty', data.rows || []);

  // 검색 상태를 받지 않는다. 보기 전환은 같은 집계를 재사용하고 오류도 독립적으로 처리한다.
  function createLoader(api, render, now = Date.now) {
    let pending = null, refreshedAt = -Infinity;
    let state = {week:'',label:'',technology:group('loading'),smallbiz:group('loading')};
    async function refresh() {
      if (!state.week) render(state);
      try {
        const data = await api('/api/trend', {axis:'ai,bigdata',top:5});
        state = {week:data.week || '',label:data.week_label || '',
          technology:resultGroup(data),smallbiz:group(data.week ? 'loading' : 'empty')};
        render(state);
        if (!data.week) return;
      } catch {
        state = {week:'',label:'',technology:group('error'),smallbiz:group('loading')};
        render(state);
      }
      try {
        // 첫 그룹 실패 시에도 둘째 그룹은 최신 주 조회로 독립적으로 살아 있어야 한다.
        const data = await api('/api/trend', {axis:'smallbiz',top:5,...(state.week ? {week:state.week} : {})});
        state = {...state,week:data.week || '',label:data.week_label || '',smallbiz:resultGroup(data)};
      } catch {
        state = {...state,smallbiz:group('error')};
      }
      render(state);
    }
    return {
      load(force = false) {
        if (pending) return pending;
        if (!force && now() - refreshedAt < 30000) return Promise.resolve();
        pending = refresh().finally(() => {refreshedAt = now();pending = null;});
        return pending;
      }
    };
  }
  function topicRoute(context, keyword) {
    return {tab:'explore',mode:'articles',context:{...context,q:keyword}};
  }
  return {createLoader,topicRoute};
});
