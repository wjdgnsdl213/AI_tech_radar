/* AI·빅데이터 트렌드 레이더 — SPA
 *
 * 서버는 JSON만 내고(web/api.py) 렌더는 여기서 한다. sobiz web/ 패턴과 같다.
 * 항목 선정·점수·브릿지 계산은 전부 서버(src/*.py)가 정한 것을 그대로 쓴다 —
 * 여기서 다시 고르면 메일·CSV와 화면이 갈라진다.
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

let AXES = [];                       // [{key,label}]
const label = k => (AXES.find(a => a.key === k) || {}).label || k;
const badges = ax => (ax || []).map(a =>
  `<span class="badge ${esc(a)}">${esc(label(a))}</span>`).join('');

/* ── 탭 ─────────────────────────────────────────────── */
const loaded = new Set();
function showTab(name) {
  $$('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  $$('.panel').forEach(p => p.classList.toggle('active', p.id === 'panel-' + name));
  location.hash = name;
  if (!loaded.has(name)) { loaded.add(name); (LOADERS[name] || (() => {}))(); }
}
$$('.tab-btn').forEach(b => b.onclick = () => showTab(b.dataset.tab));

/* ── 지표 ───────────────────────────────────────────── */
async function loadStats() {
  const s = await api('/api/stats');
  $('#s-total').textContent = num(s.total);
  $('#s-src').textContent = (s.by_source || []).slice(0, 3)
    .map(x => `${x.source} ${num(x.n)}`).join(' · ');
  $('#s-kept').textContent = num(s.kept);
  $('#s-keptp').textContent = s.total ? `전체의 ${(s.kept / s.total * 100).toFixed(1)}%` : '';
  $('#s-cross').textContent = num(s.crossing3);
  $('#s-week').textContent = num(s.this_week);
  $('#s-weekname').textContent = s.week || '';
  if (s.range?.[0]) $('#nav-range').textContent = `${s.range[0]} ~ ${s.range[1]}`;
}

/* ── ① 이번 주 ──────────────────────────────────────── */
let DIGEST = null, activeAxis = 'all';

function itemHTML(p) {
  const ins = p.insight ? `<div class="item-i">💡 ${esc(p.insight)}</div>` : '';
  return `<div class="item">
    <div class="item-t"><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title)}</a></div>
    <div class="item-m">${esc(p.source)} · ${esc(p.published)}
      · <a href="#" data-item="${p.id}">상세</a></div>
    ${ins}
    <div class="badges">${badges(p.axes)}
      <span class="score">교차 ${(p.cross_score ?? 0).toFixed(1)}</span></div>
  </div>`;
}

function renderDigest() {
  const d = DIGEST;
  if (!d || d.empty) { $('#digest-body').innerHTML = '<div class="empty">이 주차에 항목이 없습니다.</div>'; return; }

  // 칩: 기본은 '전체'. 교집합이 맨 위라는 원칙은 전체 보기에서 유지된다.
  const chips = [{ key: 'all', label: '전체', n: d.sections.reduce((a, s) => a + s.items.length, 0) }]
    .concat(d.sections.filter(s => s.items.length).map(s => ({ key: s.key, label: s.label, n: s.items.length })));
  $('#axis-chips').innerHTML = chips.map(c =>
    `<button class="chip ${c.key === activeAxis ? 'active' : ''}" data-axis="${esc(c.key)}">
       ${esc(c.label)}<span class="n">${c.n}</span></button>`).join('');

  const secs = d.sections.filter(s => s.items.length &&
    (activeAxis === 'all' || s.key === activeAxis));
  $('#digest-body').innerHTML = secs.length
    ? secs.map(s => `<div class="sec-title">${esc(s.label)}</div>` +
        s.items.map(itemHTML).join('')).join('')
      + (d.trending?.length ? `<div class="sec-title">📈 급상승 키워드</div>
         <div class="chip-row">${d.trending.slice(0, 12).map(t =>
           `<span class="chip">${esc(t.keyword)}<span class="n">${t.count}${t.is_new ? ' 신규' : ''}</span></span>`).join('')}</div>` : '')
    : '<div class="empty">해당 축에 항목이 없습니다.</div>';
}

async function loadDigest(week) {
  DIGEST = await api('/api/digest', { week: week || '' });
  if (DIGEST.lead) { $('#lead').textContent = DIGEST.lead; $('#lead-card').hidden = false; }
  else $('#lead-card').hidden = true;
  renderDigest();
}
$('#axis-chips').onclick = e => {
  const b = e.target.closest('[data-axis]');
  if (b) { activeAxis = b.dataset.axis; renderDigest(); }
};

/* ── ② 검색 ────────────────────────────────────────── */
let searchPage = 1;
const params = () => ({
  q: $('#f-q').value.trim(), axis: $('#f-axis').value,
  since: $('#f-since').value.trim(), until: $('#f-until').value.trim(),
  kept_only: $('#f-kept').checked ? 1 : 0,
});

async function runSearch(page = 1) {
  searchPage = page;
  const p = { ...params(), page, size: 50 };
  $('#search-body').innerHTML = '<div class="empty">검색 중…</div>';
  const r = await api('/api/search', p);
  const from = (page - 1) * r.size;
  $('#search-count').textContent = r.total
    ? `${num(r.total)}건 중 ${from + 1}~${from + r.items.length}` : '';
  $('#search-body').innerHTML = r.items.length
    ? `<div class="tblwrap"><table><tr><th>발행일</th><th>제목</th><th>축</th><th>교차</th><th>출처</th></tr>` +
      r.items.map(p => `<tr>
        <td class="n">${esc(p.published)}</td>
        <td><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title)}</a>
          <a href="#" data-item="${p.id}" class="mut"> ·상세</a>
          ${p.insight ? `<div class="item-i">💡 ${esc(p.insight)}</div>` : ''}</td>
        <td>${badges(p.axes)}</td>
        <td class="n">${(p.cross_score ?? 0).toFixed(1)}</td>
        <td class="n">${esc(p.source)}</td></tr>`).join('') + '</table></div>'
    : '<div class="empty">결과가 없습니다.</div>';

  const nav = [];
  if (page > 1) nav.push(`<button class="preset" data-page="${page - 1}">← 이전</button>`);
  if (from + r.size < r.total) nav.push(`<button class="preset" data-page="${page + 1}">다음 →</button>`);
  $('#search-pager').innerHTML = nav.join('');

  const qs = new URLSearchParams(params()).toString();
  $('#f-csv').href = '/search.csv?' + qs;
}
$('#search-form').onsubmit = e => { e.preventDefault(); runSearch(1); };
$('#search-pager').onclick = e => {
  const b = e.target.closest('[data-page]');
  if (b) runSearch(+b.dataset.page);
};

// 기간 프리셋 — S4(보고서 근거 찾기)에서 매번 날짜를 손으로 치는 게 제일 번거롭다
const PRESETS = [['최근 1개월', 30], ['최근 3개월', 90], ['최근 1년', 365], ['전체', 0]];
function initSearch() {
  $('#f-axis').innerHTML = '<option value="">전체 축</option>' +
    AXES.map(a => `<option value="${esc(a.key)}">${esc(a.label)}</option>`).join('');
  $('#presets').innerHTML = PRESETS.map(([t, d]) =>
    `<button type="button" class="preset" data-days="${d}">${t}</button>`).join('');
  $('#presets').onclick = e => {
    const b = e.target.closest('[data-days]'); if (!b) return;
    const d = +b.dataset.days;
    if (!d) { $('#f-since').value = ''; $('#f-until').value = ''; }
    else {
      const t = new Date(), s = new Date(Date.now() - d * 864e5);
      $('#f-until').value = t.toISOString().slice(0, 10);
      $('#f-since').value = s.toISOString().slice(0, 10);
    }
    runSearch(1);
  };
  runSearch(1);
}

/* ── ③ 연관어 네트워크 ─────────────────────────────── */
/* 배치는 물리 시뮬레이션이 아니라 **축 삼각형의 무게중심**이다.
 * 노드 좌표 = 축별 등장 비중으로 세 꼭짓점을 가중평균한 점.
 * 한 축에만 나오는 키워드는 그 꼭짓점으로, 여러 축에 걸친 키워드는 가운데로 모인다.
 * 즉 "가운데 있는 게 브릿지"라는 읽는 법이 좌표에 그대로 들어간다.
 * 드래그 물리감·노드 겹침 튜닝이 필요 없어 PLAN이 경고한 시간 블랙홀도 피한다. */
async function loadGraph() {
  const g = await api('/api/graph', { top: 20 });
  if (g.empty || !g.nodes.length) {
    $('#graph-svg').innerHTML = '<div class="empty">키워드 데이터가 없습니다.</div>'; return;
  }
  const W = 640, H = 520, R = 210, cx = W / 2, cy = H / 2 + 10;
  const keys = AXES.map(a => a.key);
  const anchors = {};
  keys.forEach((k, i) => {
    const t = -Math.PI / 2 + i * 2 * Math.PI / keys.length;
    anchors[k] = [cx + R * Math.cos(t), cy + R * Math.sin(t)];
  });
  const bridgeSet = new Set(g.bridges.map(b => b.keyword));

  const pos = {};
  g.nodes.forEach((n, i) => {
    let x = 0, y = 0, w = 0;
    keys.forEach(k => {
      const s = (n.axis_share || {})[k] || 0;
      x += anchors[k][0] * s; y += anchors[k][1] * s; w += s;
    });
    if (!w) { x = cx; y = cy; } else { x /= w; y /= w; }
    // 완전히 겹치는 걸 막는 결정적 흔들기(난수가 아니라 인덱스 기반이라 새로고침해도 같다)
    const a = i * 2.399963;
    pos[n.keyword] = [x + Math.cos(a) * 16, y + Math.sin(a) * 16];
  });

  const maxDf = Math.max(...g.nodes.map(n => n.df));
  const parts = [`<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">`];
  keys.forEach(k => {
    const [x, y] = anchors[k];
    parts.push(`<text x="${x}" y="${y + (y < cy ? -18 : 26)}" text-anchor="middle"
      font-size="13" font-weight="700" fill="var(--ax-${k})">${esc(label(k))}</text>`);
  });
  g.edges.slice(0, 600).forEach(e => {
    const a = pos[e.source], b = pos[e.target];
    if (!a || !b) return;
    parts.push(`<line x1="${a[0].toFixed(1)}" y1="${a[1].toFixed(1)}"
      x2="${b[0].toFixed(1)}" y2="${b[1].toFixed(1)}"
      stroke="#1e3932" stroke-opacity="${(e.npmi * .22).toFixed(3)}" stroke-width="1"/>`);
  });
  g.nodes.forEach(n => {
    const [x, y] = pos[n.keyword];
    const r = 3 + 7 * Math.sqrt(n.df / maxDf);
    const isB = bridgeSet.has(n.keyword);
    parts.push(`<g class="gnode"><title>${esc(n.keyword)} · ${n.df}건 · 연결 ${n.degree} · 브릿지 ${n.bridge}</title>
      <circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${r.toFixed(1)}"
        fill="var(--ax-${n.axis || 'ai'})" fill-opacity="${isB ? .95 : .5}"
        stroke="${isB ? '#c82014' : 'none'}" stroke-width="${isB ? 2 : 0}"/>
      ${(isB || n.df > maxDf * .35)
        ? `<text x="${x.toFixed(1)}" y="${(y - r - 4).toFixed(1)}" text-anchor="middle"
             class="${isB ? 'glabel-bridge' : ''}" fill="#1e3932">${esc(n.keyword)}</text>` : ''}
    </g>`);
  });
  parts.push('</svg>');
  $('#graph-svg').innerHTML = parts.join('');

  $('#bridge-table').innerHTML = g.bridges.length
    ? `<div class="tblwrap"><table><tr><th>키워드</th><th>문서</th><th>브릿지</th><th>잇는 축</th></tr>` +
      g.bridges.map(b => `<tr>
        <td><b>${esc(b.keyword)}</b></td>
        <td class="n">${b.df}</td>
        <td class="n">${b.bridge.toFixed(2)}
          <div class="bar" style="width:${Math.round(b.bridge * 60)}px"></div></td>
        <td>${badges(b.spans)}</td></tr>`).join('') + '</table></div>'
    : '<div class="empty">브릿지 후보가 없습니다.</div>';
}

/* ── ④ 급상승 ──────────────────────────────────────── */
async function loadTrend() {
  const t = await api('/api/trend', { top: 20 });
  if (!t.rows?.length) { $('#trend-body').innerHTML = '<div class="empty">데이터가 없습니다.</div>'; return; }
  const max = Math.max(...t.rows.map(r => r.score));
  $('#trend-body').innerHTML =
    `<div class="tblwrap"><table>
      <tr><th>키워드</th><th>이번 주</th><th>비중</th><th>직전</th><th>급상승</th><th>추이</th></tr>` +
    t.rows.map(r => {
      const spark = (r.series || []).map(s => s.n);
      const sm = Math.max(1, ...spark);
      return `<tr>
        <td><b>${esc(r.keyword)}</b>${r.is_new ? ' <span class="badge smallbiz">신규</span>' : ''}</td>
        <td class="n">${r.count}건</td>
        <td class="n">${r.share.toFixed(2)}%</td>
        <td class="n">${r.prev_share.toFixed(2)}%</td>
        <td class="n">${r.score.toFixed(1)}×
          <div class="bar" style="width:${Math.round(r.score / max * 70)}px"></div></td>
        <td class="n">${spark.length
          ? spark.map(n => `<span style="display:inline-block;width:6px;
              height:${Math.max(2, Math.round(n / sm * 18))}px;background:var(--green-accent);
              margin-right:1px;vertical-align:bottom"></span>`).join('') : '–'}</td></tr>`;
    }).join('') + '</table></div>';
}

/* ── ⑤ 회차 ────────────────────────────────────────── */
async function loadWeeks() {
  const w = await api('/api/weeks');
  const max = Math.max(1, ...w.weeks.map(x => x.n));
  $('#weeks-body').innerHTML = `<div class="tblwrap"><table>
    <tr><th>주차</th><th>통과 항목</th><th></th></tr>` +
    w.weeks.map(x => `<tr>
      <td><a href="#" data-week="${esc(x.week)}">${esc(x.week)}</a></td>
      <td class="n">${num(x.n)}건</td>
      <td><div class="bar" style="width:${Math.round(x.n / max * 220)}px"></div></td>
    </tr>`).join('') + '</table></div>';
}
$('#weeks-body').onclick = e => {
  const a = e.target.closest('[data-week]');
  if (a) { e.preventDefault(); loadDigest(a.dataset.week); showTab('digest'); }
};

/* ── 상세 서랍 ─────────────────────────────────────── */
document.body.addEventListener('click', async e => {
  const a = e.target.closest('[data-item]');
  if (a) {
    e.preventDefault();
    const d = await api('/api/item/' + a.dataset.item);
    $('#drawer-body').innerHTML = `
      <h2 style="font-size:17px;margin:0 26px 6px 0">${esc(d.title)}</h2>
      <div class="mut">${esc(d.source)} · ${esc(d.published)}
        · <a href="${esc(d.url)}" target="_blank" rel="noopener">원문 보기</a></div>
      <div class="badges" style="margin:10px 0">${badges(d.axes)}
        <span class="score">교차 ${(d.cross_score ?? 0).toFixed(1)}
        · 관련도 ${d.relevance != null ? d.relevance.toFixed(3) : '–'}</span></div>
      <p style="font-size:14px">${esc(d.summary || '')}</p>
      ${d.insight ? `<div class="item-i">💡 ${esc(d.insight)}</div>` : ''}
      <div class="sec-title">같은 축의 관련 항목</div>
      ${(d.related || []).map(itemHTML).join('') || '<div class="empty">없습니다.</div>'}`;
    $('#drawer').hidden = false;
  }
  if (e.target.closest('[data-close]')) $('#drawer').hidden = true;
});
document.addEventListener('keydown', e => { if (e.key === 'Escape') $('#drawer').hidden = true; });

/* ── 시작 ──────────────────────────────────────────── */
const LOADERS = {
  digest: () => loadDigest(), search: initSearch,
  graph: loadGraph, trend: loadTrend, weeks: loadWeeks,
};

(async () => {
  try {
    AXES = (await api('/api/meta')).axes;
    await loadStats();
  } catch (err) {
    $('#digest-body').innerHTML =
      `<div class="empty">서버에 연결하지 못했습니다.<br><code>${esc(err.message)}</code></div>`;
    return;
  }
  const tab = (location.hash || '#digest').slice(1);
  showTab(LOADERS[tab] ? tab : 'digest');
})();
