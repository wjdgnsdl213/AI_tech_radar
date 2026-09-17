/* 리뷰와 개인 리서치 작업실. 기존 app.js의 탐색/기사 상세를 함께 사용한다. */
window.Workspace = (() => {
  'use strict';
  const Q = s => document.querySelector(s);
  const E = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const U = ResearchStore.safeURL;
  const store = ResearchStore.createStore({getItem:k=>localStorage.getItem(k),setItem:(k,v)=>localStorage.setItem(k,v)});
  let currentReview = null, reviewKind = 'weekly', reviewRequest = 0;
  let candidates = [], statusTimer, taskRequest=0, taskPeriod='';
  const STATUS = {in_progress:'진행 중',closed:'기간 종료',final:'확정본',upcoming:'시작 전'};
  const fmt = s => s ? String(s).replace('T',' ').slice(0,16) : '—';
  function periodLabel(period) {
    const week=/^(\d{4})-W(\d{2})$/.exec(period || '');
    if(week){
      const year=Number(week[1]), start=new Date(Date.UTC(year,0,4));
      start.setUTCDate(start.getUTCDate()-(start.getUTCDay()||7)+1+(Number(week[2])-1)*7);
      const end=new Date(start);end.setUTCDate(end.getUTCDate()+6);
      const date=d=>`${d.getUTCMonth()+1}/${d.getUTCDate()}`;
      return `${start.getUTCFullYear()}년 ${date(start)}–${start.getUTCFullYear()!==end.getUTCFullYear()?end.getUTCFullYear()+'년 ':''}${date(end)}`;
    }
    const month=/^(\d{4})-(\d{2})$/.exec(period || '');
    return month?`${month[1]}년 ${Number(month[2])}월`:period || '';
  }
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
    try {fn(); if(message) notice(message);  return true;}
    catch(e) {notice('저장하지 못했습니다. '+e.message,true);return false;}
  }
  function sourceLinks(sources) {
    return (sources||[]).map(s=>U(s.url)?`<li><a href="${E(U(s.url))}" target="_blank" rel="noopener noreferrer">${E(s.title)}</a></li>`:'').join('');
  }
  function taskDetails(t, sources=[]) {
    const fields=[['fact','관찰한 사실'],['mean','추천 이유'],['team_fit','팀 업무와의 연결'],
      ['objective','목표·검토 범위'],['duration','예상 검토 기간'],['approach','단계별 진행 방법'],
      ['deliverables','예상 산출물'],['success_criteria','검토·성과 기준'],['cautions','유의 사항'],['ask','먼저 결정할 질문']];
    return `<div class="task-detail">${fields.filter(([key])=>t[key]).map(([key,label])=>
      `<section class="task-detail-section"><h4>${label}</h4><div>${ReviewFormat.render(t[key])}</div></section>`).join('')}
      ${!t.approach?'<p class="mut">이전 후보에는 실행 계획이 작성되어 있지 않습니다. 검토 의견에 진행 방법을 추가할 수 있습니다.</p>':''}
      ${sources.length?`<div class="review-evidence"><h4>추천 근거</h4><ol class="review-sources">${sourceLinks(sources)}</ol></div>`:''}</div>`;
  }
  function openTaskPlan(task, sources=[]) {
    if (!task) return;
    const dlg=Q('#task-plan-dialog');
    Q('#task-plan-title').textContent=task.title;
    const field=(key,label)=>task[key]?`<section class="plan-field"><h4>${label}</h4>${ReviewFormat.render(task[key])}</section>`:'';
    const background=field('fact','관찰한 사실')+field('mean','추천 이유')+field('team_fit','팀 업무와의 연결');
    const outcomes=field('deliverables','예상 산출물')+field('success_criteria','검토·성과 기준');
    Q('#task-plan-body').innerHTML=`
      ${task.objective||task.duration?`<div class="plan-overview">${field('objective','목표·검토 범위')}${field('duration','예상 기간')}</div>`:''}
      ${background?`<section class="plan-section"><h3>추천 배경</h3><div class="plan-background">${background}</div></section>`:''}
      <section class="plan-section plan-approach"><h3>진행 방법</h3>${task.approach?ReviewFormat.render(task.approach):'<p class="mut">아직 작성된 진행 계획이 없습니다.</p>'}</section>
      ${outcomes?`<section class="plan-section"><h3>산출물과 검토 기준</h3><div class="plan-outcomes">${outcomes}</div></section>`:''}
      ${task.cautions||task.ask?`<div class="plan-checks">${field('cautions','유의 사항')}${field('ask','먼저 결정할 질문')}</div>`:''}
      ${sources.length?`<details class="plan-sources"><summary>추천 근거 <span>${sources.length}</span></summary><ol class="review-sources">${sourceLinks(sources)}</ol></details>`:''}`;
    if(!dlg.open) dlg.showModal();
    Q('#task-plan-body').scrollTop=0;
    document.body.classList.add('task-plan-open');
    Q('#task-plan-title').focus();
    
  }
  async function loadReviews(seg='') {
    const request=++reviewRequest;
    const [kind,period='']=seg.split(':');
    reviewKind=['weekly','monthly'].includes(kind)?kind:reviewKind;
    const selectedKind=reviewKind;
    Q('#review-content').innerHTML='<div class="card work-empty">리뷰와 근거를 불러오는 중…</div>';
    document.querySelectorAll('[data-review-kind]').forEach(b=>{
      const active=b.dataset.reviewKind===reviewKind;
      b.classList.toggle('active',active);
      b.setAttribute('aria-pressed',String(active));
    });
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
      const taskPreview=r?.tasks||[];
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
        <section class="card briefing-intro"><div class="review-head"><div><h2>${E(v.label)} ${selectedKind==='monthly'?'월간':'주간'} 리뷰 <span class="review-status">${STATUS[v.status]}</span></h2>
        <p class="review-meta">${E(v.start)} ~ ${E(v.through)} · 한국 시간</p></div>
        </div>
        ${r?`<div class="review-prose briefing-summary">${ReviewFormat.render(r.summary).replace(/<\/strong>\s*[:：]\s*/g,'</strong>')}</div>`:'<div class="work-empty">이 기간의 해설은 준비 중입니다.</div>'}
        <details class="briefing-dates"><summary>데이터·해설 기준</summary><p>최신 기사 ${E(fmt(v.data_as_of))}<br>${r?`해설 기준 ${E(r.as_of||'기록 없음')} · 작성 ${E(fmt(r.generated_at))}`:'해설 준비 중'}</p>${r?.legacy?UIHelp.render('기존 리뷰의 해설과 현재 재계산한 집계는 기준 시점이 다를 수 있습니다.'):''}</details></section>
        <div class="briefing-dashboard">
          <div class="briefing-kpis">
            <article class="card briefing-kpi"><span>선별 기사</span><strong>${num(v.kept)}<small>건</small></strong><p>선택한 기간</p></article>
            <article class="card briefing-kpi"><span>직전 기간 대비</span><strong>${v.kept-v.previous.kept>0?'+':''}${num(v.kept-v.previous.kept)}<small>건</small></strong><p>${v.previous.same_elapsed?'동일 경과기간 비교':'이전 기간 비교'}</p></article>
            <a class="card briefing-kpi briefing-task-link" href="#tasks/recommendations?period=${E(v.period)}"><span>추천 과제</span><strong>${taskPreview.length}<small>개</small></strong><p>과제 추천 보기 →</p></a>
          </div>
          <section class="card briefing-activity"><div class="panel-head"><h3>일별 기사 흐름</h3><span class="mut">단위: 건</span></div><div class="briefing-bars" role="img" aria-label="${E(v.series.map(s=>s.date+' '+s.count+'건').join(', '))}">${v.series.map(s=>`<div class="briefing-bar" title="${E(s.date)} · ${num(s.count)}건"><span>${num(s.count)}</span><div class="briefing-bar-track"><i style="height:${s.count/max*100}%"></i></div><time>${E(s.date.slice(8))}</time></div>`).join('')}</div></section>
        </div>
        <div class="briefing-sections">${(r?.sections||[]).map((s,i)=>`<section class="card briefing-section"><header><span class="briefing-section-number">${String(i+1).padStart(2,'0')}</span><h3>${E(s.title.replace(/^\d+\.\s*/,''))}</h3></header>${ReviewFormat.render(s.body)}${evidenceBySection[i].length?`<details class="review-evidence"><summary>근거 기사 <span>${evidenceBySection[i].length}</span></summary><ol class="review-sources">${sourceLinks(evidenceBySection[i])}</ol></details>`:''}</section>`).join('')}</div>
        ${remainingSources.length?`<details class="card briefing-more"><summary>추가 출처 ${remainingSources.length}건</summary><ol class="review-sources">${sourceLinks(remainingSources)}</ol></details>`:''}
        <details class="card briefing-more"><summary>인용 외 주요 기사 <span>${remainingArticles.slice(0,10).length}</span></summary>${remainingArticles.slice(0,10).map(itemHTML).join('')||'<div class="work-empty">추가로 표시할 기사가 없습니다.</div>'}<p><button class="preset" data-explore-period data-since="${E(v.start)}" data-until="${E(v.through)}">관련 기사 더 보기</button></p></details>
        <section class="review-statistics" aria-label="브리핑 상세 통계">
        <details class="card review-stat"><summary>${v.previous.same_elapsed?'직전 동일 경과기간과 비교':'직전 기간과 비교'} · 상세 수치 보기</summary>
        <div class="review-stat-body">
        <p class="mut">비교 대상 ${E(v.previous.start)}부터 ${E(fmt(v.previous.until_exclusive))} 직전까지 ${UIHelp.render('기사 수는 수집 범위에도 영향을 받습니다. 한 기사는 여러 주제에 포함될 수 있으며, 보도 비중의 변화가 실제 시장 성장률을 뜻하지는 않습니다.')}</p>
        <div class="review-metrics"><div><b>${num(v.kept)}</b><span>현재 기간 기사</span></div><div><b>${num(v.previous.kept)}</b><span>비교 기간 기사</span></div><div><b>${((v.kept-v.previous.kept)>0?'+':'')+num(v.kept-v.previous.kept)}</b><span>기사 수 차이</span></div></div>
        <div class="review-table-scroll" tabindex="0" role="region" aria-label="주제별 기사 비중 비교"><table class="review-compare"><caption>주제별 기사 수와 비중 변화</caption><thead><tr><th scope="col">주제</th><th scope="col">기사 수</th><th scope="col">현재 비중</th><th scope="col">이전 비중</th><th scope="col">변화</th></tr></thead><tbody>${v.axes.map(a=>`<tr><th scope="row">${E(label(a.axis))}</th><td>${num(a.n)}</td><td>${a.share}%</td><td>${a.previous_share==null?'—':a.previous_share+'%'}</td><td>${a.previous_share==null||!v.kept?'—':((a.share-a.previous_share)>0?'+':'')+(a.share-a.previous_share).toFixed(1)+'%p'}</td></tr>`).join('')}</tbody></table></div>
        </div></details>
        <details class="card review-stat"><summary>일별 기사 수 보기</summary>
        <div class="review-stat-body"><p class="review-meta">${E(v.start)} ~ ${E(v.through)} · 한국 시간 · 단위: 건</p>
        ${v.series.length?`<ol class="review-daily">${v.series.map(s=>`<li><time datetime="${E(s.date)}">${E(s.date.slice(5).replace('-','/'))}</time><span class="review-daily-track" aria-hidden="true"><i style="width:${s.count/max*100}%"></i></span><b>${num(s.count)}</b></li>`).join('')}</ol>`:'<p class="mut">이 기간에 집계된 기사가 없습니다.</p>'}
        </div></details></section>`;
      
    } catch(e) {
      if(request!==reviewRequest)return;
      Q('#review-content').innerHTML=`<div class="card work-empty">리뷰를 불러오지 못했습니다.<br>${E(e.message)}<br><button class="preset" data-review-retry>다시 불러오기</button></div>`;
    }
  }
  function dialog(title,fields,save) {
    const dlg=Q('#work-dialog');
    Q('#work-dialog-body').innerHTML=`<form id="work-editor"><h2 id="work-dialog-title">${E(title)}</h2>${fields}<p data-dialog-error role="alert"></p><div class="work-toolbar"><button type="button" class="btn-ghost" data-dialog-close>취소</button><button type="submit">저장</button></div></form>`;
    Q('#work-editor').onsubmit=e=>{
      e.preventDefault();
      try {save(Object.fromEntries(new FormData(e.target)));dlg.close();notice('저장했습니다.');}
      catch(err){Q('[data-dialog-error]').textContent='저장하지 못했습니다. '+err.message;}
    };
    if(!dlg.open)dlg.showModal();
  }
  const field=(name,title,value='',textarea=false)=>`<label>${E(title)}${textarea?`<textarea name="${name}" maxlength="50000">${E(value)}</textarea>`:`<input name="${name}" value="${E(value)}" maxlength="${name==='title'?1000:100}" ${name==='title'?'required':''}>`}</label>`;
  function renderBoard() {
    const tasks=state().tasks;
    Q('#task-board').innerHTML=Object.entries(ResearchStore.STATES).map(([status,title])=>`<section class="task-column"><h3>${title} · ${tasks.filter(t=>t.status===status).length}</h3>${tasks.filter(t=>t.status===status).map(t=>`<article class="task-card"><h4>${E(t.title)}</h4><span class="mut">${E(periodLabel(t.period)||'직접 추가')} · ${E(t.owner||'담당자 미지정')}</span><p>${E(t.ask||t.mean||t.fact)}</p>${t.note?`<p class="scrap-note">${E(t.note)}</p>`:''}<button class="preset" data-edit-task="${E(t.key)}">검토·이력</button></article>`).join('')||'<p class="mut">아직 과제가 없습니다.</p>'}</section>`).join('');
  }
  function renderCandidates() {
    const keys=new Set(state().tasks.map(t=>t.key));
    const shown=candidates.map((t,i)=>({t,i})).filter(({t})=>!taskPeriod||t.period===taskPeriod);
    Q('#task-candidate-count').textContent=shown.length;
    Q('#task-candidates').innerHTML=shown.length?shown.map(({t,i})=>`<article class="candidate card"><div class="candidate-meta"><span>${E(periodLabel(t.period))}</span>${keys.has(t.key)?'<span class="candidate-saved">검토 중</span>':''}</div><h3>${E(t.title)}</h3><p>${E(t.objective||t.mean||t.ask)}</p><div class="candidate-actions"><button type="button" class="preset" data-plan-candidate="${i}" aria-haspopup="dialog" aria-controls="task-plan-dialog">진행 계획 보기</button><button class="preset" data-add-candidate="${i}" ${keys.has(t.key)?'disabled':''}>${keys.has(t.key)?'추가됨':'검토에 추가'}</button></div></article>`).join(''):'<div class="card work-empty">이 기간에 작성된 추천 과제가 없습니다.</div>';
  }
  async function loadWorkspace(seg='team') {
    if(seg==='tasks'){showTab('tasks','board');return;}
    Q('#work-team').hidden=false;
    window.TeamProfile?.load();
  }
  async function loadTasks(mode='recommendations',period='') {
    const request=++taskRequest,board=mode==='board';
    Q('#task-recommendations').hidden=board;Q('#task-review-board').hidden=!board;
    document.querySelectorAll('[data-task-tab]').forEach(b=>{
      const on=b.dataset.taskTab===(board?'board':'recommendations');
      b.classList.toggle('active',on);b.setAttribute('aria-pressed',String(on));
    });
    if(board){renderBoard();return;}
    taskPeriod=period;
    Q('#task-candidates').innerHTML='<div class="card work-empty">추천 과제를 불러오는 중…</div>';
    try {
      const result=await api('/api/task-candidates');if(request!==taskRequest)return;
      candidates=result.tasks;
      const periods=[...new Set([...(period?[period]:[]),...candidates.map(t=>t.period)])].sort().reverse();
      Q('#task-period').innerHTML='<option value="">전체 기간</option>'+periods.map(p=>`<option value="${E(p)}">${E(periodLabel(p))}</option>`).join('');
      Q('#task-period').value=period;renderCandidates();
    }catch(e){if(request===taskRequest)Q('#task-candidates').innerHTML=`<div class="card work-empty">추천 과제를 불러오지 못했습니다. ${E(e.message)}<br><button class="preset" data-task-retry>다시 불러오기</button></div>`;}
  }
  function editTask(key) {
    const t=state().tasks.find(t=>t.key===key);if(!t)return;
    dialog('과제 검토',`<h3>${E(t.title)}</h3>${taskDetails(t,t.sources)}<label>검토 상태<select name="status">${Object.entries(ResearchStore.STATES).map(([k,v])=>`<option value="${k}" ${k===t.status?'selected':''}>${v}</option>`).join('')}</select></label>`+
      field('owner','담당자',t.owner)+field('note','검토 의견',t.note,true)+`<details><summary>검토 이력 ${t.history.length}건</summary><ol>${[...t.history].reverse().map(h=>`<li>${E(fmt(h.at))} · ${E(ResearchStore.STATES[h.status])} · ${E(h.owner||'미지정')}<br>${E(h.note)}</li>`).join('')}</ol></details>`,
      values=>{store.editTask(key,values);renderBoard();});
  }
  document.addEventListener('click', async e=>{
    const b=e.target.closest('button,a'); if(!b)return;
    try {
      if(b.matches('[data-plan-review]')){
        const r=currentReview?.editorial,t=r?.tasks?.[Number(b.dataset.planReview)];
        if(t)openTaskPlan(t,(r.sources||[]).filter(s=>(t.source_ids||[]).includes(s.id)));
      }
      if(b.matches('[data-plan-candidate]')){const t=candidates[Number(b.dataset.planCandidate)];if(t)openTaskPlan(t,t.sources||[]);}
      if(b.matches('[data-plan-close]'))Q('#task-plan-dialog').close();
      if(b.matches('[data-review-kind]'))showTab('briefing',b.dataset.reviewKind);
      if(b.matches('[data-review-open]'))showTab('briefing',b.dataset.reviewOpen);
      if(b.matches('[data-review-current]'))showTab('briefing',reviewKind);
      if(b.matches('[data-review-retry]')){const x=routeOf();loadReviews(x.mode+':'+(x.period||''));}
      if(b.matches('[data-review-task]')){const r=currentReview.editorial,t=r.tasks.find(t=>t.title===b.dataset.reviewTask);if(t)write(()=>store.addTask({...t,key:r.period+':'+t.title,period:r.period,sources:(r.sources||[]).filter(s=>(t.source_ids||[]).includes(s.id))}),'과제 검토 보드에 추가했습니다.');}
      if(b.matches('[data-explore-period]')){Q('#f-since').value=b.dataset.since;Q('#f-until').value=b.dataset.until;Q('#f-q').value='';Q('#f-axis').value='';showTab('explore','articles');}
      if(b.matches('[data-task-tab]'))showTab('tasks',b.dataset.taskTab);
      if(b.matches('[data-task-retry]'))loadTasks('recommendations',taskPeriod);
      if(b.matches('[data-dialog-close]'))Q('#work-dialog').close();
      if(b.matches('[data-add-candidate]')){if(write(()=>store.addTask(candidates[Number(b.dataset.addCandidate)]),'검토 보드에 추가했습니다.')){renderBoard();renderCandidates();}}
      if(b.matches('[data-edit-task]'))editTask(b.dataset.editTask);
      if(b.matches('[data-new-task]'))dialog('과제 직접 추가',field('title','과제명')+field('fact','관찰한 사실','',true)+field('mean','업무 관련성','',true)+field('ask','확인할 점','',true),v=>{store.addTask({...v,key:'task:'+newKey()});showTab('tasks','board');});
    }catch(err){notice(err.message,true);}
  });
  Q('#task-plan-dialog').addEventListener('close',()=>document.body.classList.remove('task-plan-open'));
  document.addEventListener('change',async e=>{
    const t=e.target;
    if(t.id==='review-period')showTab('briefing',reviewKind+':'+t.value);
    if(t.id==='task-period')showTab('tasks','recommendations',{tab:'tasks',mode:'recommendations',period:t.value});
  });
  window.addEventListener('storage',e=>{if(e.key===ResearchStore.KEY){if(!Q('#work-dialog').open){renderBoard();}}});
  return {loadReviews,loadWorkspace,loadTasks,notice};
})();
