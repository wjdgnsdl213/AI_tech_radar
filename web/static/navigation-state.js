/* Canonical navigation and shared explore context. Kept DOM-free for link regression tests. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.NavigationState = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const emptyContext = () => ({q:'', axis:'', since:'', until:'', order:'newest'});
  const isoDate = value => {
    const s = String(value || '');
    if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return '';
    const d = new Date(s + 'T00:00:00Z');
    return !Number.isNaN(d.valueOf()) && d.toISOString().slice(0,10) === s ? s : '';
  };
  function normalizeContext(value) {
    const v = value || {};
    const axis = /^[\w,-]+$/.test(String(v.axis || '')) ? String(v.axis || '') : '';
    return {q:String(v.q || '').trim().slice(0,100), axis,
      since:isoDate(v.since), until:isoDate(v.until), order:v.order === 'oldest' ? 'oldest' : 'newest'};
  }
  function contextError(value) {
    const v = normalizeContext(value);
    return v.since && v.until && v.since > v.until ? '시작일은 종료일보다 늦을 수 없습니다.' : '';
  }
  function keywordArticleRequest(context, keyword, center) {
    const {q,axis,since,until} = normalizeContext(context);
    // 중심어는 기사 탭과 같은 원문 검색, 이웃 노드는 추출 키워드로 조회한다.
    if (keyword === center || keyword === q) return {
      path:'/api/search', scope:'search',
      params:{q,axis,since,until,kept_only:1,order:'relevance',page:1,size:25}
    };
    return {path:'/api/keyword/' + encodeURIComponent(keyword),scope:'keyword',
      params:{limit:40,axis,since,until}};
  }
  const requestKey = value => JSON.stringify(Object.entries(value || {}).sort(([a],[b]) => a.localeCompare(b)));
  function createRequestGuard() {
    let generation = 0;
    return {
      begin(context) { return {generation:++generation,key:requestKey(context)}; },
      isCurrent(ticket, context) {
        return !!ticket && ticket.generation === generation && ticket.key === requestKey(context);
      },
      invalidate() { generation += 1; }
    };
  }
  function initErrorHTML(error) {
    const message = String(error?.message || error || '알 수 없는 오류').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    return `<b>초기 정보를 불러오지 못했습니다.</b><br><code>${message}</code><br><button class="preset" data-init-retry>다시 시도</button>`;
  }
  const validPeriod = (mode, value) => {
    const re = mode === 'monthly' ? /^\d{4}-\d{2}$/ : /^\d{4}-W\d{2}$/;
    return re.test(value || '') ? value : '';
  };
  const decode = value => { try { return decodeURIComponent(value || ''); } catch { return value || ''; } };

  function parseRoute(hash, pageSearch) {
    const raw = String(hash || '').replace(/^#/, '');
    const question = raw.indexOf('?');
    const path = question < 0 ? raw : raw.slice(0, question);
    const params = new URLSearchParams(question < 0 ? '' : raw.slice(question + 1));
    const [tab = '', segment = ''] = path.split('/');
    const page = new URLSearchParams(String(pageSearch || '').replace(/^\?/, ''));
    const context = normalizeContext({q:params.get('q'),axis:params.get('axis'),since:params.get('since'),until:params.get('until'),order:params.get('order')});

    if (!tab || ['home','digest'].includes(tab)) {
      return {tab:'briefing',mode:'weekly',period:validPeriod('weekly',page.get('week')),context:emptyContext()};
    }
    if (tab === 'month') return {tab:'briefing',mode:'monthly',period:validPeriod('monthly',segment),context:emptyContext()};
    if (tab === 'reviews' || tab === 'briefing') {
      const [kind, period = ''] = segment.split(':');
      const mode = kind === 'monthly' ? 'monthly' : 'weekly';
      return {tab:'briefing',mode,period:validPeriod(mode,period),context:emptyContext()};
    }
    if (tab === 'reg') return {tab:'reg',mode:'',period:'',context:emptyContext()};
    if (tab === 'workspace') return {tab:'workspace',mode:segment || 'scraps',period:'',context:emptyContext()};
    if (tab === 'issues') return {tab:'explore',mode:'timeline',period:'',context:normalizeContext({q:decode(segment)})};
    if (tab === 'analysis') {
      const discovery = ['cross','orgs','trend'].includes(segment) ? segment : 'trend';
      return {tab:'explore',mode:'discovery:' + discovery,period:'',context};
    }
    if (tab === 'trend') return {tab:'explore',mode:'discovery:trend',period:'',context};
    if (tab === 'graph') return {tab:'explore',mode:'graph',period:'',context};
    if (tab === 'search') return {tab:'explore',mode:'articles',period:'',context};
    if (tab === 'explore') {
      const mode = ['articles','timeline','graph','start','discovery:trend','discovery:orgs','discovery:cross'].includes(segment) ? segment : 'start';
      return {tab:'explore',mode,period:'',context};
    }
    return {tab:'briefing',mode:'weekly',period:'',context:emptyContext()};
  }

  function routeHash(route) {
    if (route.tab === 'briefing') {
      const mode = route.mode === 'monthly' ? 'monthly' : 'weekly';
      const period = validPeriod(mode, route.period);
      return `briefing/${mode}${period ? ':' + period : ''}`;
    }
    if (route.tab === 'reg') return 'reg';
    if (route.tab === 'workspace') return 'workspace/' + (route.mode === 'tasks' ? 'tasks' : 'scraps');
    const mode = ['articles','timeline','graph','start','discovery:trend','discovery:orgs','discovery:cross'].includes(route.mode) ? route.mode : 'start';
    const p = new URLSearchParams();
    const c = normalizeContext(route.context);
    Object.entries(c).forEach(([key,value]) => { if (value && !(key === 'order' && value === 'newest')) p.set(key,value); });
    return `explore/${mode}${p.size ? '?' + p.toString() : ''}`;
  }

  return {emptyContext,normalizeContext,contextError,keywordArticleRequest,createRequestGuard,initErrorHTML,parseRoute,routeHash};
});
