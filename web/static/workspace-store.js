/* 개인 자료는 이 브라우저에 보관한다. DOM과 분리해 저장 실패·복원을 검증한다. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ResearchStore = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const KEY = 'sab-research-v1';
  const STATES = {observing: '관찰 중', reviewing: '검토 중', hold: '보류', complete: '검토 완료'};
  const string = (s, max = 50000) => typeof s === 'string' ? s.slice(0, max) : '';
  const escape = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const stamp = () => new Date().toISOString();
  function safeURL(s) {
    try {const u = new URL(s); return ['https:', 'http:'].includes(u.protocol) ? u.href : '';} catch {return '';}
  }
  function sources(rows) {
    if (!Array.isArray(rows)) return [];
    return rows.slice(0, 100).map(s => ({id: Number(s.id) || 0, title: string(s.title, 500), url: safeURL(s.url)}));
  }
  function scrap(s) {
    if (!s || !string(s.key, 300) || !string(s.title, 1000)) throw new Error('저장 자료의 제목과 식별자가 필요합니다.');
    return {key: string(s.key, 300), kind: ['article','review','note'].includes(s.kind) ? s.kind : 'article',
      title: string(s.title, 1000), text: string(s.text), url: safeURL(s.url), source: string(s.source, 200),
      published: string(s.published, 40), topic: string(s.topic, 100), note: string(s.note),
      sources: sources(s.sources), savedAt: string(s.savedAt, 40) || stamp(), updatedAt: string(s.updatedAt, 40) || stamp()};
  }
  function task(t) {
    if (!t || !string(t.key, 400) || !string(t.title, 1000)) throw new Error('과제 제목과 식별자가 필요합니다.');
    if (t.status && !Object.hasOwn(STATES, t.status)) throw new Error('과제 상태를 확인해 주세요.');
    return {key:string(t.key,400),title:string(t.title,1000),period:string(t.period,40),
      fact:string(t.fact),mean:string(t.mean),ask:string(t.ask),sources:sources(t.sources),
      status:t.status || 'observing',owner:string(t.owner,100),note:string(t.note),
      history:Array.isArray(t.history) ? t.history.slice(-100).map(h => ({at:string(h.at,40),
        status:Object.hasOwn(STATES,h.status) ? h.status : 'observing',owner:string(h.owner,100),note:string(h.note)})) : []};
  }
  function validate(data) {
    if (!data || data.version !== 1 || !['scraps','issues','tasks'].every(k => Array.isArray(data[k]) && data[k].length <= 10000)) {
      throw new Error('SAB Trend 백업 파일 형식이 아닙니다. 기존 자료는 변경하지 않았습니다.');
    }
    const unique = list => [...new Map(list.map(r => [r.key,r])).values()];
    return {version:1, scraps:unique(data.scraps.map(scrap)), tasks:unique(data.tasks.map(task)),
      issues:unique(data.issues.map(i => {
        const q = string(i.query,100).trim();
        if (!q) throw new Error('추적 키워드가 비어 있습니다.');
        return {key:q.toLocaleLowerCase(),query:q,savedAt:string(i.savedAt,40) || stamp()};
      }))};
  }
  function createStore(storage) {
    const read = () => {
      const raw = storage.getItem(KEY);
      return raw ? validate(JSON.parse(raw)) : {version:1,scraps:[],issues:[],tasks:[]};
    };
    function change(fn) {
      const data = read(); fn(data);
      const clean = validate(data);
      storage.setItem(KEY, JSON.stringify(clean));
      return clean;
    }
    return {read,
      saveScrap: value => change(d => {if (!d.scraps.some(s => s.key === value.key)) d.scraps.unshift(scrap(value));}),
      editScrap: (key, changes) => change(d => {
        const i = d.scraps.findIndex(s => s.key === key);
        if (i < 0) throw new Error('저장 자료를 찾을 수 없습니다.');
        d.scraps[i] = scrap({...d.scraps[i],...changes,key,updatedAt:stamp()});
      }),
      removeScrap: key => change(d => {d.scraps = d.scraps.filter(s => s.key !== key);}),
      followIssue: query => change(d => {
        query = string(query,100).trim();
        if (!query) throw new Error('추적할 키워드를 입력해 주세요.');
        if (!d.issues.some(i => i.key === query.toLocaleLowerCase())) d.issues.push({key:query.toLocaleLowerCase(),query,savedAt:stamp()});
      }),
      unfollowIssue: key => change(d => {d.issues = d.issues.filter(i => i.key !== key);}),
      addTask: value => change(d => {
        if (!d.tasks.some(t => t.key === value.key)) {
          const t = task(value);
          t.history = [{at:stamp(),status:t.status,owner:t.owner,note:t.note}];
          d.tasks.unshift(t);
        }
      }),
      editTask: (key, changes) => change(d => {
        const i = d.tasks.findIndex(t => t.key === key);
        if (i < 0) throw new Error('검토 과제를 찾을 수 없습니다.');
        const old = d.tasks[i], next = task({...old,...changes,key});
        if (['status','owner','note'].some(k => next[k] !== old[k])) {
          next.history = [...old.history,{at:stamp(),status:next.status,owner:next.owner,note:next.note}].slice(-100);
        }
        d.tasks[i] = next;
      }),
      backup: () => JSON.stringify(read(), null, 2),
      restore: text => {
        if (text.length > 10000000) throw new Error('백업 파일은 10MB 이하여야 합니다.');
        const incoming = validate(JSON.parse(text));
        return change(d => {
          for (const kind of ['scraps','issues','tasks']) {
            const keys = new Set(d[kind].map(x => x.key));
            d[kind].push(...incoming[kind].filter(x => !keys.has(x.key)));
          }
        });
      }
    };
  }
  function exportBundle(data, selected, title = '검토 자료') {
    const keys = new Set(selected), rows = data.scraps.filter(s => keys.has(s.key)).map(scrap);
    if (!rows.length) throw new Error('내보낼 자료를 선택해 주세요.');
    // Markdown의 raw HTML과 제목 개행이 보고서 구조를 바꾸지 않게 한다.
    const md = s => String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
      .replace(/([\\`*_\[\]#])/g,'\\$1');
    const link = url => safeURL(url).replace(/[()<>"\s]/g, c => encodeURIComponent(c));
    const date = stamp().slice(0,10);
    const markdown = [`# ${md(title).replace(/\n/g,' ')}`,`작성일: ${date} · 선택 자료 ${rows.length}건`,
      ...rows.map((s,i) => `## ${i+1}. ${md(s.title).replace(/\n/g,' ')}\n\n${md(s.published)} · ${md(s.source)} · ${md(s.topic)}\n\n${md(s.text)}\n\n메모: ${md(s.note)}\n\n${s.url ? `원문: ${link(s.url)}` : ''}\n` +
        s.sources.filter(x => x.url).map(x => `- ${md(x.title)}: ${link(x.url)}`).join('\n'))].join('\n\n');
    const html = `<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escape(title)}</title>
      <style>body{font:15px/1.8 system-ui,sans-serif;color:#162336;max-width:850px;margin:40px auto;padding:0 24px}h1{font-size:28px}h2{font-size:20px}article{border-top:1px solid #dbe0e8;margin-top:28px;padding-top:16px}.text{white-space:pre-wrap;overflow-wrap:anywhere}.meta{color:#5b6575;font-size:13px}a{color:#2453a5;overflow-wrap:anywhere}.note{background:#f4f6fa;padding:12px}@media print{body{margin:0;max-width:none}h2{break-after:avoid}a{color:inherit}}</style>
      <h1>${escape(title)}</h1><p class="meta">${date} · 선택 자료 ${rows.length}건 · 브라우저 인쇄에서 PDF로 저장할 수 있습니다.</p>
      ${rows.map((s,i) => `<article><h2>${i+1}. ${escape(s.title)}</h2><p class="meta">${escape(s.published)} · ${escape(s.source)} · ${escape(s.topic)}</p><div class="text">${escape(s.text)}</div>${s.note ? `<p class="text note">메모: ${escape(s.note)}</p>`:''}${s.url ? `<p>원문: <a href="${escape(s.url)}" rel="noopener noreferrer">${escape(s.url)}</a></p>`:''}${s.sources.filter(x=>x.url).map(x=>`<p><a href="${escape(x.url)}" rel="noopener noreferrer">${escape(x.title)}</a></p>`).join('')}</article>`).join('')}</html>`;
    return {markdown,html};
  }
  return {createStore, exportBundle, safeURL, STATES, KEY};
});
