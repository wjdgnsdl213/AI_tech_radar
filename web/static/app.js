/* SAB Trend — SPA
 *
 * 서버(web/api.py)는 JSON만 내고 렌더는 여기서 한다.
 * 항목 선정·점수·브릿지는 전부 src/*.py가 정한 것을 그대로 쓴다 —
 * 화면에서 다시 고르면 메일·CSV와 갈라진다.
 *
 * 화면에 내부 지표(교차 점수·관련도·축 키)를 노출하지 않는다.
 * 팀원에게 "교차 27.2"는 아무 뜻이 없다. 정렬에만 쓰고 화면에는 안 보인다.
 */
'use strict';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = n => (n ?? 0).toLocaleString('ko-KR');
const api = async (p, q) => {
  const u = new URL(p, location.origin);
  Object.entries(q || {}).forEach(([k, v]) => v !== '' && v != null && u.searchParams.set(k, v));
  const r = await fetch(u);
  if (!r.ok) throw new Error(`${p} ${r.status}`);
  return r.json();
};

let AXES = [];
const label = k => (AXES.find(a => a.key === k) || {}).label || k;
const tags = ax => (ax || []).map(a =>
  `<span class="tag ${esc(a)}">${esc(label(a))}</span>`).join('');

/* ── 탭 ── */
const loaded = new Set();
/* 주소는 '#tab' 또는 '#analysis/trend' 두 꼴이다.
   서브탭을 주소에 안 담으면 뒤로 가기가 '분석' 안 어디로 돌아갈지 정할 수 없다. */
function routeOf() {
  const [tab, sub] = (location.hash || '#home').slice(1).split('/');
  return { tab: LOADERS[tab] ? tab : 'home', sub: SUB_LOADERS[sub] ? sub : '' };
}

function showTab(name, sub) {
  if (name === 'analysis' && !sub) {
    // 메뉴로 들어올 때는 보던 탭을 유지한다 — 매번 '교차'로 튕기면 성가시다
    const cur = $('.subtab.active');
    sub = (cur && cur.dataset.sub) || 'cross';
  }
  $$('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  $$('.panel').forEach(p => p.classList.toggle('active', p.id === 'panel-' + name));
  // ★ 주소를 먼저 맞춘다. hashchange가 이걸 보고 "이미 그 화면"인지 판단한다.
  const want = name + (sub ? '/' + sub : '');
  if (location.hash.slice(1) !== want) location.hash = want;
  closeNav();
  window.scrollTo(0, 0);        // 화면을 갈아탔는데 스크롤이 중간에 남아 있으면 길을 잃는다
  if (!loaded.has(name)) { loaded.add(name); (LOADERS[name] || (() => {}))(); }
  if (sub) showSub(sub, true);
}

/* ★ 뒤로 가기를 살린다.
   showTab은 location.hash를 바꾸므로 브라우저 기록은 원래 쌓이고 있었다.
   그런데 그 변화를 듣는 곳이 없어서, 뒤로 가면 주소만 되돌아가고 화면은
   그대로 남았다(실측: 홈 → 급상승 '전체 보기' → 뒤로 가기 → 홈으로 안 감).
   showTab이 스스로 바꾼 해시로는 아래가 아무 일도 하지 않는다 — 값이 같아
   hashchange 자체가 안 뜨고, 떠도 이미 그 화면이라 렌더가 멱등이다. */
window.addEventListener('hashchange', () => {
  const r = routeOf();
  showTab(r.tab, r.sub);
});

/* 메뉴 접기.
   넓은 화면에서는 아이콘만 남기고(기둥을 좁힌다), 좁은 화면(820px 이하)에서는
   통째로 밀어 넣는다. 같은 버튼이 화면 폭에 따라 다른 일을 하는 게 아니라,
   CSS가 폭에 맞는 표현을 고르고 JS는 상태만 켠다. */
// CSS의 820px 분기와 같은 값. matchMedia를 쓰면 그게 없는 환경에서 접기 버튼이
// 통째로 죽는데(실측), innerWidth는 어디에나 있다.
const NARROW = () => window.innerWidth <= 820;

function closeNav() {
  $('#sidenav').classList.remove('open');
  $('#navscrim').classList.remove('open');
}

function setFold(on) {
  $('#sidenav').classList.toggle('fold', on);
  $('.shell').classList.toggle('fold', on);
  try { localStorage.setItem('navFold', on ? '1' : '0'); } catch (e) { /* 무시 */ }
}

$('#nav-fold').onclick = () => {
  if (NARROW()) {
    $('#sidenav').classList.toggle('open');
    $('#navscrim').classList.toggle('open');
  } else {
    setFold(!$('#sidenav').classList.contains('fold'));
  }
};
$('#navscrim').onclick = closeNav;
// 접어둔 상태는 다음에 열 때도 유지된다 — 매번 다시 접게 하면 성가시다
try { if (localStorage.getItem('navFold') === '1') setFold(true); } catch (e) { /* 무시 */ }

// 메뉴·로고·"전체 보기" 버튼이 전부 같은 경로를 탄다
document.body.addEventListener('click', e => {
  const b = e.target.closest('[data-tab]');
  if (!b) return;
  e.preventDefault();
  const want = b.dataset.tab;
  // 교차·기관·급상승은 이제 '분석' 안의 탭이다. 홈 카드의 '전체 보기'처럼
  // 예전 이름으로 부르는 곳이 여럿이라, 이름을 바꾸는 대신 여기서 넘겨준다.
  if (SUB_LOADERS[want]) { showTab('analysis', want); return; }
  showTab(want);
});

/* 상단 검색 = 기사 검색. 어느 화면에 있든 여기서 바로 들어간다. */
$('#top-form').onsubmit = e => {
  e.preventDefault();
  const q = $('#top-q').value.trim();
  showTab('search');
  if (!loaded.has('search')) { loaded.add('search'); initSearch(); }
  $('#f-q').value = q;      // 검색 패널의 입력창 id는 f-q 다
  runSearch();
};

function itemHTML(p) {
  return `<div class="item">
    <div class="item-t">
      <a href="#" data-item="${p.id}">${esc(p.title)}</a>
      <a href="${esc(p.url)}" target="_blank" rel="noopener" class="src-link"
         title="원문으로 이동">원문 ↗</a>
    </div>
    <div class="item-m">${tags(p.axes)} ${esc(p.source)} · ${esc(p.published)}
      ${p.insight ? '<span class="has-ai"><svg class="ico"><use href="#i-bulb"/></svg> 해설</span>' : ''}</div>
  </div>`;
}

/* ── ① 이번 주 ── */
let DIGEST = null, activeAxis = 'all';

function renderDigest() {
  const d = DIGEST;
  $('#digest-title').textContent = d?.week_label || '—';
  if (!d || d.empty) {
    $('#digest-body').innerHTML = '<div class="empty">이 주차에 항목이 없습니다.</div>';
    $('#axis-chips').innerHTML = ''; return;
  }
  const chips = [{ key: 'all', label: '전체', n: d.sections.reduce((a, s) => a + s.items.length, 0) }]
    .concat(d.sections.filter(s => s.items.length).map(s => ({ key: s.key, label: s.label, n: s.items.length })));
  $('#axis-chips').innerHTML = chips.map(c =>
    `<button class="chip ${c.key === activeAxis ? 'active' : ''}" data-axis="${esc(c.key)}"
     >${esc(c.label)}<span class="n">${c.n}</span></button>`).join('');

  const secs = d.sections.filter(s => s.items.length &&
    (activeAxis === 'all' || s.key === activeAxis));
  $('#digest-body').innerHTML = secs.length
    ? secs.map(s => `<div class="sec-title">${esc(s.label)}</div>
        <div class="items">${s.items.map(itemHTML).join('')}</div>`).join('')
    : '<div class="empty">해당 주제에 항목이 없습니다.</div>';

  // 사이드: 이번 주 급상승
  $('#side-trend').innerHTML = (d.trending || []).length
    ? d.trending.slice(0, 10).map(t =>
        `<div class="mini" data-kw="${esc(t.keyword)}">
           <span class="k">${esc(t.keyword)}</span>
           <span class="v">${t.count}건${t.is_new ? ' <span class="new">신규</span>' : ''}</span>
         </div>`).join('')
    : '<div class="empty">—</div>';
}

async function loadDigest(week) {
  DIGEST = await api('/api/digest', { week: week || '' });
  if (DIGEST.lead) { $('#lead').textContent = DIGEST.lead; $('#lead-card').hidden = false; }
  else $('#lead-card').hidden = true;
  /* 주간 과제 후보 — 흐름 요약 바로 아래.
     흐름은 "무슨 일이 있었나", 이건 "그래서 눈여겨볼 게 무엇인가"다.
     관찰(사실)과 함의(해석)를 줄로 갈라 어디까지가 자료인지 보이게 한다. */
  $('#week-tasks-wrap').hidden = !(DIGEST.tasks || []).length;
  $('#week-tasks').innerHTML = (DIGEST.tasks || []).map(t => `<div class="wtask">
      <div class="wtask-h">${esc(t.title || '')}</div>
      <div class="wtask-r"><span class="wtask-k">관찰</span><span>${esc(t.fact || '')}</span></div>
      <div class="wtask-r"><span class="wtask-k">함의</span><span>${esc(t.mean || '')}</span></div>
      <div class="wtask-r"><span class="wtask-k">확인</span><span>${esc(t.ask || '')}</span></div>
    </div>`).join('');
  renderDigest();
  if ($('#week-select').options.length === 0) loadWeekOptions(DIGEST.week);
  else $('#week-select').value = DIGEST.week;
  if (!$('#side-bridge').dataset.done) loadSideBridge();
}

/* 회차는 별도 탭이 아니라 주차 선택으로 둔다 — 지난 주를 보는 건 별도 화면이
   필요한 일이 아니라 같은 화면의 날짜만 바꾸는 일이다.

   ★ 시작할 때 채운다. 전에는 '이번 주' 탭을 열어야 채워졌는데, 이 선택창은
     사이드바에 **늘 보인다.** 홈에서 시작하면 비어 있어서 고장으로 보였다.
     보이는 것과 채워지는 시점이 어긋나면 그건 버그로 읽힌다. */
async function loadWeekOptions(cur) {
  const w = await api('/api/weeks', { limit: 60 });
  $('#week-select').innerHTML = w.weeks.map(x =>
    `<option value="${esc(x.week)}" ${x.week === cur ? 'selected' : ''}>${esc(x.label)}</option>`).join('');
}
$('#week-select').onchange = e => {
  // 홈에서 주차를 고르면 그 주차 지면으로 넘어가야 한다 — 고르기만 하고
  // 아무 일도 안 일어나면 선택창이 왜 있는지 알 수 없다.
  showTab('digest');
  loaded.add('digest');
  loadDigest(e.target.value);
};
$('#axis-chips').onclick = e => {
  const b = e.target.closest('[data-axis]');
  if (b) { activeAxis = b.dataset.axis; renderDigest(); }
};

async function loadSideBridge() {
  const g = await api('/api/graph', { top: 8 });
  $('#side-bridge').dataset.done = '1';
  $('#side-bridge').innerHTML = (g.bridges || []).length
    ? g.bridges.map(b => `<div class="mini" data-kw="${esc(b.keyword)}">
        <span class="k">${esc(b.keyword)}</span>
        <span class="v">${b.spans.map(s => esc(label(s))).join(' · ')}</span></div>`).join('')
    : '<div class="empty">—</div>';
}

/* ── ② 검색 ── */
const params = () => ({
  q: $('#f-q').value.trim(), axis: $('#f-axis').value,
  since: $('#f-since').value.trim(), until: $('#f-until').value.trim(),
  kept_only: 1,
});

/* 한 화면에 50줄은 끝까지 훑기 전에 지친다. 25줄이면 한 화면에 들어온다.
   대신 쪽수가 늘어나므로 번호 페이지가 같이 필요하다. */
const PAGE_SIZE = 25;

/* 이전/다음만 있으면 "지금 몇 쪽인지", "몇 쪽까지 있는지"를 알 수 없고
   뒤쪽으로 건너뛸 방법도 없다. 앞뒤 2쪽씩과 처음·끝을 항상 보여준다. */
function renderPager(page, total, size) {
  const last = Math.max(1, Math.ceil(total / size));
  if (last <= 1) { $('#search-pager').innerHTML = ''; return; }
  const nums = new Set([1, last]);
  for (let i = page - 2; i <= page + 2; i++) if (i > 0 && i <= last) nums.add(i);
  const sorted = [...nums].sort((a, b) => a - b);

  const out = [`<button data-page="${page - 1}" ${page === 1 ? 'disabled' : ''}>←</button>`];
  sorted.forEach((n, i) => {
    if (i && n - sorted[i - 1] > 1) out.push('<span class="gap">…</span>');
    out.push(`<button data-page="${n}" class="${n === page ? 'cur' : ''}">${n}</button>`);
  });
  out.push(`<button data-page="${page + 1}" ${page === last ? 'disabled' : ''}>→</button>`);
  $('#search-pager').innerHTML = out.join('');
}

async function runSearch(page = 1) {
  const p = { ...params(), page, size: PAGE_SIZE };
  $('#search-body').innerHTML = '<div class="empty">검색 중…</div>';
  // 같은 검색어가 법령·연관어에도 걸리는지 함께 찾는다 (첫 페이지에서만)
  if (page === 1) loadSearchOther((p.q || '').trim());
  const r = await api('/api/search', p);
  const from = (page - 1) * r.size;
  $('#search-count').textContent = r.total
    ? `${num(r.total)}건 중 ${from + 1}~${from + r.items.length}` : '';
  $('#search-body').innerHTML = r.items.length
    ? `<div class="tblwrap"><table><tr><th>날짜</th><th>제목</th><th>주제</th><th>출처</th></tr>` +
      r.items.map(p => `<tr>
        <td class="n">${esc(p.published)}</td>
        <td class="t"><a href="#" data-item="${p.id}">${esc(p.title)}</a>
          <a href="${esc(p.url)}" target="_blank" rel="noopener" class="src-link"
             title="원문으로 이동">원문 ↗</a>
          ${p.insight ? `<span class="snip"><svg class="ico"><use href="#i-bulb"/></svg> ${esc(p.insight)}</span>`
            : (p.summary ? `<span class="snip">${esc(p.summary)}</span>` : '')}</td>
        <td>${tags(p.axes)}</td>
        <td class="n">${esc(p.source)}</td></tr>`).join('') + '</table></div>'
    : '<div class="empty">결과가 없습니다.</div>';

  renderPager(page, r.total, r.size);
  $('#f-csv').href = '/search.csv?' + new URLSearchParams(params()).toString();
}
$('#search-form').onsubmit = e => { e.preventDefault(); runSearch(1); };
$('#search-pager').onclick = e => {
  const b = e.target.closest('[data-page]'); if (b) runSearch(+b.dataset.page);
};

const PRESETS = [['최근 1개월', 30], ['최근 3개월', 90], ['최근 1년', 365], ['전체 기간', 0]];
function initSearch() {
  $('#f-axis').innerHTML = '<option value="">전체 주제</option>' +
    AXES.map(a => `<option value="${esc(a.key)}">${esc(a.label)}</option>`).join('');
  $('#presets').innerHTML = PRESETS.map(([t, d]) =>
    `<button type="button" class="preset" data-days="${d}">${t}</button>`).join('');
  $('#presets').onclick = e => {
    const b = e.target.closest('[data-days]'); if (!b) return;
    const d = +b.dataset.days;
    if (!d) { $('#f-since').value = ''; $('#f-until').value = ''; }
    else {
      $('#f-until').value = new Date().toISOString().slice(0, 10);
      $('#f-since').value = new Date(Date.now() - d * 864e5).toISOString().slice(0, 10);
    }
    runSearch(1);
  };
  runSearch(1);
}

/* ── ③ 연관어 네트워크 (검색형) ──
 * 전체 코퍼스로 그린 고정 지도가 아니라, 검색한 키워드 주변만 그린다.
 * 전체를 400 노드로 압축하면 어느 주제에도 안 맞는 그림이 되고 일반어가 상위를 먹는다.
 * 실제 질문은 "지금 보는 주제 옆에 뭐가 있나"이지 "연관어 전체 지도"가 아니다.
 *
 * 배치는 물리 시뮬레이션이 아니라 중심 키워드를 가운데 두고 NPMI 순으로 둘레에
 * 놓는 방사형이다. 결정적이라 새로고침해도 같은 그림이고 드래그 처리가 필요 없다.
 * 색은 그 키워드가 주로 어느 주제 기사에 나오는지를 나타낸다. */
async function loadSuggest() {
  const s = await api('/api/suggest', { limit: 40 });
  $('#ego-list').innerHTML = s.items.map(i => `<option value="${esc(i.keyword)}">`).join('');
  // 자주 쓸 만한 출발점 몇 개는 버튼으로 — 빈 화면에서 뭘 쳐야 할지 모르는 걸 막는다
}

let EGO = null, egoSel = null;

async function loadEgo(kw, hops) {
  if (!kw) return;
  $('#ego-q').value = kw;
  hops = hops || +($('#ego-hops')?.value || 1);
  $('#graph-svg').innerHTML = '<div class="empty">그리는 중…</div>';
  const g = await api('/api/ego', { kw, hops, per_hop: hops > 1 ? 8 : 14 });
  if (g.empty) {
    // 이전 검색의 지도 상태가 남으면 휠·드래그가 없는 그림을 계속 만진다
    EGO = null; BASE = VIEW = null;
    $('#graph-tools').hidden = true;
    $('#graph-svg').innerHTML = (await indexState()).ready
      ? `<div class="empty">'${esc(kw)}' — ${esc(g.reason || '결과가 없습니다.')}</div>`
      : await emptyOrBuilding('');
    return;
  }
  EGO = g;
  $('#graph-tools').hidden = false;
  // 색이 무엇을 뜻하는지 화면에 적어둔다 — 색만 칠해두고 설명이 없으면
  // 보는 사람은 그냥 알록달록한 점으로 읽는다.
  $('#graph-legend').innerHTML = Object.entries(AXES || {})
    .map(([k, v]) => `<b><i style="background:var(--ax-${k})"></i>${esc(v.label || k)}</b>`)
    .join('') + '<b class="mut">점 크기 = 기사 수</b>';
  $('#ego-hops').value = hops;
  $('#hop-label').textContent = RANGE_LABEL[hops] || hops;
  const far = g.nodes.filter(n => n.hop >= 2).length;
  $('#graph-stat').textContent = `${g.nodes.length}개 키워드 · ${g.edges.length}개 연결`
    + (far ? ` · 2단계 ${far}개` : '');
  drawEgo(g);
  selectNode(g.center);
}

/** 라벨 배경(알약)을 그리려면 글자 폭이 필요한데 SVG는 그리기 전엔 못 잰다.
 *  한글·전각은 한 칸, ASCII는 대략 0.56칸으로 어림한다. */
function textW(s, fs) {
  let u = 0;
  for (const c of s) u += c.charCodeAt(0) < 128 ? .56 : 1;
  return u * fs;
}


/** 노드 반지름 = 기사 건수.
 *
 *  ★ 로그를 쓴다. 건수가 3건에서 15,000건까지 네 자릿수를 넘나들기 때문이다.
 *     제곱근 척도로 최댓값에 맞춰 정규화했더니 링 노드들이 전부 뭉개졌다
 *     — 'AI모델' 1홉에서 중심 324건이 척도를 잡아먹어 링 11종이 7.2~8.6px에
 *     들어갔다. 로그로 바꾸면 같은 경우가 13.1~18.1px로 벌어진다.
 *
 *  ★ 중심을 고정 크기로 두지 않는다. 중심이 항상 큰 게 아니었다 —
 *     'Claude'(873건)를 검색하면 링에 2,096건짜리가 있다.
 */
const RADIUS = df => Math.min(26, Math.max(7, 5 + 2.7 * Math.log(Math.max(df, 1))));

let LAYOUT = null;      // {pos, home, rad, inc, el} — 노드를 끌어 옮기려면 좌표를 들고 있어야 한다

/* ── 배치: 힘 기반 (Obsidian 방식) ────────────────────────────────
 * 동심원 배치를 버렸다. 원은 규칙적이라 읽기 쉬울 것 같지만, 실제로는
 *   · 모든 노드가 중심에서 같은 거리에 서서 "무엇이 가까운지"가 안 보이고
 *   · 선이 전부 중심을 향해 방사형으로 뻗어 살처럼 보이고
 *   · 라벨이 원을 따라 줄지어 서서 서로를 가린다
 * 힘 기반은 관계가 강한 것끼리 저절로 뭉쳐서, 배치 자체가 정보가 된다.
 *
 * 물리 엔진을 쓰지 않는다. 반발(모든 쌍) + 인력(간선) + 중심 수렴을 정해진
 * 횟수만큼 돌린다. 난수 씨앗을 키워드로 고정해 **같은 검색은 같은 그림**이 된다
 * — 새로고침마다 모양이 바뀌면 어제 본 것과 비교할 수 없다.
 */
function layout(g, rad) {
  const N = g.nodes.length;
  const W = 1000, H = 700, cx = W / 2, cy = H / 2;
  let seed = 0;
  for (const ch of g.center) seed = (seed * 31 + ch.charCodeAt(0)) >>> 0;
  const rnd = () => ((seed = (seed * 1103515245 + 12345) >>> 0) / 4294967296);

  const idx = new Map(g.nodes.map((n, i) => [n.keyword, i]));
  const P = g.nodes.map((n, i) => {
    if (n.center) return { x: cx, y: cy, vx: 0, vy: 0 };
    // 초기 위치는 링이지만 시작점일 뿐이다 — 힘이 곧 재배치한다
    const a = (i / N) * Math.PI * 2 + rnd() * .6;
    const r = 150 + rnd() * 180;
    return { x: cx + Math.cos(a) * r, y: cy + Math.sin(a) * r, vx: 0, vy: 0 };
  });

  const links = [];
  for (const e of g.edges || []) {
    const a = idx.get(e.source), b = idx.get(e.target);
    if (a !== undefined && b !== undefined) links.push([a, b, e.npmi]);
  }

  const STEPS = 260;
  for (let step = 0; step < STEPS; step++) {
    const cool = 1 - step / STEPS;
    // 반발 — 노드가 서로 밀어낸다. 크기가 클수록 더 넓은 자리를 차지한다.
    for (let i = 0; i < N; i++) {
      for (let j = i + 1; j < N; j++) {
        let dx = P[j].x - P[i].x, dy = P[j].y - P[i].y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = rnd() - .5; dy = rnd() - .5; d2 = 1; }
        const d = Math.sqrt(d2);
        const want = rad[g.nodes[i].keyword] + rad[g.nodes[j].keyword] + 54;
        const f = (want * want * 2.4) / d2;
        const ux = dx / d, uy = dy / d;
        P[i].vx -= ux * f; P[i].vy -= uy * f;
        P[j].vx += ux * f; P[j].vy += uy * f;
      }
    }
    // 인력 — 이어진 것끼리 당긴다. 관계가 강할수록(NPMI) 더 가깝게.
    for (const [a, b, w] of links) {
      const dx = P[b].x - P[a].x, dy = P[b].y - P[a].y;
      const d = Math.hypot(dx, dy) || 1;
      const rest = 150 - 60 * Math.min(1, w);
      const f = (d - rest) * 0.012 * (0.4 + w);
      const ux = dx / d, uy = dy / d;
      P[a].vx += ux * f; P[a].vy += uy * f;
      P[b].vx -= ux * f; P[b].vy -= uy * f;
    }
    // 중심으로 약하게 모아 화면 밖으로 흩어지지 않게 한다
    for (let i = 0; i < N; i++) {
      P[i].vx += (cx - P[i].x) * 0.0016;
      P[i].vy += (cy - P[i].y) * 0.0016;
      if (g.nodes[i].center) { P[i].vx *= .25; P[i].vy *= .25; }
      P[i].x += P[i].vx * cool * .55;
      P[i].y += P[i].vy * cool * .55;
      P[i].vx *= .82; P[i].vy *= .82;
    }
  }

  const pos = {};
  g.nodes.forEach((n, i) => (pos[n.keyword] = [P[i].x, P[i].y]));
  return pos;
}

function drawEgo(g) {
  const maxDf = Math.max(...g.nodes.map(n => n.df || 1));
  const rad = {}, fsz = {};
  g.nodes.forEach(n => {
    rad[n.keyword] = RADIUS(n.df);
    fsz[n.keyword] = n.center ? 15 : Math.max(11.5, 14 - (n.df ? 0 : 1));
  });

  const pos = layout(g, rad);
  // 배치가 끝난 뒤 실제 범위에 맞춰 화면을 잡는다 — 미리 정하면 여백이 남거나 잘린다
  const xs = Object.values(pos).map(p => p[0]), ys = Object.values(pos).map(p => p[1]);
  const pad = 90;
  const x0 = Math.min(...xs) - pad, y0 = Math.min(...ys) - pad;
  const W = Math.max(560, Math.max(...xs) + pad - x0);
  const H = Math.max(420, Math.max(...ys) + pad - y0);
  Object.keys(pos).forEach(k => { pos[k][0] -= x0; pos[k][1] -= y0; });

  const out = [`<svg viewBox="0 0 ${W.toFixed(0)} ${H.toFixed(0)}"
    xmlns="http://www.w3.org/2000/svg">`];

  /* ★ 그리는 순서가 곧 겹치는 순서다: 선 → 노드 → 라벨.
     라벨을 노드와 같이 그리면 옆 노드의 선이 글자 위로 지나간다. 라벨만
     맨 마지막 층에 모아 두면 무엇 위에도 얹히지 않는다. */
  out.push('<g class="edges">');
  (g.edges || []).forEach(e2 => {
    const a = pos[e2.source], b = pos[e2.target];
    if (!a || !b) return;
    // 직선이다. 곡선은 방사형 배치를 감추려던 임시방편이었는데, 힘 배치에서는
    // 선이 이미 사방으로 흩어져서 휘게 할 이유가 없다.
    out.push(`<line class="gedge" data-s="${esc(e2.source)}" data-t="${esc(e2.target)}"
      x1="${a[0].toFixed(1)}" y1="${a[1].toFixed(1)}"
      x2="${b[0].toFixed(1)}" y2="${b[1].toFixed(1)}"
      stroke-opacity="${(0.10 + e2.npmi * 0.42).toFixed(3)}"
      stroke-width="${(0.8 + e2.npmi * 1.1).toFixed(2)}"/>`);
  });
  out.push('</g><g class="dots">');
  g.nodes.forEach(n => {
    const [x, y] = pos[n.keyword], r = rad[n.keyword];
    out.push(`<g class="gnode${n.keyword === egoSel ? ' sel' : ''}"
      data-node="${esc(n.keyword)}">
      <title>${esc(n.keyword)} · ${n.df}건</title>
      <circle class="dot" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}"
        r="${r.toFixed(1)}" fill="var(--ax-${n.axis || 'ai'})"/>
    </g>`);
  });
  out.push('</g><g class="labels">');
  g.nodes.forEach(n => {
    const [x, y] = pos[n.keyword], r = rad[n.keyword], fs = fsz[n.keyword];
    out.push(`<text class="glabel${n.center ? ' glabel-c' : ''}"
      data-label="${esc(n.keyword)}"
      x="${x.toFixed(1)}" y="${(y + r + fs + 3).toFixed(1)}" text-anchor="middle"
      font-size="${fs}">${esc(n.keyword)}</text>`);
  });
  out.push('</g>');
  out.push('</svg>');
  $('#graph-svg').innerHTML = out.join('');

  /* 끌어 옮길 때 만질 것들을 미리 모아둔다.
     노드 요소도 같이 들고 있는다 — 키워드로 셀렉터를 만들면 따옴표·괄호가
     든 키워드에서 깨져서 CSS.escape가 필요해지는데, 참조를 쥐면 그럴 일이 없다. */
  const inc = {}, el = {}, lab = {};
  $$('#graph-svg .gnode').forEach(g2 => (el[g2.dataset.node] = g2));
  // 라벨은 별도 층에 있으므로 노드를 끌 때 같이 옮겨야 한다
  $$('#graph-svg .glabel').forEach(x => (lab[x.dataset.label] = x));
  // 간선이 곡선(path)이라 끌 때 d를 다시 만든다 — 양끝과 제어점을 함께 옮긴다
  $$('#graph-svg line.gedge').forEach(l => {
    (inc[l.dataset.s] = inc[l.dataset.s] || []).push([l, '1']);
    (inc[l.dataset.t] = inc[l.dataset.t] || []).push([l, '2']);
  });
  LAYOUT = { pos, home: JSON.parse(JSON.stringify(pos)), rad, inc, el, lab };

  // 전체가 한눈에 들어오는 상태에서 시작하고, 파고드는 건 사용자가 한다
  BASE = { x: 0, y: 0, w: W, h: H };
  VIEW = { ...BASE };
  applyView();
}

/** 노드 하나를 옮긴다. 다시 그리지 않고 해당 요소와 붙은 선만 만진다 —
 *  전체를 다시 그리면 끌고 있는 동안 버벅이고 선택 상태도 잃는다. */
function moveNode(kw, x, y) {
  if (!LAYOUT) return;
  LAYOUT.pos[kw] = [x, y];
  const h = LAYOUT.home[kw], g = LAYOUT.el[kw], lb = LAYOUT.lab[kw];
  const tr = `translate(${(x - h[0]).toFixed(1)} ${(y - h[1]).toFixed(1)})`;
  if (g) g.setAttribute('transform', tr);
  if (lb) lb.setAttribute('transform', tr);   // 라벨은 별도 층이라 따로 옮긴다
  (LAYOUT.inc[kw] || []).forEach(([el, end]) => {
    el.setAttribute('x' + end, x.toFixed(1));
    el.setAttribute('y' + end, y.toFixed(1));
  });
}

function resetLayout() {
  if (!LAYOUT) return;
  Object.keys(LAYOUT.home).forEach(kw => moveNode(kw, ...LAYOUT.home[kw]));
}

/* ── 지도 조작 ──────────────────────────────────────────────────
 * 라벨 겹침은 간격을 벌려서 못 없앤다 — 노드가 늘면 그만큼 원이 커지고,
 * 전체를 한 화면에 맞추느라 글씨가 다시 작아지기 때문이다(3홉에서 실측).
 * 그래서 '한 장의 그림'을 포기하고 지도로 만든다. 전체 모양은 축소로 보고,
 * 읽을 때는 그쪽으로 확대해 들어간다. viewBox만 움직이므로 다시 그리지 않는다. */
let BASE = null, VIEW = null;

function applyView() {
  const svg = $('#graph-svg svg');
  if (!svg || !VIEW) return;
  svg.setAttribute('viewBox',
    `${VIEW.x.toFixed(1)} ${VIEW.y.toFixed(1)} ${VIEW.w.toFixed(1)} ${VIEW.h.toFixed(1)}`);
  const z = $('#zoom-label');
  if (z) z.textContent = Math.round(BASE.w / VIEW.w * 100) + '%';
}

/** fx,fy = 화면상의 고정점(0~1). 그 지점이 제자리에 남도록 확대한다 —
 *  커서 아래를 보고 있다가 휠을 굴렸는데 딴 데로 튀면 길을 잃는다. */
function zoomBy(k, fx = .5, fy = .5) {
  if (!VIEW) return;
  const w = Math.min(BASE.w * 1.2, Math.max(BASE.w * .12, VIEW.w / k));
  const h = VIEW.h * (w / VIEW.w);
  VIEW = { x: VIEW.x + (VIEW.w - w) * fx, y: VIEW.y + (VIEW.h - h) * fy, w, h };
  applyView();
}

const MAP = $('#graph-svg');
MAP.addEventListener('wheel', e => {
  if (!VIEW) return;
  e.preventDefault();                       // 지도 위에서는 페이지가 안 굴러야 한다
  const r = MAP.getBoundingClientRect();
  if (!r.width || !r.height) return;
  zoomBy(Math.exp(-e.deltaY * .0018),
    (e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height);
}, { passive: false });

/* ★ setPointerCapture를 쓰면 안 된다.
   포인터를 캡처하면 그 뒤의 click·dblclick이 **캡처한 요소로 재타겟**된다.
   즉 e.target이 늘 #graph-svg(div)가 되어 closest('[data-node]')가 null이고,
   노드 클릭·더블클릭이 통째로 죽는다 (실측: 지도를 넣은 뒤 노드 선택 불가).
   대신 window에서 이동/뗌을 듣는다 — 포인터가 요소 밖으로 나가도 따라온다. */
let pan = null, nodeDrag = null, moved = 0;

function onPointerMove(e) {
  const d = nodeDrag || pan;
  if (!d) return;
  const dx = e.clientX - d.sx, dy = e.clientY - d.sy;
  moved = Math.max(moved, Math.abs(dx) + Math.abs(dy));
  const kx = VIEW.w / d.r.width, ky = VIEW.h / d.r.height;
  if (nodeDrag) {
    if (moved < 3) return;          // 클릭할 때의 미세한 흔들림까지 이동으로 치지 않는다
    moveNode(nodeDrag.kw, nodeDrag.p0[0] + dx * kx, nodeDrag.p0[1] + dy * ky);
  } else {
    VIEW.x = pan.vx - dx * kx;
    VIEW.y = pan.vy - dy * ky;
    applyView();
  }
}

function onPointerUp() {
  pan = nodeDrag = null;
  MAP.classList.remove('grabbing');
  window.removeEventListener('pointermove', onPointerMove);
  window.removeEventListener('pointerup', onPointerUp);
}

MAP.addEventListener('pointerdown', e => {
  if (!VIEW || e.button) return;
  const r = MAP.getBoundingClientRect();
  if (!r.width || !r.height) return;
  moved = 0;
  const g = e.target.closest('[data-node]');
  if (g && LAYOUT) {
    // 노드를 잡았으면 지도가 아니라 그 노드가 움직인다
    const kw = g.dataset.node;
    nodeDrag = { kw, sx: e.clientX, sy: e.clientY, p0: [...LAYOUT.pos[kw]], r };
  } else {
    pan = { sx: e.clientX, sy: e.clientY, vx: VIEW.x, vy: VIEW.y, r };
    MAP.classList.add('grabbing');
  }
  window.addEventListener('pointermove', onPointerMove);
  window.addEventListener('pointerup', onPointerUp);
});

$('#graph-tools').addEventListener('click', e => {
  if (e.target.closest('[data-relayout]')) { resetLayout(); return; }
  const b = e.target.closest('[data-zoom]');
  if (!b || !VIEW) return;
  const d = +b.dataset.zoom;
  if (d === 0) { VIEW = { ...BASE }; applyView(); } else zoomBy(d > 0 ? 1.35 : 1 / 1.35);
});

/* 클릭 = 이 키워드의 기사를 옆에 띄운다 (망은 그대로).
   더블클릭 = 그 키워드를 중심으로 다시 그린다.
   망을 유지한 채 여러 노드를 훑어보는 게 기본 동작이어야 한다 — 클릭할 때마다
   그림이 갈아엎히면 어디를 보고 있었는지 잃는다.

   ★ 고른 노드는 **주변을 흐리게 해서** 드러낸다.
     크기를 건수에 맞추고 나니 비슷한 크기가 많아져서, 테두리만으로는 어디를
     골랐는지 찾기 어려워졌다. 색을 바꾸는 방법도 있지만 색은 이미 축(AI·
     빅데이터·소상공인)을 뜻한다 — 크기를 건수에 맞춘 것과 같은 이유로,
     선택 표시하자고 그 뜻을 덮으면 안 된다.
     그래서 색은 그대로 두고 **관계없는 것을 물러나게** 한다:
       고른 노드   테두리 굵게 + 그대로
       이웃 노드   그대로 (누구와 이어졌는지가 알고 싶은 것이다)
       이은 선     진하고 굵게
       나머지      흐리게 */
function selectNode(kw) {
  egoSel = kw;
  const near = new Set([kw]);
  (LAYOUT?.inc[kw] || []).forEach(([l]) => {
    near.add(l.dataset.s);
    near.add(l.dataset.t);
  });
  $$('#graph-svg .gnode').forEach(g => {
    const me = g.dataset.node === kw;
    g.classList.toggle('sel', me);
    g.classList.toggle('dim', !me && !near.has(g.dataset.node));
  });
  $$('#graph-svg .glabel').forEach(x => {
    const me = x.dataset.label === kw;
    x.classList.toggle('sel', me);
    x.classList.toggle('dim', !me && !near.has(x.dataset.label));
  });
  $$('#graph-svg line.gedge').forEach(l => {
    const hot = l.dataset.s === kw || l.dataset.t === kw;
    l.classList.toggle('hot', hot);
    l.classList.toggle('dim', !hot);
  });
  showKeyword(kw);
}

$('#graph-svg').addEventListener('click', e => {
  if (moved > 5) return;      // 지도를 끌었을 뿐이다 — 노드를 고른 게 아니다
  const g = e.target.closest('[data-node]');
  if (g) selectNode(g.dataset.node);
});
$('#graph-svg').addEventListener('dblclick', e => {
  const g = e.target.closest('[data-node]');
  if (g) loadEgo(g.dataset.node);
});
$('#ego-form').onsubmit = e => { e.preventDefault(); loadEgo($('#ego-q').value.trim()); };
// 검색 뒤에도 범위를 늘렸다 줄였다 할 수 있어야 한다 — 몇 홉이 맞는지는
// 그려보기 전에는 모른다.
// 슬라이더 값은 '몇 홉까지 찾아볼까'다. 표시되는 노드의 홉은 간선 기준으로
// 다시 계산되므로, 2로 올려도 대부분 1홉으로 나오는 게 정상이다.
const RANGE_LABEL = { 1: '좁게', 2: '보통', 3: '넓게' };
$('#ego-hops').oninput = e => {
  $('#hop-label').textContent = RANGE_LABEL[e.target.value] || e.target.value;
};
$('#ego-hops').onchange = e => {
  const v = $('#ego-q').value.trim();
  if (v) loadEgo(v, +e.target.value);
};

async function loadSuggest() {
  const s = await api('/api/suggest', { limit: 40 });
  $('#ego-list').innerHTML = s.items.map(i => `<option value="${esc(i.keyword)}">`).join('');
}
async function loadGraph() { await loadSuggest(); }

/* 오른쪽 패널 = 선택한 키워드의 **기사(소스)** + 그 키워드와 가까운 말들.
   그래프에서 뭔가를 발견해도 기사로 확인할 수 없으면 과제 후보로 못 쓴다. */
async function showKeyword(kw) {
  $('#kw-card').hidden = false;
  $('#kw-title').textContent = kw;
  $('#kw-count').textContent = '';
  $('#kw-body').innerHTML = '<div class="empty">불러오는 중…</div>';

  // 지금 그려진 망에서 이 노드와 이어진 말들을 칩으로 — 옆으로 옮겨 다니기 쉽게
  const near = (EGO?.edges || [])
    .filter(e => e.source === kw || e.target === kw)
    .sort((a, b) => b.npmi - a.npmi).slice(0, 8)
    .map(e => (e.source === kw ? e.target : e.source));
  $('#kw-related').innerHTML = near.length
    ? near.map(k => `<button class="chip" data-node="${esc(k)}">${esc(k)}</button>`).join('')
    : '';

  const r = await api('/api/keyword/' + encodeURIComponent(kw), { limit: 40 });
  $('#kw-count').textContent = r.total
    ? `기사 ${num(r.total)}건` + (r.kept ? ` · 다이제스트 ${r.kept}건` : '') : '';
  $('#kw-body').innerHTML = r.items.length
    ? `<div class="items" style="padding:0;border:0;margin:0">
        ${r.items.map(itemHTML).join('')}</div>`
    : '<div class="empty">기사가 없습니다.</div>';
}
$('#kw-related').addEventListener('click', e => {
  const b = e.target.closest('[data-node]');
  if (b) selectNode(b.dataset.node);
});

/* ── ④ 급상승 ── */
let TREND = null;

/* 상승폭이 무엇인지 화면에서 설명한다.
   숫자만 있으면 "38배"가 뭘 기준으로 38배인지 알 수 없다.
   실제 계산식(src/trend.py)을 그대로 옮긴다 — 설명을 지어내면 안 된다. */
const RISE_TIP = (weeks) => `이번 주 비중 ÷ 직전 ${weeks}주 평균 비중

비중 = 그 주 통과 기사 중 이 키워드가 나온 비율.
건수가 아니라 비중으로 재는 이유는, 수집량이 늘면
모든 키워드가 같이 늘어서 전부 급상승으로 보이기 때문입니다.

직전에 한 번도 안 나온 신규 키워드는
상승폭이 이번 주 건수와 같아집니다.`;

const infoIcon = (tip) => `<i class="info">?<span class="tip">${esc(tip)}</span></i>`;

/* 말풍선 위치를 손으로 잡는다.
   CSS만으로 두면 .tblwrap(overflow-x:auto) 같은 스크롤 컨테이너가 잘라내서
   설명이 아예 안 보였다. position:fixed로 빼내면 잘리지 않는 대신 위치를
   조상 기준으로 계산할 수 없어서, 아이콘의 화면 좌표를 보고 직접 놓는다.
   화면 밖으로 나가지 않게 좌우를 8px 안쪽으로 물린다. */
document.body.addEventListener('mouseover', e => {
  const ic = e.target.closest('.info');
  if (!ic) return;
  const tip = ic.querySelector('.tip');
  if (!tip) return;
  const r = ic.getBoundingClientRect();
  const w = tip.offsetWidth || 290, h = tip.offsetHeight || 120;
  let left = r.left + r.width / 2 - w / 2;
  left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
  // 위에 자리가 없으면 아래로 내린다
  const top = r.top - h - 10 >= 8 ? r.top - h - 10 : r.bottom + 10;
  tip.style.left = left + 'px';
  tip.style.top = top + 'px';
});

async function loadTrend() {
  const t = await api('/api/trend', { top: 20 });
  TREND = t.rows || [];
  $('#trend-week').textContent = t.week_label || '';
  const max = Math.max(...t.rows.map(r => r.score));
  if (!t.rows?.length) { $('#trend-body').innerHTML = await emptyOrBuilding('데이터가 없습니다.'); return; }
  $('#trend-body').innerHTML = `<div class="tblwrap"><table>
      <tr><th style="width:44px;text-align:center">순위</th><th>키워드</th>
        <th>이번 주</th>
        <th>상승폭${infoIcon(RISE_TIP(t.compare_weeks || 4))}</th><th></th></tr>` +
    t.rows.map((r, i) => `<tr>
        <td class="rank${i < 3 ? ' top' : ''}">${i + 1}</td>
        <td><a href="#" data-kwpop="${esc(r.keyword)}"><b>${esc(r.keyword)}</b></a>
          ${r.is_new ? ' <span class="new">신규</span>' : ''}</td>
        <td class="n">${r.count}건</td>
        <td class="n">${r.score.toFixed(1)}배
          <div class="bar" style="width:${Math.round(r.score / max * 90)}px"></div></td>
        <td class="n"><button class="preset" data-kwpop="${esc(r.keyword)}">추이·연관어</button></td>
      </tr>`).join('') + '</table></div>';
}

/* ── 키워드 팝업 — 추이 + 연관어를 한 화면에 ──
 * 표 안의 작은 스파크라인으로는 0인 주와 데이터 없음이 구분되지 않았고,
 * 무엇보다 "왜 떴는지"를 알려면 함께 나온 말을 봐야 한다. 둘을 같이 띄운다. */
/* 꺾은선. 막대는 "이번 주가 몇 건인가"를 보여주지만, 여기서 궁금한 건
   **올라가는 중인가**다 — 방향은 이어진 선이 훨씬 빨리 읽힌다. */
function lineChart(series, demo) {
  if (!series?.length) return '<div class="empty">데이터가 없습니다.</div>';
  const W = 470, H = 214, L = 34, R = 16, T = 26, B = 38;
  const n = series.length, max = Math.max(1, ...series.map(s => s.n));
  const px = i => n === 1 ? (L + W - R) / 2 : L + i * (W - L - R) / (n - 1);
  const py = v => T + (1 - v / max) * (H - T - B);
  const pts = series.map((s, i) => [px(i), py(s.n)]);
  const o = [`<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg"
      class="${demo ? 'demo' : ''}">`];

  // 가로 눈금 — 값을 눈으로 되짚을 수 있게 최소한만
  [0, .5, 1].forEach(f => {
    const y = py(max * f);
    o.push(`<line x1="${L}" y1="${y.toFixed(1)}" x2="${W - R}" y2="${y.toFixed(1)}"
      stroke="#eef2f7"/><text x="${L - 7}" y="${(y + 4).toFixed(1)}" text-anchor="end"
      font-size="10.5" fill="#b6c0cf">${Math.round(max * f)}</text>`);
  });

  const col = demo ? '#d97706' : 'var(--blue)';
  const d = pts.map((q, i) => `${i ? 'L' : 'M'}${q[0].toFixed(1)} ${q[1].toFixed(1)}`).join(' ');
  o.push(`<path d="${d} L${pts[n - 1][0].toFixed(1)} ${py(0).toFixed(1)}
      L${pts[0][0].toFixed(1)} ${py(0).toFixed(1)}Z" fill="${col}" fill-opacity=".08"/>`);
  o.push(`<path d="${d}" fill="none" stroke="${col}" stroke-width="2.2"
      stroke-linejoin="round" stroke-linecap="round"/>`);

  series.forEach((s, i) => {
    const [x, y] = pts[i], last = i === n - 1;
    o.push(`<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${last ? 5 : 3.4}"
      fill="#fff" stroke="${col}" stroke-width="${last ? 2.6 : 2}"/>`);
    // 값은 마지막과 최고점만 — 전부 찍으면 선이 안 보인다
    if (last || s.n === max)
      o.push(`<text x="${x.toFixed(1)}" y="${(y - 11).toFixed(1)}" text-anchor="middle"
        font-size="11.5" font-weight="700" fill="${col}">${s.n}</text>`);
    // x축은 'W31'이 아니라 '8월 4주차' — 몇 월인지 세지 않아도 되게
    if (n <= 9 || i % 2 === 0 || last)
      o.push(`<text x="${x.toFixed(1)}" y="${H - 14}" text-anchor="middle"
        font-size="10.5" fill="#94a3b8">${esc(s.short || s.week)}</text>`);
  });
  return o.join('') + '</svg>';
}

/* 주차를 아직 한 주밖에 못 모았으면 추이라는 게 성립하지 않는다.
   그래도 이 화면이 뭘 보여줄 자리인지는 보여야 하므로 **예시**를 띄운다.
   - 실제 값이 2주 이상 있으면 이 함수는 아예 호출되지 않는다.
   - 켤 때만 나오고, 배지·색(주황)·축 아래 문구로 실측이 아님을 못 놓치게 한다.
   키워드로 난수를 고정해 같은 말은 늘 같은 그림이 나온다 — 새로고침마다
   모양이 바뀌면 진짜 데이터로 착각할 여지가 커진다. */
function mockSeries(kw, real) {
  let h = 0;
  for (const c of kw) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  const rnd = () => ((h = (h * 1103515245 + 12345) >>> 0) / 4294967296);
  const end = Math.max(6, real[real.length - 1]?.n || 12);
  return real.map((s, i) => {
    const ramp = Math.pow((i + 1) / real.length, 1.9);      // 뒤로 갈수록 오르는 모양
    return { ...s, n: Math.max(0, Math.round(end * ramp * (.55 + rnd() * .75))) };
  });
}

function miniNet(g) {
  if (!g || g.empty || g.nodes.length < 2) return '<div class="empty">연관어가 없습니다.</div>';
  const W = 460, H = 340, cx = W / 2, cy = H / 2;
  const ring = g.nodes.filter(n => !n.center);
  const r0 = Math.max(110, (ring.length * 96) / (2 * Math.PI));
  const pos = { [g.center]: [cx, cy] };
  ring.forEach((n, i) => {
    const t = -Math.PI / 2 + i * 2 * Math.PI / ring.length;
    pos[n.keyword] = [cx + r0 * Math.cos(t) * .82, cy + r0 * Math.sin(t) * .62];
  });
  const out = [`<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">`];
  ring.forEach(n => {
    const [x, y] = pos[n.keyword];
    out.push(`<line x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}"
      stroke="#cbd5e1" stroke-width="1"/>`);
  });
  g.nodes.forEach(n => {
    const [x, y] = pos[n.keyword], r = n.center ? 20 : 8;
    out.push(`<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${r}"
      fill="var(--ax-${n.axis || 'ai'})" fill-opacity="${n.center ? .95 : .55}"/>
      <text x="${x.toFixed(1)}" y="${(y + r + 13).toFixed(1)}" text-anchor="middle"
        class="glabel" font-size="${n.center ? 13 : 11.5}" fill="#334155"
        font-weight="${n.center ? 700 : 500}">${esc(n.keyword)}</text>`);
  });
  return out.join('') + '</svg>';
}

let MSERIES = null, MKW = '';     // 예시 토글이 다시 그릴 때 쓴다

async function showKeywordModal(kw) {
  MKW = kw;
  $('#kwmodal').hidden = false;
  $('#m-title').textContent = kw;
  $('#m-sub').textContent = '';
  $('#m-demo').innerHTML = '';
  $('#m-chart').innerHTML = '<div class="empty">불러오는 중…</div>';
  $('#m-net').innerHTML = '<div class="empty">불러오는 중…</div>';
  $('#m-count').textContent = '';
  $('#m-foot').innerHTML = '';

  // 표에 이미 있는 값이라 /api/trend를 다시 부르지 않는다
  const row = (TREND || []).find(r => r.keyword === kw);
  $('#m-sub').textContent = row
    ? `이번 주 ${row.count}건 · 상승폭 ${row.score.toFixed(1)}배${row.is_new ? ' · 신규' : ''}`
    : '';

  const [s, g, k] = await Promise.all([
    api('/api/series', { kw, weeks: 8 }),
    api('/api/ego', { kw, hops: 1, per_hop: 9 }),
    api('/api/keyword/' + encodeURIComponent(kw), { limit: 40 }),
  ]);
  MSERIES = s.series || [];
  renderMChart(false);
  $('#m-net').innerHTML = miniNet(g);
  $('#m-count').textContent = k.total ? `전체 ${num(k.total)}건 중 ${k.items.length}건` : '';
  $('#m-foot').innerHTML = k.items.length
    ? `<div class="items" style="padding:0;border:0;margin:0">
        ${k.items.map(itemHTML).join('')}</div>`
    : '<div class="empty">기사가 없습니다.</div>';
}

/** 실측이 2주 이상이면 그대로 그린다. 한 주뿐이면 그릴 게 없으므로
 *  '예시로 보기' 버튼을 대신 내준다 — 가짜 값을 먼저 보여주지는 않는다. */
function renderMChart(demo) {
  const real = MSERIES || [];
  const filled = real.filter(s => s.n > 0).length;
  if (demo) {
    $('#m-chart').innerHTML = lineChart(mockSeries(MKW, real), true) +
      `<div class="mut" style="font-size:11.5px;margin-top:2px">
         ↑ 실제 수집값이 아닙니다. 주차가 쌓이면 이 자리에 실측이 들어갑니다.</div>`;
    $('#m-demo').innerHTML = `<span class="demo-badge">예시 데이터</span>
      <button class="preset demo-toggle" data-demo="0">실제값</button>`;
    return;
  }
  $('#m-chart').innerHTML = lineChart(real, false) +
    `<div class="mut" style="font-size:11.5px;margin-top:2px">${real.length}주 중 ${filled}주 수집됨</div>`;
  $('#m-demo').innerHTML =
    `<button class="preset demo-toggle" data-demo="1">예시로 보기</button>`;
}

document.body.addEventListener('click', e => {
  const b = e.target.closest('[data-kwpop]');
  if (b) { e.preventDefault(); showKeywordModal(b.dataset.kwpop); }
  const d = e.target.closest('[data-demo]');
  if (d) renderMChart(d.dataset.demo === '1');
  if (e.target.closest('[data-mclose]')) $('#kwmodal').hidden = true;
});

/* ── 공통 클릭 ── */
document.body.addEventListener('click', async e => {
  const kw = e.target.closest('[data-kw]');
  if (kw) {
    e.preventDefault();
    if (!$('#panel-graph').classList.contains('active')) {
      showTab('graph');
      if (!loaded.has('graph')) { loaded.add('graph'); await loadGraph(); }
    }
    return loadEgo(kw.dataset.kw);   // 망도 그 키워드 중심으로 다시 그린다
  }
  const it = e.target.closest('[data-item]');
  if (it) {
    e.preventDefault();
    const d = await api('/api/item/' + it.dataset.item);
    const m = d.meta || {};
    const isLaw = !!m.target;      // 법령 어댑터가 붙이는 표식
    const badges = isLaw ? `
      ${m['부처'] ? `<span class="rbadge dept">${esc(m['부처'])}</span>` : ''}
      ${m['종류'] ? `<span class="rbadge">${esc(m['종류'])}</span>` : ''}
      ${m['제개정'] ? `<span class="rbadge">${esc(m['제개정'])}</span>` : ''}
      ${m['시행일자'] ? `<span class="rbadge">시행 ${esc(fmtYmd(m['시행일자']))}</span>` : ''}`
      : tags(d.axes);
    $('#drawer-body').innerHTML = `
      <h2 style="font-size:19px;margin:0 30px 8px 0;line-height:1.45">${esc(d.title)}</h2>
      <div class="mut">${badges} ${esc(isLaw ? '법제처' : d.source)} · ${esc(d.published)}</div>
      <a class="btn-src" href="${esc(d.url)}" target="_blank" rel="noopener">
        ${isLaw ? '법제처 원문 보기' : '원문 기사 보기'} <span>↗</span></a>
      ${d.insight ? `<div class="item-i"><svg class="ico"><use href="#i-bulb"/></svg> ${esc(d.insight)}</div>` : ''}
      <p style="font-size:15px;margin-top:14px">${esc(d.summary || '')}</p>
      ${isLaw ? '' : `<div class="sec-title">비슷한 기사</div>
      <div class="items" style="padding:0;box-shadow:none;margin:0">
        ${(d.related || []).map(itemHTML).join('') || '<div class="empty">없습니다.</div>'}</div>`}`;
    $('#drawer').hidden = false;
  }
  if (e.target.closest('[data-close]')) $('#drawer').hidden = true;
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { $('#drawer').hidden = true; $('#kwmodal').hidden = true; }
});

/* ── 시작 ── */
/* ── ⓪ 홈 ────────────────────────────────────────────────────────
 * "이번 주에 무슨 일이 있었나"가 이 한 화면에서 끝나야 한다.
 * 원격 DB라 왕복 하나가 곧 지연이므로 /api/home 한 번으로 다 받는다. */
async function loadHome() {
  const h = await api('/api/home');
  $('#home-title').textContent = h.week_label || '—';
  $('#home-sub').textContent = h.total_kept ? `이번 주 통과 ${num(h.total_kept)}건` : '';
  if (h.lead) {
    $('#home-lead').hidden = false;
    $('#home-lead-t').textContent = h.lead;   // 줄바꿈은 .lead의 white-space가 살린다
  }

  $('#home-trend').innerHTML = (h.trending || []).length
    ? h.trending.map((r, i) => `<div class="hrow">
        <span class="rank${i < 3 ? ' top' : ''}">${i + 1}</span>
        <span class="k" data-kwpop="${esc(r.keyword)}">${esc(r.keyword)}</span>
        ${r.is_new ? '<span class="new">신규</span>' : ''}
        <span class="n">${r.count}건 · ${r.score.toFixed(1)}배</span></div>`).join('')
    : '<div class="empty">데이터가 없습니다.</div>';

  loadMonth();      // 별도 호출 — 월간은 홈보다 훨씬 덜 바뀐다
  $('#home-reg').innerHTML = regHTML(h.regulatory || [], true);
  $('#home-cross').innerHTML = (h.crossing || []).length
    ? `<div class="items" style="padding:0;border:0;margin:0">
        ${h.crossing.map(itemHTML).join('')}</div>`
    : '<div class="empty">항목이 없습니다.</div>';

  // 수집 현황 — 마지막 수집이 3일 넘게 밀리면 빨갛게. 수집은 소급되지 않으므로
  // 멈춘 걸 늦게 알수록 손실이 그대로 쌓인다.
  const today = new Date();
  $('#home-health').innerHTML = `<table class="health">
    <tr><th>소스</th><th>누적</th><th>최근 발행</th><th>최근 수집</th></tr>` +
    (h.health || []).map(s => {
      const d = s.collected ? Math.round((today - new Date(s.collected)) / 86400000) : 999;
      return `<tr><td><b>${esc(s.label || s.source)}</b></td><td>${num(s.total)}건</td>
        <td>${esc(s.latest || '—')}</td>
        <td class="${d > 3 ? 'stale' : ''}">${esc(s.collected || '—')}
          ${d > 3 ? ` (${d}일 전)` : ''}</td></tr>`;
    }).join('') + '</table>';
}

/* ── ③-b 법령·규제 ───────────────────────────────────────────────
 * 제목만으로는 성격을 알 수 없다 — 어느 부처가, 무슨 종류를, 제정인지
 * 개정인지, 언제 시행하는지가 판단에 필요한 정보다. 배지로 같이 보여준다. */
function regHTML(rows, compact) {
  if (!rows.length) return '<div class="empty">항목이 없습니다.</div>';
  const today = new Date();
  return rows.map(r => {
    const days = r.published ? Math.round((today - new Date(r.published)) / 86400000) : 999;
    return `<div class="reg-item">
      <a href="#" data-item="${r.id}">${esc(r.title)}</a>
      <a class="src-link" href="${esc(r.url)}" target="_blank" rel="noopener"
         title="법제처 원문">원문 ↗</a>
      <div class="reg-meta">
        ${days <= 14 ? '<span class="rbadge new">최신</span>' : ''}
        ${r.dept ? `<span class="rbadge dept">${esc(r.dept)}</span>` : ''}
        ${r.kind ? `<span class="rbadge">${esc(r.kind)}</span>` : ''}
        ${r.revision ? `<span class="rbadge">${esc(r.revision)}</span>` : ''}
        <span>발령 ${esc(r.published || '—')}</span>
        ${!compact && r.effective ? `<span>· 시행 ${esc(fmtYmd(r.effective))}</span>` : ''}
      </div></div>`;
  }).join('');
}

const fmtYmd = s => (s && s.length === 8)
  ? `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6)}` : (s || '');

/* 법령은 계속 쌓인다 — 지운 적이 없고 매 수집마다 새 것만 더해진다.
   기간을 안 자르면 몇 달 뒤 오래된 고시와 이번 주 고시가 섞여서, 정작
   "새로 뭐가 나왔나"를 보러 온 사람이 찾지 못한다. 기본은 최근 30일. */
let REG_RANGE = 30;

async function loadReg() {
  $('#reg-body').innerHTML = '<div class="empty">불러오는 중…</div>';
  const r = await api('/api/regulatory', { limit: 200, days: REG_RANGE });
  const all = await api('/api/regulatory', { limit: 1 });
  $('#reg-sub').textContent =
    `${num(r.total)}건` + (REG_RANGE ? ` · 누적 ${num(all.total)}건` : '');
  $('#reg-body').innerHTML = regHTML(r.items || [], false);
}
$('#reg-range').addEventListener('click', e => {
  const b = e.target.closest('[data-range]');
  if (!b) return;
  $$('#reg-range .chip').forEach(c => c.classList.toggle('active', c === b));
  REG_RANGE = +b.dataset.range;
  loadReg();
});

/* 검색어가 다른 화면에도 걸리는지 함께 보여준다.
   전에는 기사만 뒤지고 끝나서, 같은 말이 법령에도 있는지 알 방법이 없었다.
   법령은 건수가 적어 3건까지 그대로 펼치고, 나머지는 그쪽 화면으로 넘긴다. */
async function loadSearchOther(q) {
  const box = $('#search-other');
  if (!q) { box.innerHTML = ''; return; }
  $('#search-title').textContent = `'${q}' 검색`;
  let reg = { items: [], total: 0 };
  try { reg = await api('/api/regulatory', { limit: 3, q }); } catch (e) { /* 무시 */ }

  const regCard = reg.total ? `<div class="card">
      <div class="panel-head"><h2><svg class="ico"><use href="#i-law"/></svg> 법령·규제 ${num(reg.total)}건</h2>
        <button class="linkish" data-regq="${esc(q)}">전체 보기</button></div>
      ${regHTML(reg.items, true)}</div>` : '';

  box.innerHTML = regCard + `<div class="card xrow">
      <span class="mut">'${esc(q)}'의 연관어 망을 그려볼 수 있습니다</span>
      <button class="preset" data-kw="${esc(q)}"><svg class="ico"><use href="#i-graph"/></svg> 연관어 네트워크로 보기</button>
    </div>`;
}
// '전체 보기' → 법령 화면을 그 검색어로 연다
document.body.addEventListener('click', async e => {
  const b = e.target.closest('[data-regq]');
  if (!b) return;
  e.preventDefault();
  showTab('reg');
  if (!loaded.has('reg')) loaded.add('reg');
  $('#reg-body').innerHTML = '<div class="empty">불러오는 중…</div>';
  const r = await api('/api/regulatory', { limit: 200, q: b.dataset.regq });
  $('#reg-sub').textContent = `'${b.dataset.regq}' ${num(r.total)}건`;
  $$('#reg-range .chip').forEach(c => c.classList.remove('active'));
  $('#reg-body').innerHTML = regHTML(r.items || [], false);
});

/* ── 월간 리뷰 (L3) ──────────────────────────────────────────────
 * 주간은 "이번 주에 무슨 일이 있었나", 월간은 "여러 주에 걸쳐 무엇이
 * 이어졌나"다. 홈의 '이번 주 흐름' 바로 아래에 둔다 — 네댓 줄짜리 글이라
 * 페이지를 따로 만들 분량이 아니고, 읽는 순서로도 그 자리가 맞다. */
async function loadMonth(month) {
  const m = await api('/api/monthly', month ? { month } : {});
  if (!m.lead) { $('#home-month').hidden = true; return; }
  $('#home-month').hidden = false;
  // textContent에 태그를 넣으면 글자 그대로 보인다 — 아이콘은 innerHTML이어야 한다
  $('#month-title').innerHTML =
    `<svg class="ico"><use href="#i-calendar"/></svg> ${esc(m.label)} 리뷰`;
  $('#month-select').innerHTML = (m.months || [])
    .map(x => `<option value="${esc(x.month)}"${x.month === m.month ? ' selected' : ''}>
      ${esc(x.label)}</option>`).join('');
  $('#month-lead').textContent = m.lead;
  // 보고서는 SPA 밖의 인쇄용 문서다 — 새 탭으로 연다
  $('#rep-html').href = '/report?month=' + encodeURIComponent(m.month);
  $('#rep-md').href = '/report.md?month=' + encodeURIComponent(m.month);
}
$('#month-select').onchange = e => loadMonth(e.target.value);

/* ── 뉴스레터 ────────────────────────────────────────────────────
 * 보내기 전에 눈으로 볼 수 없는 발송물은 언젠가 이상한 채로 나간다.
 * 실제 메일 HTML을 그대로 iframe에 띄운다 — sandbox를 비워 스크립트를 막고,
 * 페이지 CSS와도 섞이지 않게 한다(메일은 인라인 스타일만 쓴다).
 * 수신자·키워드는 읽기 전용이다. 이 화면에는 로그인이 없어서, 같은 망의
 * 누구나 수신자를 고칠 수 있으면 안 된다. */
async function loadNews() {
  const n = await api('/api/newsletter');
  $('#nl-week').textContent = n.week_label || '';
  $('#nl-subject').textContent = n.subject || '';
  const f = $('#nl-frame');
  f.srcdoc = n.preview || '<p style="font-family:sans-serif;color:#64748b">'
    + '보낼 내용이 아직 없습니다.</p>';

  const s = n.smtp;
  $('#nl-smtp').innerHTML = `
    <div class="hrow"><span>상태</span><span class="n">
      ${s.configured ? '<b style="color:#15803d">설정됨</b>'
                     : '<b style="color:#b91c1c">미설정 — 발송 안 됨</b>'}</span></div>
    <div class="hrow"><span>서버</span><span class="n">${esc(s.host || '—')}:${esc(s.port)}</span></div>
    <div class="hrow"><span>보내는 사람</span><span class="n">${esc(s.from || '—')}</span></div>
    <div class="hrow"><span>받는 사람</span><span class="n">${
      s.to.length ? s.to.map(esc).join('<br>') : '—'}</span></div>
    <div class="hrow"><span>발송 시각</span><span class="n">${esc(n.schedule.digest)}</span></div>
    ${s.configured ? '' : `<div class="mut" style="margin-top:10px">
      .env의 SMTP_HOST · SMTP_USER · SMTP_PASSWORD · MAIL_TO를 채우면 발송됩니다.</div>`}`;

  const a = n.alerts;
  $('#nl-alerts').innerHTML = `
    <div class="hrow"><span>상태</span><span class="n">${a.enabled ? '켜짐' : '꺼짐'}</span></div>
    <div class="hrow"><span>범위</span><span class="n">최근 ${a.days}일</span></div>
    <div class="hrow"><span>발송 시각</span><span class="n">${esc(n.schedule.alert)}</span></div>
    <div class="chip-row" style="margin-top:12px">
      ${a.keywords.map(k => `<span class="chip">${esc(k)}</span>`).join('')
        || '<span class="mut">등록된 키워드가 없습니다</span>'}</div>
    <div class="mut" style="margin-top:10px">config.yaml의 alerts.keywords에서 바꿉니다.</div>`;
}

/* ── 교차 ────────────────────────────────────────────────────────
 * "팀의 업무는 세 축의 교집합에 있다"가 이 도구의 전제인데(CLAUDE.md),
 * 정작 교집합은 다이제스트 다섯 칸에만 보였다. 그 주에 30건이 있어도 5건만
 * 나오고 나머지는 어디에서도 볼 수 없었다. 여기서 전부 본다. */
let CROSS_AXES = 2;   // '정확히 N축'. API 파라미터 이름도 axes다.

async function loadCross() {
  $('#cross-body').innerHTML = '<div class="empty">불러오는 중…</div>';
  const r = await api('/api/cross', { axes: CROSS_AXES, weeks: 8, limit: 80 });
  $('#cross-sub').textContent = `최근 8주 · ${CROSS_AXES}개 축이 걸린 기사 ${num(r.total)}건`;
  $('#cross-body').innerHTML = r.rows.length
    ? `<div class="items" style="padding:0;border:0;margin:0">
        ${r.rows.map(itemHTML).join('')}</div>`
    : '<div class="empty">해당 항목이 없습니다.</div>';
  const max = Math.max(1, ...(r.combos || []).map(c => c.n));
  $('#cross-combos').innerHTML = (r.combos || []).map(c => `<div class="hrow">
      <span>${esc(c.combo)}</span>
      <span class="n">${c.n}건<div class="bar" style="width:${Math.round(c.n / max * 70)}px"></div></span>
    </div>`).join('') || '<div class="empty">—</div>';
  const wmax = Math.max(1, ...(r.weeks || []).map(x => x.n));
  $('#cross-weeks').innerHTML = (r.weeks || []).map(x => `<div class="hrow">
      <span>${esc(x.label.replace(/^\d+년 /, ''))}</span>
      <span class="n">${x.n}건<div class="bar" style="width:${Math.round(x.n / wmax * 70)}px"></div></span>
    </div>`).join('');
}
$('#cross-axes').addEventListener('click', e => {
  const b = e.target.closest('[data-min]');
  if (!b) return;
  $$('#cross-axes .chip').forEach(c => c.classList.toggle('active', c === b));
  CROSS_AXES = +b.dataset.min;
  loadCross();
});

/* ── 기관 ────────────────────────────────────────────────────────
 * 어디가 반복해서 나오는지 보면 협업·벤치마크 대상이 보인다.
 * 여러 주에 걸쳐 나온 것만 남긴다 — 한 주 한 번은 '반복'이 아니다. */
async function loadOrgs() {
  const r = await api('/api/orgs', { weeks: 8, limit: 40 });
  $('#orgs-sub').textContent = `최근 8주 · ${r.rows.length}곳`;
  if (!r.rows.length) { $('#orgs-body').innerHTML = await emptyOrBuilding('반복 등장한 기관이 없습니다.'); return; }
  const wk = r.weeks || [];
  const max = Math.max(1, ...r.rows.flatMap(x => x.series.map(s => s.n)));
  $('#orgs-body').innerHTML = `<div class="tblwrap"><table>
      <tr><th>기관</th><th>등장</th><th>주차</th>
        <th>${wk.map(x => x.label.replace(/^\d+년 /, '')).join('</th><th>')}</th></tr>` +
    r.rows.map(x => `<tr>
      <td><a href="#" data-kw="${esc(x.keyword)}"><b>${esc(x.keyword)}</b></a></td>
      <td class="n">${x.total}건</td><td class="n">${x.weeks}주</td>
      ${x.series.map(s => `<td class="n">${s.n ? `<button class="spark"
        style="opacity:${(0.3 + 0.7 * s.n / max).toFixed(2)}"
        data-orgw="${esc(x.keyword)}|${esc(s.week)}"
        title="${esc(s.label)} ${s.n}건 — 눌러서 기사 보기">${s.n}</button>` : ''}</td>`).join('')}
    </tr>`).join('') + '</table></div>';
}

/* 기관 표의 주차 칸을 누르면 그 주 기사를 서랍에 띄운다.
   숫자만 보여주고 끝나면 "왜 그 주에 늘었나"를 확인할 방법이 없다. */
document.body.addEventListener('click', async e => {
  const b = e.target.closest('[data-orgw]');
  if (!b) return;
  const [kw, week] = b.dataset.orgw.split('|');
  $('#drawer').hidden = false;
  $('#drawer-body').innerHTML = '<div class="empty">불러오는 중…</div>';
  const r = await api('/api/org_items', { kw, week, limit: 40 });
  const wl = r.label || r.week || '';
  $('#drawer-body').innerHTML = `
    <h2 style="font-size:19px;margin:0 30px 6px 0">${esc(kw)}</h2>
    <div class="mut">${esc(wl)} · 이 주 ${r.items.length}건 (전체 ${num(r.total)}건)</div>
    <div class="items" style="padding:0;box-shadow:none;margin:12px 0 0">
      ${r.items.map(itemHTML).join('') || '<div class="empty">기사가 없습니다.</div>'}</div>`;
});

/* ── 분석: 교차 · 기관 · 급상승을 한 메뉴 안의 탭으로 ──────────────
 * 메뉴 항목이 여덟 개까지 늘자 무엇이 어디 있는지 찾기 어려워졌다. 이 셋은
 * 전부 "쌓인 데이터를 각도만 바꿔 보는" 화면이라 한 자리에 묶는 게 맞다.
 * 검색은 본문 맨 위 검색창이 이미 모든 화면에서 닿으므로 메뉴에서 뺐다. */
const SUB_LOADERS = { cross: loadCross, orgs: loadOrgs, trend: loadTrend };
const subLoaded = new Set();

function showSub(name, fromTab) {
  $$('.subtab').forEach(b => b.classList.toggle('active', b.dataset.sub === name));
  $$('.subpanel').forEach(p => p.classList.toggle('active', p.id === 'sub-' + name));
  // 탭 전환에서 불려 온 거면 주소는 이미 맞다. 여기서 또 쓰면 기록이 두 번 쌓인다.
  if (!fromTab && location.hash.slice(1) !== 'analysis/' + name) {
    location.hash = 'analysis/' + name;
  }
  if (!subLoaded.has(name)) { subLoaded.add(name); (SUB_LOADERS[name] || (() => {}))(); }
}
$$('.subtab').forEach(b => (b.onclick = () => showSub(b.dataset.sub)));

/* 급상승·기관·연관어가 비었을 때, 그게 "데이터가 없다"인지 "아직 만드는 중"인지
   화면에 적는다. 배포 직후에는 인덱스를 다시 만드느라 이 셋이 비는데, 아무 설명이
   없으면 기능이 사라진 것으로 보인다. 상태는 한 번만 물어보고 캐시한다. */
let IDX = null;

async function indexState() {
  if (IDX) return IDX;
  try { IDX = await api('/api/index_status'); } catch (e) { IDX = { ready: true }; }
  return IDX;
}

async function emptyOrBuilding(fallback) {
  const s = await indexState();
  if (s.ready) return `<div class="empty">${fallback}</div>`;
  const pct = s.rows ? Math.min(99, Math.round(s.rows / 2418391 * 100)) : 0;
  return `<div class="empty building">
      <b>키워드 인덱스를 만드는 중입니다</b>
      <div style="margin-top:6px">이 화면은 인덱스가 준비되면 채워집니다. 약 10분 걸립니다.
        ${s.rows ? `<br>진행 ${num(s.rows)}행 (${pct}%)` : ''}</div>
      <button class="preset" style="margin-top:12px" data-idxretry>다시 확인</button>
    </div>`;
}
document.body.addEventListener('click', e => {
  if (e.target.closest('[data-idxretry]')) { IDX = null; location.reload(); }
});

const LOADERS = {
  analysis: () => {},          // 서브탭은 showTab이 정한다
  news: loadNews,
  home: loadHome, reg: loadReg,
  digest: () => loadDigest(), search: initSearch, graph: loadGraph, trend: loadTrend,
};

(async () => {
  try {
    AXES = (await api('/api/meta')).axes;
  } catch (err) {
    $('#digest-body').innerHTML =
      `<div class="empty">서버에 연결하지 못했습니다.<br><code>${esc(err.message)}</code></div>`;
    return;
  }
  // 메일의 '전체 보기'가 ?week=2026-W35#digest 로 들어온다
  const wanted = new URLSearchParams(location.search).get('week');
  if (wanted) { loaded.add('digest'); await loadDigest(wanted); }
  loadWeekOptions();          // 사이드바에 늘 보이므로 탭과 무관하게 채운다
  const r = routeOf();
  showTab(r.tab, r.sub);
})();
