/* AI 빅데이터 트렌드 — SPA
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
  $$('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  $$('.panel').forEach(p => p.classList.toggle('active', p.id === 'panel-' + name));
  location.hash = name;
  if (!loaded.has(name)) { loaded.add(name); (LOADERS[name] || (() => {}))(); }
}
$$('.tab-btn').forEach(b => b.onclick = () => showTab(b.dataset.tab));

/* ── 항목 카드 ── */
/* 제목을 누르면 상세가 열리고, 원문은 그 안에서 또는 옆의 링크로 간다.
   목록에서는 AI 해설을 접어둔다 — 20건이 늘어서면 해설이 목록을 밀어내
   무엇이 있는지 훑는 일 자체가 어려워진다. */
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

async function runSearch(page = 1) {
  const p = { ...params(), page, size: 50 };
  $('#search-body').innerHTML = '<div class="empty">검색 중…</div>';
  const r = await api('/api/search', p);
  const from = (page - 1) * r.size;
  $('#search-count').textContent = r.total
    ? `${num(r.total)}건 중 ${from + 1}~${from + r.items.length}` : '';
  $('#search-body').innerHTML = r.items.length
    ? `<div class="tblwrap"><table><tr><th>날짜</th><th>제목</th><th>주제</th><th>출처</th></tr>` +
      r.items.map(p => `<tr>
        <td class="n">${esc(p.published)}</td>
        <td><a href="#" data-item="${p.id}">${esc(p.title)}</a>
          <a href="${esc(p.url)}" target="_blank" rel="noopener" class="src-link"
             title="원문으로 이동">원문 ↗</a>
          ${p.insight ? '<span class="has-ai">💡</span>' : ''}</td>
        <td>${tags(p.axes)}</td>
        <td class="n">${esc(p.source)}</td></tr>`).join('') + '</table></div>'
    : '<div class="empty">결과가 없습니다.</div>';

  const nav = [];
  if (page > 1) nav.push(`<button class="preset" data-page="${page - 1}">← 이전</button>`);
  if (from + r.size < r.total) nav.push(`<button class="preset" data-page="${page + 1}">다음 →</button>`);
  $('#search-pager').innerHTML = nav.join('');
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
function drawEgo(g) {
  const W = 900, H = 660, cx = W / 2, cy = H / 2;
  const maxHop = Math.max(...g.nodes.map(n => n.hop));
  const pos = { [g.center]: [cx, cy] }, byHop = {};
  g.nodes.forEach(n => (byHop[n.hop] = byHop[n.hop] || []).push(n));
  Object.keys(byHop).filter(h => +h > 0).forEach(hs => {
    const h = +hs, arr = byHop[hs];
    const r = 140 + (h - 1) * (maxHop > 1 ? 200 / maxHop : 0) + (h - 1) * 55;
    arr.forEach((n, i) => {
      const t = -Math.PI / 2 + (i + (h % 2) * .5) * 2 * Math.PI / arr.length;
      pos[n.keyword] = [cx + r * Math.cos(t), cy + r * Math.sin(t) * .78];
    });
  });
  const maxDf = Math.max(...g.nodes.map(n => n.df || 1));
  const out = [`<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">`];
  (g.edges || []).forEach(e2 => {
    const a = pos[e2.source], b = pos[e2.target];
    if (!a || !b) return;
    const mid = e2.source === g.center || e2.target === g.center;
    out.push(`<line x1="${a[0].toFixed(1)}" y1="${a[1].toFixed(1)}"
      x2="${b[0].toFixed(1)}" y2="${b[1].toFixed(1)}" stroke="#94a3b8"
      stroke-opacity="${(e2.npmi * (mid ? .55 : .22)).toFixed(3)}"
      stroke-width="${mid ? 1.5 : 1}"/>`);
  });
  g.nodes.forEach(n => {
    const [x, y] = pos[n.keyword];
    const r = n.center ? 26
      : Math.max(6, 7 + 11 * Math.sqrt((n.df || 1) / maxDf)) / (1 + n.hop * .18);
    const fade = n.center ? 1 : Math.max(.38, 1 - (n.hop - 1) * .3);
    out.push(`<g class="gnode${n.keyword === egoSel ? ' sel' : ''}"
      data-node="${esc(n.keyword)}">
      <title>${esc(n.keyword)} · ${n.df}건${n.center ? '' : ` · ${n.hop}홉`}</title>
      <circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${r.toFixed(1)}"
        fill="var(--ax-${n.axis || 'ai'})" fill-opacity="${fade.toFixed(2)}"/>
      <text x="${x.toFixed(1)}" y="${(y + r + 14).toFixed(1)}" text-anchor="middle"
        class="${n.center ? 'glabel-bridge' : ''}" fill="#334155"
        font-size="${n.center ? 15 : Math.max(10, 13 - n.hop)}"
        opacity="${n.center ? 1 : Math.max(.5, 1 - (n.hop - 1) * .25)}"
        >${esc(n.keyword)}</text>
    </g>`);
  });
  out.push('</svg>');
  $('#graph-svg').innerHTML = out.join('');
}

/* 클릭 = 이 키워드의 기사를 옆에 띄운다 (망은 그대로).
   더블클릭 = 그 키워드를 중심으로 다시 그린다.
   망을 유지한 채 여러 노드를 훑어보는 게 기본 동작이어야 한다 — 클릭할 때마다
   그림이 갈아엎히면 어디를 보고 있었는지 잃는다. */
function selectNode(kw) {
  egoSel = kw;
  $$('#graph-svg .gnode').forEach(g =>
    g.classList.toggle('sel', g.dataset.node === kw));
  showKeyword(kw);
}

$('#graph-svg').addEventListener('click', e => {
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

  const r = await api('/api/keyword/' + encodeURIComponent(kw), { limit: 15 });
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
async function loadTrend() {
  const t = await api('/api/trend', { top: 20 });
  $('#trend-week').textContent = t.week_label || '';
  if (!t.rows?.length) { $('#trend-body').innerHTML = '<div class="empty">데이터가 없습니다.</div>'; return; }
  const max = Math.max(...t.rows.map(r => r.score));
  $('#trend-body').innerHTML = `<div class="tblwrap"><table>
      <tr><th>키워드</th><th>이번 주</th><th>상승폭</th><th>최근 5주 추이</th></tr>` +
    t.rows.map(r => {
      const sp = (r.series || []).map(s => s.n), sm = Math.max(1, ...sp);
      return `<tr>
        <td><a href="#" data-kw="${esc(r.keyword)}"><b>${esc(r.keyword)}</b></a>
          ${r.is_new ? ' <span class="new">신규</span>' : ''}</td>
        <td class="n">${r.count}건</td>
        <td class="n">${r.score.toFixed(1)}배
          <div class="bar" style="width:${Math.round(r.score / max * 90)}px"></div></td>
        <td class="n">${sp.length ? `<span class="spark" title="${
            (r.series || []).map(s => `${s.week} ${s.n}건`).join(' / ')}">${
            sp.map((n, i) => `<i style="height:${Math.max(1, Math.round(n / sm * 22))}px"
              class="${i === sp.length - 1 ? 'now' : ''}"></i>`).join('')
          }</span> <span class="spark-n">${sp.join('·')}</span>` : '–'}</td>
      </tr>`;
    }).join('') + '</table></div>';
}

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
    $('#drawer-body').innerHTML = `
      <h2 style="font-size:18px;margin:0 30px 8px 0;line-height:1.45">${esc(d.title)}</h2>
      <div class="mut">${tags(d.axes)} ${esc(d.source)} · ${esc(d.published)}
        · <a href="${esc(d.url)}" target="_blank" rel="noopener">원문 보기</a></div>
      <p style="font-size:14px;margin-top:14px">${esc(d.summary || '')}</p>
      ${d.insight ? `<div class="item-i">💡 ${esc(d.insight)}</div>` : ''}
      <div class="sec-title">비슷한 기사</div>
      <div class="items" style="padding:0;box-shadow:none;margin:0">
        ${(d.related || []).map(itemHTML).join('') || '<div class="empty">없습니다.</div>'}</div>`;
    $('#drawer').hidden = false;
  }
  if (e.target.closest('[data-close]')) $('#drawer').hidden = true;
});
document.addEventListener('keydown', e => { if (e.key === 'Escape') $('#drawer').hidden = true; });

/* ── 시작 ── */
const LOADERS = {
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
  const tab = (location.hash || '#digest').slice(1);
  showTab(LOADERS[tab] ? tab : 'digest');
})();
