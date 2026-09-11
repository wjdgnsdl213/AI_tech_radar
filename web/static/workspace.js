/* 리뷰와 개인 리서치 작업실. 기존 app.js의 탐색/기사 상세를 함께 사용한다. */
window.Workspace = (() => {
  'use strict';
  const Q = s => document.querySelector(s);
  const E = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const U = ResearchStore.safeURL;
  const store = ResearchStore.createStore({getItem:k=>localStorage.getItem(k),setItem:(k,v)=>localStorage.setItem(k,v)});
  const articles = new Map();
  let currentReview = null, reviewKind = 'weekly', reviewRequest = 0, issueRequest = 0;
  let candidates = [], selected = new Set(), visibleScraps = [], statusTimer;
  const STATUS = {in_progress:'진행 중',closed:'기간 종료',final:'확정본',upcoming:'시작 전'};
  const fmt = s => s ? String(s).replace('T',' ').slice(0,16) : '—';
  // 사내 HTTP 주소에서도 작성할 수 있도록 secure-context 전용 randomUUID에 의존하지 않는다.
  const newKey = () => (globalThis.crypto?.randomUUID?.() || Date.now().toString(36)+'-'+Math.random().toString(36).slice(2));
  function notice(message, error = false) {
    clearTimeout(statusTimer);
    const n = Q('#work-status'); n.textContent = message; n.hidden = false;
    n.classList.toggle('error',error);
    statusTimer = setTimeout(()=>{n.hidden=true;},error ? 10000 : 4000);
  }
  function state() {
    try {return store.read();} catch (e) {notice('개인 저장소를 읽지 못했습니다. '+e.message,true);return {version:1,scraps:[],issues:[],tasks:[]};}
  }
  function write(fn, message) {
    try {fn(); if(message) notice(message); refreshSaveButtons(); return true;}
    catch(e) {notice('저장하지 못했습니다. '+e.message,true);return false;}
  }
  function download(text,name,type) {
    const url=URL.createObjectURL(new Blob([text],{type})), a=document.createElement('a');
    a.href=url;a.download=name;document.body.append(a);a.click();a.remove();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  function registerArticle(p) {
    if(p?.id) articles.set(String(p.id),{...articles.get(String(p.id)),...p});
  }
  function articleButton(p) {
    registerArticle(p);
    return `<button type="button" class="preset save-article" data-save-article="${E(p.id)}" aria-label="${E(p.title)} 보관함에 저장">스크랩</button>`;
  }
  function refreshSaveButtons() {
    let saved;
    try {saved=new Set(store.read().scraps.map(s=>s.key));} catch {return;}
    document.querySelectorAll('[data-save-article]').forEach(b=>{
      const on=saved.has('article:'+b.dataset.saveArticle); b.textContent=on?'저장됨':'스크랩';
      b.classList.toggle('saved',on);
    });
  }
  async function saveArticle(id) {
    let p=articles.get(String(id));
    if(!p || (!p.summary && !p.insight)) p=await api('/api/item/'+encodeURIComponent(id));
    if(!p?.title) throw new Error('기사를 찾을 수 없습니다.');
    registerArticle(p);
    write(()=>store.saveScrap({key:'article:'+id,kind:'article',title:p.title,text:p.insight||p.summary||'',
      url:p.url,source:p.source,published:p.published}),'보관함에 저장했습니다. 메모와 주제는 작업실에서 추가할 수 있습니다.');
  }
  function sourceLinks(sources) {
    return (sources||[]).map(s=>U(s.url)?`<li><a href="${E(U(s.url))}" target="_blank" rel="noopener noreferrer">${E(s.title)}</a>${articleButton(s)}</li>`:'').join('');
  }
  function reviewText(v) {
    const r=v.editorial;
    const parts=[`${v.label} · ${STATUS[v.status]}\n집계 기간: ${v.start}~${v.through}\n해설 기준일: ${r?.as_of||'확인되지 않음'}\n데이터 기준: ${fmt(v.data_as_of)}`];
    if(r) parts.push(r.summary,...(r.sections||[]).map(s=>s.title+'\n'+s.body));
    parts.push(`통과 기사 ${v.kept}건 · 직전 ${v.previous.same_elapsed?'동일 경과기간':'기간'} ${v.previous.kept}건`);
    return parts.join('\n\n');
  }
  async function loadReviews(seg='') {
    const request=++reviewRequest;
    const [kind,period='']=seg.split(':');
    reviewKind=['weekly','monthly'].includes(kind)?kind:reviewKind;
    const selectedKind=reviewKind;
    Q('#review-content').innerHTML='<div class="card work-empty">리뷰와 근거를 불러오는 중…</div>';
    document.querySelectorAll('[data-review-kind]').forEach(b=>b.classList.toggle('active',b.dataset.reviewKind===reviewKind));
    try {
      const [v,ps]=await Promise.all([api('/api/reviews',{kind:reviewKind,period}),api('/api/reviews/periods',{kind:reviewKind})]);
      if(request!==reviewRequest) return;
      currentReview=v;
      const options=ps.periods.some(p=>p.period===v.period)?ps.periods:[{period:v.period,label:v.label},...ps.periods];
      Q('#review-period').innerHTML=options.map(p=>`<option value="${E(p.period)}" ${p.period===v.period?'selected':''}>${E(p.label)}</option>`).join('');
      const r=v.editorial, sources=r?.sources||[];
      const citedIds=new Set((r?.sections||[]).flatMap(s=>s.source_ids||[]));
      const remainingSources=sources.filter(s=>!citedIds.has(s.id));
      const remainingArticles=(v.articles||[]).filter(a=>!citedIds.has(a.id));
      const taskPreview=(r?.tasks||[]).slice(0,3);
      const shownEvidence=new Set();
      const evidenceBySection=(r?.sections||[]).map(s=>(s.source_ids||[]).flatMap(id=>{
        if(shownEvidence.has(id))return [];
        const source=sources.find(x=>x.id===id);
        if(!source)return [];
        shownEvidence.add(id);return [source];
      }));
      const reportLinks=Q('#briefing-report-links');
      reportLinks.hidden=selectedKind!=='monthly';
      if(selectedKind==='monthly'){
        Q('#briefing-report-html').href='/report?month='+encodeURIComponent(v.period);
        Q('#briefing-report-md').href='/report.md?month='+encodeURIComponent(v.period);
      }
      const max=Math.max(1,...v.series.map(s=>s.count));
      Q('#review-content').innerHTML=`
        <div class="card"><div class="review-head"><div><h2>${E(v.label)} ${selectedKind==='monthly'?'월간':'주간'} 리뷰 <span class="review-status">${STATUS[v.status]}</span></h2>
        <p class="review-meta">집계 ${E(v.start)}~${E(v.through)} · 한국 시간<br>최신 기사 ${E(fmt(v.data_as_of))}<br>${r?`해설 기준 ${E(r.as_of||'기존 리뷰에 기준일 기록 없음')} · 작성 ${E(fmt(r.generated_at))}`:'아직 작성된 해설이 없습니다.'}</p></div>
        <button class="btn-ghost" data-save-review>리뷰 저장</button></div>
        ${r?`<div class="review-prose">${ReviewFormat.render(r.summary)}</div>${r.legacy?UIHelp.render('기존 리뷰의 해설과 현재 재계산한 집계는 기준 시점이 다를 수 있습니다.'):''}
        ${(r.sections||[]).map((s,i)=>`<section class="review-section"><h3>${E(s.title)}</h3>${ReviewFormat.render(s.body)}${evidenceBySection[i].length?`<div class="review-evidence"><h4>근거 기사</h4><ol class="review-sources">${sourceLinks(evidenceBySection[i])}</ol></div>`:''}</section>`).join('')}`:
        '<div class="work-empty">이 기간의 해설은 준비 중입니다.</div>'}
        ${remainingSources.length?`<details><summary>해설에서 인용하지 않은 출처 ${remainingSources.length}건</summary><ol class="review-sources">${sourceLinks(remainingSources)}</ol></details>`:''}</div>
        <div class="card"><details><summary>${v.previous.same_elapsed?'직전 동일 경과기간과 비교':'직전 기간과 비교'} · 상세 수치 보기</summary>
        <p class="mut">비교 대상 ${E(v.previous.start)}부터 ${E(fmt(v.previous.until_exclusive))} 직전까지</p>${UIHelp.render('기사 수는 수집 범위에도 영향을 받습니다. 한 기사는 여러 주제에 포함될 수 있으며, 보도 비중의 변화가 실제 시장 성장률을 뜻하지는 않습니다.')}
        <div class="review-metrics"><div><b>${num(v.kept)}</b><span>현재 기간 기사</span></div><div><b>${num(v.previous.kept)}</b><span>비교 기간 기사</span></div><div><b>${v.previous.kept?((v.kept-v.previous.kept)>0?'+':'')+num(v.kept-v.previous.kept):'—'}</b><span>기사 수 차이</span></div></div>
        <table class="review-compare"><thead><tr><th>주제</th><th>기사 수</th><th>현재 비중</th><th>이전 비중</th><th>변화</th></tr></thead><tbody>${v.axes.map(a=>`<tr><td>${E(label(a.axis))}</td><td>${num(a.n)}</td><td>${a.share}%</td><td>${a.previous_share==null?'—':a.previous_share+'%'}</td><td>${a.previous_share==null||!v.kept?'—':((a.share-a.previous_share)>0?'+':'')+(a.share-a.previous_share).toFixed(1)+'%p'}</td></tr>`).join('')}</tbody></table>

        ${v.series.length?`<details><summary>일별 기사 수 보기</summary><div class="review-bars" aria-hidden="true">${v.series.map(s=>`<div title="${E(s.date)} ${s.count}건"><i style="height:${Math.max(2,s.count/max*100)}%"></i></div>`).join('')}</div><p class="mut">${v.series.map(s=>`${E(s.date.slice(5))} ${num(s.count)}건`).join(' · ')}</p></details>`:''}</details></div>
        ${taskPreview.length?`<div class="card"><h2>검토할 과제 후보 ${taskPreview.length}개</h2>${taskPreview.map(t=>`<div class="candidate"><h3>${E(t.title)}</h3><p>${E(t.ask)}</p><button class="preset" data-review-task="${E(t.title)}">작업실에 추가</button></div>`).join('')}</div>`:''}
        <div class="card"><h2>인용 외 주요 기사</h2>${remainingArticles.slice(0,10).map(itemHTML).join('')||'<div class="work-empty">추가로 표시할 기사가 없습니다.</div>'}<p><button class="preset" data-explore-period data-since="${E(v.start)}" data-until="${E(v.through)}">관련 기사 더 보기</button></p></div>`;
      refreshSaveButtons();
    } catch(e) {
      if(request!==reviewRequest)return;
      Q('#review-content').innerHTML=`<div class="card work-empty">리뷰를 불러오지 못했습니다.<br>${E(e.message)}<br><button class="preset" data-review-retry>다시 불러오기</button></div>`;
    }
  }
  function following() {
    const list=state().issues;
    Q('#issue-following').innerHTML=list.length?list.map(i=>`<span><button data-follow-query="${E(i.query)}">${E(i.query)}</button><button data-unfollow="${E(i.key)}" aria-label="${E(i.query)} 추적 해제">×</button></span>`).join(''):'<p class="mut">등록한 관심 주제가 없습니다.</p>';
  }
  function followTopic(query) {
    if(!query){notice('등록할 주제를 먼저 입력해 주세요.',true);return false;}
    const ok=write(()=>store.followIssue(query),'관심 주제로 등록했습니다.');
    if(ok)following();
    return ok;
  }
  async function loadIssues(seg='') {
    following();
    if(seg) {try{Q('#issue-query').value=decodeURIComponent(seg);}catch{Q('#issue-query').value=seg;}}
    if(Q('#issue-query').value.trim()) await searchIssue(1);
  }
  async function searchIssue(page=1) {
    const query=Q('#issue-query').value.trim(), days=Number(Q('#issue-days').value), request=++issueRequest;
    if(!query) return;
    Q('#issue-results').innerHTML='<div class="card work-empty">관련 흐름을 불러오는 중…</div>';
    try {
      const d=await api('/api/issues',{q:query,days,page}); if(request!==issueRequest)return;
      let day='';
      Q('#issue-results').innerHTML=`<div class="card"><div class="panel-head"><h2>${E(d.query)}</h2><span class="mut">최근 ${d.days}일 · ${num(d.total)}건</span></div><p class="mut">발행일 오름차순 · ${E(d.through)}까지 · ${d.sources.map(s=>`${E(s.source)} ${num(s.count)}건`).join(' / ')}</p>
        ${d.articles.map(p=>{const heading=p.published!==day?`<h3 class="issue-day">${E(p.published)}</h3>`:'';day=p.published;return heading+`<div class="issue-item">${itemHTML(p)}<p>${E(p.summary)}</p></div>`;}).join('')||'<div class="work-empty">이 기간에 일치하는 기사가 없습니다. 검색어 또는 기간을 바꿔보세요.</div>'}
        <div class="work-toolbar"><button class="preset" data-issue-page="${page-1}" ${page===1?'disabled':''}>이전</button><span>${page} / ${Math.max(1,Math.ceil(d.total/d.size))}</span><button class="preset" data-issue-page="${page+1}" ${page*d.size>=d.total?'disabled':''}>다음</button></div></div>`;
      refreshSaveButtons();
    } catch(e) {if(request===issueRequest)Q('#issue-results').innerHTML=`<div class="card work-empty">조회에 실패했습니다. ${E(e.message)}<br><button class="preset" data-issue-page="1">다시 시도</button></div>`;}
  }
  function renderScraps() {
    const d=state(), query=Q('#scrap-query').value.trim().toLowerCase(), topic=Q('#scrap-topic').value;
    const topics=[...new Set(d.scraps.map(s=>s.topic).filter(Boolean))].sort();
    Q('#scrap-topic').innerHTML='<option value="">모든 주제</option>'+topics.map(t=>`<option ${t===topic?'selected':''}>${E(t)}</option>`).join('');
    const activeTopic=Q('#scrap-topic').value;
    visibleScraps=d.scraps.filter(s=>(!activeTopic||s.topic===activeTopic)&&(!query||[s.title,s.text,s.note,s.topic].join(' ').toLowerCase().includes(query)));
    selected=new Set([...selected].filter(k=>d.scraps.some(s=>s.key===k)));
    Q('#scrap-list').innerHTML=visibleScraps.length?visibleScraps.map(s=>`<article class="card scrap-card"><input type="checkbox" data-scrap-select="${E(s.key)}" ${selected.has(s.key)?'checked':''} aria-label="${E(s.title)} 내보내기 선택"><div><h3>${E(s.title)}</h3><span class="mut">${E(s.topic||'미분류')} · ${E(s.published||s.savedAt.slice(0,10))} · ${{article:'기사',review:'리뷰',note:'메모'}[s.kind]}</span><p>${E(s.text.slice(0,240))}${s.text.length>240?'…':''}</p>${s.note?`<p class="scrap-note">${E(s.note)}</p>`:''}<div class="work-toolbar"><button class="preset" data-edit-scrap="${E(s.key)}">읽기·메모 편집</button>${s.url?`<a class="preset" href="${E(s.url)}" target="_blank" rel="noopener noreferrer">원문 ↗</a>`:''}<button class="preset" data-remove-scrap="${E(s.key)}">보관함에서 제거</button></div></div></article>`).join(''):'<div class="card work-empty">저장한 자료가 없습니다.</div>';
    updateSelected();
  }
  function updateSelected() {
    Q('#scrap-selected').textContent=`총 ${selected.size}건 선택`;
    Q('#scrap-select-all').checked=visibleScraps.length>0&&visibleScraps.every(s=>selected.has(s.key));
    Q('#scrap-select-all').indeterminate=visibleScraps.some(s=>selected.has(s.key))&&!Q('#scrap-select-all').checked;
  }
  function dialog(title,fields,save) {
    const dlg=Q('#work-dialog');
    Q('#work-dialog-body').innerHTML=`<form id="work-editor"><h2 id="work-dialog-title">${E(title)}</h2>${fields}<p data-dialog-error role="alert"></p><div class="work-toolbar"><button type="button" class="btn-ghost" data-dialog-close>취소</button><button type="submit">저장</button></div></form>`;
    Q('#work-editor').onsubmit=e=>{
      e.preventDefault();
      try {save(Object.fromEntries(new FormData(e.target)));dlg.close();notice('저장했습니다.');refreshSaveButtons();}
      catch(err){Q('[data-dialog-error]').textContent='저장하지 못했습니다. '+err.message;}
    };
    if(!dlg.open)dlg.showModal();
  }
  const field=(name,title,value='',textarea=false)=>`<label>${E(title)}${textarea?`<textarea name="${name}" maxlength="50000">${E(value)}</textarea>`:`<input name="${name}" value="${E(value)}" maxlength="${name==='title'?1000:100}" ${name==='title'?'required':''}>`}</label>`;
  function editScrap(key) {
    const s=state().scraps.find(s=>s.key===key);
    if(!s)return;
    dialog('자료 읽기·메모 편집',field('title','제목',s.title)+field('topic','주제',s.topic)+
      `<details><summary>저장한 내용 전체 보기</summary><p class="review-prose">${E(s.text)}</p><ol class="review-sources">${sourceLinks(s.sources)}</ol></details>`+
      field('note','내 메모',s.note,true), values=>{store.editScrap(key,values);renderScraps();});
  }
  function renderBoard() {
    const tasks=state().tasks;
    Q('#task-board').innerHTML=Object.entries(ResearchStore.STATES).map(([status,title])=>`<section class="task-column"><h3>${title} · ${tasks.filter(t=>t.status===status).length}</h3>${tasks.filter(t=>t.status===status).map(t=>`<article class="task-card"><h4>${E(t.title)}</h4><span class="mut">${E(t.period||'직접 추가')} · ${E(t.owner||'담당자 미지정')}</span><p>${E(t.ask||t.mean||t.fact)}</p>${t.note?`<p class="scrap-note">${E(t.note)}</p>`:''}<button class="preset" data-edit-task="${E(t.key)}">검토·이력</button></article>`).join('')||'<p class="mut">아직 과제가 없습니다.</p>'}</section>`).join('');
  }
  function renderCandidates() {
    const keys=new Set(state().tasks.map(t=>t.key));
    Q('#task-candidates').innerHTML=candidates.length?candidates.map((t,i)=>`<article class="candidate"><span class="mut">${E(t.period)}</span><h3>${E(t.title)}</h3><p><b>관찰</b> ${E(t.fact)}<br><b>업무 관련성</b> ${E(t.mean)}<br><b>확인할 점</b> ${E(t.ask)}</p><ol class="review-sources">${sourceLinks(t.sources)}</ol><button class="preset" data-add-candidate="${i}" ${keys.has(t.key)?'disabled':''}>${keys.has(t.key)?'보드에 추가됨':'검토 보드에 추가'}</button></article>`).join(''):'<div class="work-empty">작성된 과제 후보가 없습니다.</div>';
  }
  async function loadWorkspace(seg='scraps') {
    const tasks=seg==='tasks';
    Q('#work-scraps').hidden=tasks;Q('#work-tasks').hidden=!tasks;
    document.querySelectorAll('[data-work-tab]').forEach(b=>b.classList.toggle('active',b.dataset.workTab===(tasks?'tasks':'scraps')));
    if(!tasks){renderScraps();return;}
    renderBoard();Q('#task-candidates').innerHTML='<div class="work-empty">과제 후보를 불러오는 중…</div>';
    try {candidates=(await api('/api/task-candidates')).tasks;renderCandidates();}
    catch(e){Q('#task-candidates').innerHTML=`<p>후보를 불러오지 못했습니다. ${E(e.message)}</p><button class="preset" data-work-tab="tasks">다시 불러오기</button>`;}
  }
  function editTask(key) {
    const t=state().tasks.find(t=>t.key===key);if(!t)return;
    dialog('과제 검토',`<h3>${E(t.title)}</h3><p class="review-prose">관찰: ${E(t.fact)}\n업무 관련성: ${E(t.mean)}\n확인할 점: ${E(t.ask)}</p><ol class="review-sources">${sourceLinks(t.sources)}</ol><label>검토 상태<select name="status">${Object.entries(ResearchStore.STATES).map(([k,v])=>`<option value="${k}" ${k===t.status?'selected':''}>${v}</option>`).join('')}</select></label>`+
      field('owner','담당자',t.owner)+field('note','검토 의견',t.note,true)+`<details><summary>검토 이력 ${t.history.length}건</summary><ol>${[...t.history].reverse().map(h=>`<li>${E(fmt(h.at))} · ${E(ResearchStore.STATES[h.status])} · ${E(h.owner||'미지정')}<br>${E(h.note)}</li>`).join('')}</ol></details>`,
      values=>{store.editTask(key,values);renderBoard();});
  }
  document.addEventListener('click', async e=>{
    const b=e.target.closest('button,a'); if(!b)return;
    try {
      if(b.matches('[data-save-article]')){e.preventDefault();await saveArticle(b.dataset.saveArticle);}
      if(b.matches('[data-review-kind]'))showTab('briefing',b.dataset.reviewKind);
      if(b.matches('[data-review-open]'))showTab('briefing',b.dataset.reviewOpen);
      if(b.matches('[data-review-current]'))showTab('briefing',reviewKind);
      if(b.matches('[data-review-retry]')){const x=routeOf();loadReviews(x.mode+':'+(x.period||''));}
      if(b.matches('[data-save-review]')&&currentReview){const v=currentReview;write(()=>store.saveScrap({key:'review:'+v.period,kind:'review',title:v.label+' 리뷰',text:reviewText(v),published:v.editorial?.as_of||v.through,source:'SAB Trend',sources:v.editorial?.sources||[]}),'리뷰를 보관함에 저장했습니다.');}
      if(b.matches('[data-review-task]')){const r=currentReview.editorial,t=r.tasks.find(t=>t.title===b.dataset.reviewTask);if(t)write(()=>store.addTask({...t,key:r.period+':'+t.title,period:r.period,sources:r.sources.filter(s=>t.source_ids.includes(s.id))}),'과제 검토 보드에 추가했습니다.');}
      if(b.matches('[data-issue-follow]'))followTopic(Q('#issue-query').value);
      if(b.matches('[data-follow-query]')){Q('#f-q').value=b.dataset.followQuery;showTab('explore','timeline');}
      if(b.matches('[data-explore-period]')){Q('#f-since').value=b.dataset.since;Q('#f-until').value=b.dataset.until;Q('#f-q').value='';Q('#f-axis').value='';showTab('explore','articles');}
      if(b.matches('[data-unfollow]')){if(write(()=>store.unfollowIssue(b.dataset.unfollow),'추적을 해제했습니다.'))following();}
      if(b.matches('[data-issue-page]')&&!b.disabled)await searchIssue(Number(b.dataset.issuePage));
      if(b.matches('[data-work-tab]'))showTab('workspace',b.dataset.workTab);
      if(b.matches('[data-edit-scrap]'))editScrap(b.dataset.editScrap);
      if(b.matches('[data-remove-scrap]')){
        const s=state().scraps.find(s=>s.key===b.dataset.removeScrap);
        if(s&&confirm(`‘${s.title}’과 메모를 보관함에서 제거할까요? 백업한 파일이 있으면 복원할 수 있습니다.`)){
          if(write(()=>store.removeScrap(s.key),'보관함에서 제거했습니다.'))renderScraps();
        }
      }
      if(b.matches('[data-new-note]'))dialog('메모 작성',field('title','제목')+field('topic','주제')+field('note','메모','',true),v=>{store.saveScrap({...v,key:'note:'+newKey(),kind:'note',text:''});renderScraps();});
      if(b.matches('[data-dialog-close]'))Q('#work-dialog').close();
      if(b.matches('[data-work-backup]')){download(store.backup(),'sab-research-backup-'+new Date().toISOString().slice(0,10)+'.json','application/json');notice('전체 백업을 내려받았습니다.');}
      if(b.matches('[data-bundle]')){const out=ResearchStore.exportBundle(store.read(),[...selected],Q('#bundle-title').value||'검토 자료');const kind=b.dataset.bundle;download(out[kind],'sab-review-bundle.'+(kind==='html'?'html':'md'),kind==='html'?'text/html;charset=utf-8':'text/markdown;charset=utf-8');notice('선택한 자료를 내려받았습니다.');}
      if(b.matches('[data-bundle-preview]')){
        const out=ResearchStore.exportBundle(store.read(),[...selected],Q('#bundle-title').value||'검토 자료');
        Q('#work-dialog-body').innerHTML=`<h2 id="work-dialog-title">보고자료 미리보기</h2><iframe class="bundle-preview" title="선택 자료 보고서" sandbox srcdoc="${E(out.html)}"></iframe><div class="work-toolbar"><button type="button" class="preset" data-dialog-close>닫기</button></div>`;
        Q('#work-dialog').showModal();
      }
      if(b.matches('[data-add-candidate]')){if(write(()=>store.addTask(candidates[Number(b.dataset.addCandidate)]),'검토 보드에 추가했습니다.')){renderBoard();renderCandidates();}}
      if(b.matches('[data-edit-task]'))editTask(b.dataset.editTask);
      if(b.matches('[data-new-task]'))dialog('과제 직접 추가',field('title','과제명')+field('fact','관찰한 사실','',true)+field('mean','업무 관련성','',true)+field('ask','확인할 점','',true),v=>{store.addTask({...v,key:'task:'+newKey()});renderBoard();});
    }catch(err){notice(err.message,true);}
  });
  document.addEventListener('submit',e=>{if(e.target.id==='issue-form'){e.preventDefault();Q('#f-q').value=Q('#issue-query').value.trim();showTab('explore','timeline');}});
  document.addEventListener('input',e=>{if(e.target.id==='scrap-query')renderScraps();});
  document.addEventListener('change',async e=>{
    const t=e.target;
    if(t.id==='review-period')showTab('briefing',reviewKind+':'+t.value);
    if(t.id==='scrap-topic')renderScraps();
    if(t.matches('[data-scrap-select]')){t.checked?selected.add(t.dataset.scrapSelect):selected.delete(t.dataset.scrapSelect);updateSelected();}
    if(t.id==='scrap-select-all'){visibleScraps.forEach(s=>t.checked?selected.add(s.key):selected.delete(s.key));renderScraps();}
    if(t.id==='work-restore'&&t.files[0]){
      try {if(t.files[0].size>10000000)throw new Error('백업 파일은 10MB 이하여야 합니다.');const text=await t.files[0].text();store.restore(text);notice('기존 기록을 보존하고 백업의 새 자료를 합쳤습니다.');renderScraps();renderBoard();following();refreshSaveButtons();}
      catch(err){notice('복원하지 못했습니다. '+err.message,true);}finally{t.value='';}
    }
  });
  window.addEventListener('storage',e=>{if(e.key===ResearchStore.KEY){refreshSaveButtons();if(!Q('#work-dialog').open){renderScraps();renderBoard();following();}}});
  return {loadReviews,loadIssues,loadWorkspace,articleButton,registerArticle,refreshSaveButtons,notice,renderFollowing:following,followTopic};
})();
