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
function showTab(name) {
  $$('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  $$('.panel').forEach(p => p.classList.toggle('active', p.id === 'panel-' + name));
  location.hash = name;
  closeNav();
  window.scrollTo(0, 0);        // 화면을 갈아탔는데 스크롤이 중간에 남아 있으면 길을 잃는다
  if (!loaded.has(name)) { loaded.add(name); (LOADERS[name] || (() => {}))(); }
}

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
  showTab(b.dataset.tab);
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
      ${p.insight ? '<span class="has-ai">💡 해설</span>' : ''}</div>
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
  renderDigest();
  if ($('#week-select').options.length === 0) loadWeekOptions(DIGEST.week);
  else $('#week-select').value = DIGEST.week;
  if (!$('#side-bridge').dataset.done) loadSideBridge();
}

// 회차는 별도 탭이 아니라 주차 선택으로 둔다 — 지난 주를 보는 건
// 별도 화면이 필요한 일이 아니라 같은 화면의 날짜만 바꾸는 일이다.
async function loadWeekOptions(cur) {
  const w = await api('/api/weeks', { limit: 60 });
  $('#week-select').innerHTML = w.weeks.map(x =>
    `<option value="${esc(x.week)}" ${x.week === cur ? 'selected' : ''}>${esc(x.label)}</option>`).join('');
}
$('#week-select').onchange = e => loadDigest(e.target.value);
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
          ${p.insight ? `<span class="snip">💡 ${esc(p.insight)}</span>`
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
    $('#graph-svg').innerHTML =
      `<div class="empty">'${esc(kw)}' — ${esc(g.reason || '결과가 없습니다.')}</div>`;
    return;
  }
  EGO = g;
  $('#graph-tools').hidden = false;
  $('#ego-hops').value = hops;
  $('#hop-label').textContent = hops + '홉';
  $('#graph-stat').textContent = `${g.nodes.length}개 키워드 · ${g.edges.length}개 연결`;
  drawEgo(g);
  selectNode(g.center);
}

/* 배치는 물리 시뮬레이션이 아니라 홉별 동심원이다.
   중심에서 멀수록 관계가 먼 말이라는 게 거리로 보이고, 결정적이라 다시 그려도 같다. */
/** 라벨 배경(알약)을 그리려면 글자 폭이 필요한데 SVG는 그리기 전엔 못 잰다.
 *  한글·전각은 한 칸, ASCII는 대략 0.56칸으로 어림한다. */
function textW(s, fs) {
  let u = 0;
  for (const c of s) u += c.charCodeAt(0) < 128 ? .56 : 1;
  return u * fs;
}

let LAYOUT = null;      // {pos, home, rad, inc, el} — 노드를 끌어 옮기려면 좌표를 들고 있어야 한다

/** 노드 반지름 = 기사 건수. 중심도 예외 없다.
 *
 *  ★ 로그를 쓴다. 건수가 3건에서 15,000건까지 네 자릿수를 넘나들기 때문이다.
 *     제곱근 척도로 가장 큰 값에 맞춰 정규화했더니 링 노드들이 전부 뭉개졌다
 *     — 'AI모델' 1홉에서 중심 324건이 척도를 잡아먹어 링 11종이 7.2~8.6px에
 *     들어갔다. 로그로 바꾸면 같은 경우가 13.1~18.1px로 벌어진다.
 *
 *  ★ 중심을 고정 크기(26)로 두던 걸 없앤다. 중심이 항상 큰 게 아니었다 —
 *     'Claude'(873건)를 검색하면 링에 2,096건짜리가 있다. 고정값은 덜 흔한
 *     말을 더 크게 그려서 크기가 뜻하는 바를 거짓으로 만든다.
 *
 *  ★ 홉이 깊다고 줄이던 것도 없앤다. 크기는 건수만 뜻해야 한다.
 *     먼 홉이라는 건 거리와 투명도가 이미 말해준다. */
const RADIUS = df => Math.min(32, Math.max(9, 7 + 3.4 * Math.log(Math.max(df, 1))));

function drawEgo(g) {
  const byHop = {};
  g.nodes.forEach(n => (byHop[n.hop] = byHop[n.hop] || []).push(n));
  const rad = {}, fsz = {};
  g.nodes.forEach(n => {
    rad[n.keyword] = RADIUS(n.df);
    fsz[n.keyword] = n.center ? 15 : Math.max(11, 13 - n.hop);
  });

  /* ★ 한 홉을 원 하나에 다 세우지 않는다.
     둘레는 반지름에 비례하는데 노드 수는 홉마다 확 늘어서, 24개짜리 홉을
     한 원에 세우면 반지름이 367px까지 튄다. 그러면 이웃 노드끼리는 여전히
     붙어 있으면서 원과 원 사이만 휑해진다 — "너무 떨어져 있다"의 정체다.
     12개씩 나눠 여러 겹으로 돌리면 같은 개수를 절반 반지름에 담는다.
     건수가 큰 것부터 안쪽에 둬서 중요한 게 중심 가까이 오게 한다. */
  const RING_MAX = 12;
  const bands = [];
  Object.keys(byHop).filter(h => +h > 0).map(Number).sort()
    .forEach(h => {
      const arr = byHop[h].slice().sort((x, y) => (y.df || 0) - (x.df || 0));
      const subs = Math.max(1, Math.ceil(arr.length / RING_MAX));
      const per = Math.ceil(arr.length / subs);
      for (let i = 0; i < subs; i++)
        bands.push({ hop: h, nodes: arr.slice(i * per, (i + 1) * per) });
    });

  /* 간격을 상수로 박지 않고 **실제 크기에서 계산**한다.
     노드가 커지거나 라벨이 길어지면 그만큼만 벌어진다 — 상수로 두면
     최악의 경우에 맞춰야 해서 평소에 늘 휑하다. */
  let prev = 0, prevR = rad[g.center], prevFs = fsz[g.center];
  bands.forEach(b => {
    const maxR = Math.max(...b.nodes.map(n => rad[n.keyword]));
    const maxFs = Math.max(...b.nodes.map(n => fsz[n.keyword]));
    // 둘레가 라벨들의 실제 폭 합을 담을 만큼은 되어야 한다
    const need = b.nodes.reduce((s, n) => s + textW(n.keyword, fsz[n.keyword]) + 16, 0)
      / (2 * Math.PI);
    /* 두 겹 사이에 꼭 필요한 거리 = 안쪽 원의 반지름 + 그 아래 라벨 높이
       + 바깥 원의 반지름. 여기에 숨통 8px만 더한다.
       배수(×1.45)로 잡았더니 노드가 커질수록 필요 이상으로 밀어내서,
       모처럼 크기를 건수에 맞췄더니 그림이 도로 휑해졌다. */
    const clearance = prevR + prevFs * 1.35 + maxR + 8;
    b.r = Math.max(prev + clearance, 118, need);
    prev = b.r;
    prevR = maxR;
    prevFs = maxFs;
  });

  const R = bands.length ? bands[bands.length - 1].r : 160;
  const W = Math.round(Math.max(900, R * 2 + 190));
  const H = Math.round(Math.max(620, R * 1.72 + 190));
  const cx = W / 2, cy = H / 2;

  const pos = { [g.center]: [cx, cy] };
  bands.forEach((b, bi) => {
    b.nodes.forEach((n, i) => {
      // 겹마다 시작 각을 반 칸씩 어긋내 안팎이 일직선으로 서지 않게 한다
      const t = -Math.PI / 2 + (i + (bi % 2) * .5) * 2 * Math.PI / b.nodes.length;
      // 세로를 눌러 타원으로 — 가로가 긴 화면을 쓰면서 위아래 여백을 줄인다
      pos[n.keyword] = [cx + b.r * Math.cos(t), cy + b.r * Math.sin(t) * .86];
    });
  });

  const out = [`<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">`];
  // 선에 양끝 키워드를 적어둔다 — 노드를 끌 때 이 선들만 골라 다시 잇는다
  (g.edges || []).forEach(e2 => {
    const a2 = pos[e2.source], b2 = pos[e2.target];
    if (!a2 || !b2) return;
    const mid = e2.source === g.center || e2.target === g.center;
    out.push(`<line data-s="${esc(e2.source)}" data-t="${esc(e2.target)}"
      x1="${a2[0].toFixed(1)}" y1="${a2[1].toFixed(1)}"
      x2="${b2[0].toFixed(1)}" y2="${b2[1].toFixed(1)}" stroke="#94a3b8"
      stroke-opacity="${(e2.npmi * (mid ? .5 : .16)).toFixed(3)}"
      stroke-width="${mid ? 1.5 : 1}"/>`);
  });
  g.nodes.forEach(n => {
    const [x, y] = pos[n.keyword], r = rad[n.keyword], fs = fsz[n.keyword];
    const fade = n.center ? 1 : Math.max(.4, 1 - (n.hop - 1) * .28);
    /* ★ 라벨은 예외 없이 노드 **아래**에 붙인다.
       전에는 이웃끼리 높이를 어긋내려고 위/아래를 번갈아 놨는데, 겹침은
       조금 줄었지만 같은 원 위의 말들이 들쭉날쭉해서 훑어 읽기가 더 나빴다.
       대신 흰 알약을 깔아 선 위에 겹쳐도 글자가 죽지 않게 한다. */
    const ly = y + r + fs + 2;
    const tw = textW(n.keyword, fs);
    out.push(`<g class="gnode${n.keyword === egoSel ? ' sel' : ''}"
      data-node="${esc(n.keyword)}">
      <title>${esc(n.keyword)} · ${n.df}건${n.center ? '' : ` · ${n.hop}홉`}</title>
      <circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${r.toFixed(1)}"
        fill="var(--ax-${n.axis || 'ai'})" fill-opacity="${fade.toFixed(2)}"/>
      <rect class="lbl-bg" x="${(x - tw / 2 - 5).toFixed(1)}" y="${(ly - fs * .85).toFixed(1)}"
        width="${(tw + 10).toFixed(1)}" height="${(fs * 1.22).toFixed(1)}" rx="4"/>
      <text x="${x.toFixed(1)}" y="${ly.toFixed(1)}" text-anchor="middle"
        class="glabel${n.center ? ' glabel-bridge' : ''}" fill="#334155"
        font-size="${fs}"
        opacity="${n.center ? 1 : Math.max(.72, 1 - (n.hop - 1) * .16)}"
        >${esc(n.keyword)}</text>
    </g>`);
  });
  out.push('</svg>');
  $('#graph-svg').innerHTML = out.join('');

  /* 끌어 옮길 때 만질 것들을 미리 모아둔다.
     노드 요소도 같이 들고 있는다 — 키워드로 셀렉터를 만들면 따옴표·괄호가
     든 키워드에서 깨져서 CSS.escape가 필요해지는데, 참조를 쥐면 그럴 일이 없다. */
  const inc = {}, el = {};
  $$('#graph-svg .gnode').forEach(g2 => (el[g2.dataset.node] = g2));
  $$('#graph-svg line').forEach(l => {
    (inc[l.dataset.s] = inc[l.dataset.s] || []).push([l, '1']);
    (inc[l.dataset.t] = inc[l.dataset.t] || []).push([l, '2']);
  });
  LAYOUT = { pos, home: JSON.parse(JSON.stringify(pos)), rad, inc, el };

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
  const h = LAYOUT.home[kw], g = LAYOUT.el[kw];
  if (g) g.setAttribute('transform', `translate(${(x - h[0]).toFixed(1)} ${(y - h[1]).toFixed(1)})`);
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
  $$('#graph-svg line').forEach(l => {
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
$('#ego-hops').oninput = e => { $('#hop-label').textContent = e.target.value + '홉'; };
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
  if (!t.rows?.length) { $('#trend-body').innerHTML = '<div class="empty">데이터가 없습니다.</div>'; return; }
  const max = Math.max(...t.rows.map(r => r.score));
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
      ${d.insight ? `<div class="item-i">💡 ${esc(d.insight)}</div>` : ''}
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
      <div class="panel-head"><h2>⚖️ 법령·규제 ${num(reg.total)}건</h2>
        <button class="linkish" data-regq="${esc(q)}">전체 보기</button></div>
      ${regHTML(reg.items, true)}</div>` : '';

  box.innerHTML = regCard + `<div class="card xrow">
      <span class="mut">'${esc(q)}'의 연관어 망을 그려볼 수 있습니다</span>
      <button class="preset" data-kw="${esc(q)}">🕸️ 연관어 네트워크로 보기</button>
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
  $('#month-title').textContent = `🗓️ ${m.label} 리뷰`;
  $('#month-sub').textContent = m.weeks
    ? `${m.weeks}개 주차 · 통과 ${num(m.kept)}건` : '';
  $('#month-select').innerHTML = (m.months || [])
    .map(x => `<option value="${esc(x.month)}"${x.month === m.month ? ' selected' : ''}>
      ${esc(x.label)}</option>`).join('');
  $('#month-lead').textContent = m.lead;
}
$('#month-select').onchange = e => loadMonth(e.target.value);

const LOADERS = {
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
  const tab = (location.hash || '#home').slice(1);
  showTab(LOADERS[tab] ? tab : 'home');
})();
