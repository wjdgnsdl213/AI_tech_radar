const test = require('node:test');
const assert = require('node:assert/strict');
const {createStore} = require('../web/static/workspace-store.js');

test('task execution details survive saving, editing, and backup restore', () => {
  const memory = () => {const data = new Map(); return {getItem:k=>data.get(k),setItem:(k,v)=>data.set(k,v)};};
  const store = createStore(memory());
  const details = {objective:'상담 오류 줄이기',team_fit:'AI도우미',
    approach:'1주차: 기준 질문 구성\n2주차: 오답 검증',deliverables:'검증표',
    success_criteria:'출처 정확도 비교',cautions:'개인정보 제외',duration:'2주 검토안'};
  store.addTask({key:'review:1',title:'상담 품질 검증',...details});
  store.editTask('review:1',{owner:'담당자',note:'검토 시작'});
  const restored = createStore(memory());
  restored.restore(store.backup());
  for (const [key,value] of Object.entries(details)) assert.equal(restored.read().tasks[0][key],value);
});
