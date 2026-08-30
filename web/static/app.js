/* AI·빅데이터 트렌드 레이더 — SPA
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
function itemHTML(p) {
  return `<div class="item">
    <div class="item-t"><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title)}</a></div>
    <div class="item-m">${tags(p.axes)} ${esc(p.source)} · ${esc(p.published)}
      · <a href="#" data-item="${p.id}">자세히</a></div>
    ${p.insight ? `<div class="item-i">💡 ${esc(p.insight)}</div>` : ''}
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
        <td><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title)}</a>
          <a href="#" data-item="${p.id}" class="mut"> ·자세히</a>
          ${p.insight ? `<div class="item-i">💡 ${esc(p.insight)}</div>` : ''}</td>
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

/* ── ③ 연관어 네트워크 ──
 * 배치는 물리 시뮬레이션이 아니라 **주제 삼각형의 무게중심**이다.
 * 노드 좌표 = 주제별 등장 비중으로 세 꼭짓점을 가중평균한 점.
 * 한 주제에만 나오면 그 꼭짓점으로, 여러 주제에 걸치면 가운데로 모인다.
 * 결정적이라 새로고침해도 같은 그림이고 드래그·충돌 처리가 필요 없다. */
async function loadGraph() {
  const g = await api('/api/graph', { top: 20 });
  if (g.empty || !g.nodes?.length) {
    $('#graph-svg').innerHTML = '<div class="empty">키워드 데이터가 없습니다.</div>'; return;
  }
  const W = 900, H = 640, R = 265, cx = W / 2, cy = H / 2 + 8;
  const keys = AXES.map(a => a.key), anchors = {};
  keys.forEach((k, i) => {
    const t = -Math.PI / 2 + i * 2 * Math.PI / keys.length;
    anchors[k] = [cx + R * Math.cos(t), cy + R * Math.sin(t)];
  });
  const bset = new Set((g.bridges || []).map(b => b.keyword));
  const pos = {};
  g.nodes.forEach((n, i) => {
    let x = 0, y = 0, w = 0;
    keys.forEach(k => {
      const s = (n.axis_share || {})[k] || 0;
      x += anchors[k][0] * s; y += anchors[k][1] * s; w += s;
    });
    if (!w) { x = cx; y = cy; } else { x /= w; y /= w; }
    const a = i * 2.399963;   // 황금각 — 난수가 아니라 재현되는 흔들기
    pos[n.keyword] = [x + Math.cos(a) * 20, y + Math.sin(a) * 20];
  });

  const maxDf = Math.max(...g.nodes.map(n => n.df));
  const out = [`<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">`];
  keys.forEach(k => {
    const [x, y] = anchors[k];
    out.push(`<text x="${x}" y="${y + (y < cy ? -22 : 30)}" text-anchor="middle"
      font-size="15" font-weight="700" fill="var(--ax-${k})">${esc(label(k))}</text>`);
  });
  (g.edges || []).slice(0, 700).forEach(e => {
    const a = pos[e.source], b = pos[e.target]; if (!a || !b) return;
    out.push(`<line x1="${a[0].toFixed(1)}" y1="${a[1].toFixed(1)}" x2="${b[0].toFixed(1)}"
      y2="${b[1].toFixed(1)}" stroke="#1e3932" stroke-opacity="${(e.npmi * .2).toFixed(3)}"/>`);
  });
  g.nodes.forEach(n => {
    const [x, y] = pos[n.keyword], r = 4 + 8 * Math.sqrt(n.df / maxDf), isB = bset.has(n.keyword);
    out.push(`<g class="gnode" data-kw="${esc(n.keyword)}">
      <title>${esc(n.keyword)} · ${n.df}건 (클릭하면 기사 목록)</title>
      <circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${r.toFixed(1)}"
        fill="var(--ax-${n.axis || 'ai'})" fill-opacity="${isB ? .95 : .45}"
        stroke="${isB ? '#c82014' : 'none'}" stroke-width="${isB ? 2 : 0}"/>
      ${(isB || n.df > maxDf * .3)
        ? `<text x="${x.toFixed(1)}" y="${(y - r - 5).toFixed(1)}" text-anchor="middle"
             class="${isB ? 'glabel-bridge' : ''}" fill="#1e3932">${esc(n.keyword)}</text>` : ''}
    </g>`);
  });
  out.push('</svg>');
  $('#graph-svg').innerHTML = out.join('');

  $('#bridge-table').innerHTML = (g.bridges || []).length
    ? g.bridges.map(b => `<div class="mini" data-kw="${esc(b.keyword)}">
        <span class="k">${esc(b.keyword)}</span>
        <span class="v">${b.spans.map(s => esc(label(s))).join(' · ')} · ${b.df}건</span>
      </div>`).join('')
    : '<div class="empty">—</div>';
}

/* 노드·키워드 클릭 → 그 키워드가 나온 기사.
   그래프에서 발견한 걸 기사로 확인할 수 없으면 과제 후보로 못 쓴다. */
async function showKeyword(kw) {
  $('#kw-card').hidden = false;
  $('#kw-title').textContent = kw;
  $('#kw-count').textContent = '';
  $('#kw-body').innerHTML = '<div class="empty">불러오는 중…</div>';
  const r = await api('/api/keyword/' + encodeURIComponent(kw), { limit: 15 });
  $('#kw-count').textContent = `${num(r.total)}건`;
  $('#kw-body').innerHTML = r.items.length
    ? `<div class="items" style="padding:0;box-shadow:none;margin:0">
        ${r.items.map(itemHTML).join('')}</div>`
    : '<div class="empty">기사가 없습니다.</div>';
  $('#kw-card').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

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
        <td class="n">${sp.length ? sp.map(n =>
          `<span style="display:inline-block;width:8px;height:${Math.max(2, Math.round(n / sm * 20))}px;
            background:var(--green-accent);margin-right:2px;vertical-align:bottom"></span>`).join('') : '–'}</td>
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
    return showKeyword(kw.dataset.kw);
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
