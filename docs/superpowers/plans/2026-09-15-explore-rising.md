# 검색 중 급상승 주제 상시 노출 계획

> 후속 구현자는 superpowers:executing-plans로 작업별 구현·검증을 진행한다. 2026-09-15: 사용자 승인 후 구현 완료. 실제 반영·검증 기록은 아래 실행 결과 참고.

**목표:** 검색어 입력 후에도 급상승 주제를 확인하고 다른 주제로 이동할 수 있게 한다.

**구조:** 기사·시간 흐름·연관어의 공통 결과 영역에 보조 패널을 둔다. 기존 `/api/trend`와 브라우저 API 캐시를 재사용하되, 주제 패널의 성공·실패·로딩은 본 검색과 분리한다.

**기술:** 현재 Vanilla JS/HTML/CSS, FastAPI, 기존 급상승 집계. 새 UI 프레임워크·검색 엔진 도입 없음.

**설계 근거:** 사용자 제공 Korea.net 이미지의 오른쪽 Popular Words 배치를 참고한다. 사이트 전체 디자인이나 방문자 인기 검색어라는 의미를 가져오지 않는다. 현재 데이터는 뉴스에서 차지하는 비중의 상승을 측정하므로 명칭은 ‘급상승 주제’다.

## 화면 및 동작

- 데스크톱: 검색 결과와 260px 보조 패널을 24px 간격으로 배치. 전체 탐색 폭은 기존 최대 1,600px를 유지한다. 보조 패널은 `position:sticky; top:24px`로 목록 스크롤 중 유지하되 화면보다 길면 일반 흐름으로 둔다.
- 1,180px 미만: 결과 위의 접이식 ‘급상승 주제’ 한 줄로 전환. 기본 접힘 상태에서 상위 3개 주제를 짧게 보여주고, 펼치면 전체 목록. 모바일에서 3열(연관망·기사·급상승)을 만들지 않는다.
- 내용: AI·빅데이터 5개와 소상공인 5개를 구분. 순위, 키워드, 현재 건수와 신규 여부를 표시한다. 이미 있는 두 그룹을 사용해 AI 관련 키워드가 소상공인을 밀어내는 문제를 피한다.
- 집계 기준: ‘최신 집계 주 · 9/7~9/13’처럼 API가 반환한 주차를 표시. 검색 날짜와 별개임을 도움말에 명시한다. 과거 날짜 검색에서도 급상승은 최신 수집 주 기준이며, 선택 기간 기준 급상승은 1차 범위에 넣지 않는다.
- 클릭: 해당 주제로 **새 검색**. 현재 날짜·주제 필터를 유지하고 기사 보기 첫 페이지로 이동한다. 뒤로 가면 원래 검색어·기간·보기로 복원된다. 결과가 없으면 ‘선택한 기간에는 결과가 없습니다’와 필터 해제 행동을 제공한다.
- 키워드 클릭 외 패널 펼침·그룹 보기 전환은 기사 검색을 다시 실행하지 않는다.
- 로딩·빈 목록·오류를 구분하고 오류에는 ‘다시 불러오기’를 제공한다. 검색 결과는 그대로 유지한다.
- 입력 문자열은 `textContent` 또는 기존 `esc()`로 처리하며 HTML로 신뢰하지 않는다.

## 작업 1: 공통 보조 영역 배치

**파일:** `web/static/index.html`, `web/static/workspace.css`, `web/static/app.js`.

- [ ] `explore-results-heading` 바로 뒤에 공통 `aside#explore-topics` 추가. 기사/시간 흐름/연관어의 기존 패널은 `section.explore-layout`의 결과 열에 묶는다. 비탐색 페이지를 이 레이아웃 안으로 이동시키지 않는다.
- [ ] 탐색 시작 화면의 `#explore-rising`을 이 공통 패널로 이동하고 기존 시작 카드 중복 제거. ID와 이벤트 핸들러가 한 곳만 남도록 확인.
- [ ] `showTab`에서 탐색 세 보기와 시작 화면에만 패널 표시. 브리핑·법령·작업실에서는 숨김. 검색 제목과 보기 탭은 결과와 보조 패널 위의 공통 위치 유지.
- [ ] 다음 기본 CSS를 적용하고, 실제 연관어 기사 패널의 최소 읽기 폭을 확인한다.

```css
.explore-layout { display:grid; grid-template-columns:minmax(0,1fr) 260px; gap:24px; align-items:start; }
#explore-topics { position:sticky; top:24px; min-width:0; }
@media(max-width:1179px) {
  .explore-layout { display:block; }
  #explore-topics { position:static; margin-bottom:18px; }
}
```

## 작업 2: 독립 로딩·캐시

**파일:** `web/static/app.js`, 기존 `web/static/api-client.js` 재사용.

**입력/출력:** `loadExploreTopics(): Promise<void>`, `/api/trend`의 `week_label` 및 `rows`를 표시한다. 검색 상태를 인수로 받지 않아 검색어 변경마다 집계 요청을 새로 만들지 않는다.

- [ ] 기존 `loadExploreStart()`의 급상승 조회 부분을 `loadExploreTopics()`로 추출. 기사 조회와 함께 `await Promise.all()`로 묶지 않고 독립적으로 호출한다.
- [ ] 최신 주는 한 번만 결정하고 두 그룹에 같은 주를 전달한다. 예: 첫 `/api/trend?axis=ai,bigdata&top=5` 응답의 `week`를 소상공인 요청에도 적용한다. 주가 없으면 빈 상태 표시.
- [ ] 프로세스 중복 요청은 기존 `api()`가 합치게 한다. 두 그룹 요청의 성공 여부는 별도 표시하여 한 그룹 실패가 다른 그룹을 가리지 않게 한다.
- [ ] 실패 시 재시도 버튼은 급상승 함수만 다시 실행하고 검색 본문을 초기화하지 않는다. 최신 주 갱신은 패널을 재방문하고 캐시가 만료된 시점에 확인한다.

요청 형태:

```js
const technology = await api('/api/trend', {axis:'ai,bigdata',top:5});
// 실제 구현에서는 첫 응답을 바로 표시하고, 두 번째 요청은 해당 그룹에서 오류 처리한다.
if (technology.week) {
  const smallbiz = await api('/api/trend', {axis:'smallbiz',week:technology.week,top:5});
}
```

## 작업 3: 키워드 이동·상태 복원

**파일:** `web/static/app.js`, `tests/navigation-state.test.cjs`.

- [ ] 다음 조건 보존 테스트를 먼저 추가해 새 이동 기능을 검증한다.

```js
test('rising topic navigation changes only query and view', () => {
  const before = state.parseRoute('#explore/graph?q=AI&axis=ai&since=2026-09-01&until=2026-09-07', '');
  const next = {...before,mode:'articles',context:{...before.context,q:'공공데이터'}};
  const restored = state.parseRoute('#' + state.routeHash(next), '');
  assert.equal(restored.context.q, '공공데이터');
  assert.equal(restored.context.axis, 'ai');
  assert.equal(restored.context.since, '2026-09-01');
  assert.equal(restored.context.until, '2026-09-07');
  assert.equal(restored.mode, 'articles');
});
```

- [ ] 패널의 버튼에 `data-rising-query`를 사용한다. 클릭 시 기존 정규화 경로를 사용해 `showTab('explore','articles', nextRoute)` 호출. `history.replaceState`로 원래 검색 기록을 덮어쓰지 않는다.
- [ ] 이전 검색의 늦은 응답이 새 키워드의 결과를 덮지 않는지 기존 request guard로 확인한다.
- [ ] 실브라우저에서 검색 → 급상승 키워드 → 뒤로 가기 후 검색어·날짜·주제·보기 복원을 확인한다. 위 순수 함수 테스트만으로 실제 브라우저 기록 검증을 대신하지 않는다.

## 작업 4: 완료 기준

- [ ] 390 / 768 / 1,280 / 1,920px에서 본문 가로 넘침 없음. 200% 확대에서 검색 버튼·보기 탭과 패널 접근 가능.
- [ ] 기사·시간 흐름·연관어에서 검색 중에도 주제 목록이 남아 있고 검색 실패 때도 독립적으로 동작.
- [ ] 느린 급상승 요청 동안 기사 목록은 먼저 표시. 급상승 오류·빈 목록 때문에 기사 영역이 바뀌지 않음.
- [ ] 패널 키워드는 Tab·Enter로 실행 가능하고 접기 토글은 펼침 상태를 스크린리더에 전달.
- [ ] 두 그룹 모두 동일 집계 주를 표시. 날짜 검색 범위와 집계 기준을 오해하지 않게 확인.
- [ ] `node --test tests/*.test.cjs`, `python -m pytest -q tests/test_explore_api.py tests/test_response_cache.py` 통과.
- [ ] 같은 검색어·같은 캐시 조건에서 구현 전후 본 기사 응답 및 렌더 시간 비교. 보조 패널 때문에 본 검색 p95가 악화되면 요청 우선순위·레이아웃을 조정.

예상 구현 범위는 공통 레이아웃·기존 API 연결·상태 보존·검증이다. 클릭 로그 수집, 방문자 인기 검색어 통계, 기간별 급상승 재계산은 포함하지 않는다.

## 실행 결과 — 2026-09-15

구현 완료, 로컬 8024에서 검증. 배포·커밋은 수행하지 않음.

- `index.html`: 공통 결과 컨테이너와 `aside`, 네이티브 details 접기/펼치기. 기존 네 개 패널을 초기화 시 같은 DOM 노드 그대로 결과 열로 이동하여 ID·이벤트·상태를 보존했다.
- `explore-topics.js`: 독립 로더, 두 그룹 동일 집계 주, 그룹별 오류·빈 결과·로딩, 진행 중 요청 재사용, 30초 내 보기 전환 재조회 방지, 명시적 재시도. 첫 그룹 실패 시 둘째 그룹은 최신 주를 독립 조회한다.
- `app.js`: 검색과 독립적으로 로더 호출, 키워드 버튼으로 기사 첫 페이지 이동, 적용 중 날짜·축 조건 보존, 결과가 없으면 기간·주제 필터 해제. 펼침은 검색을 실행하지 않는다.
- `workspace.css`: 데스크톱 보조 열 260px·간격 24px, 좁은 화면에서는 결과 위 접이식 패널. 화면보다 긴 패널은 sticky 해제. 1180~1600px에서는 연관망과 근거 기사를 세로 배치해 세 열로 좁아지는 문제를 피했다. 기사 표는 고정 열 배치로 긴 요약이 보조 목록을 밀어내지 않게 했다.
- `tests/explore-topics.test.cjs`: 느린 둘째 그룹, 첫 그룹 실패 후 재시도, 캐시·동시 요청, 새 주차 전환에서 오래된 그룹 제거, 키워드 이동의 필터 보존 테스트. 위 예시를 그대로 복제하는 대신 실제 로더·경로 생성 함수를 검사한다.

검증:

- Node 테스트 32개, Python 탐색·캐시·연관어 테스트 15개 통과(기존 FastAPI deprecation 경고 4개).
- 실제 DB에서 AI·빅데이터 5개와 소상공인 5개 및 2026년 9월 2주차 표시.
- 기사/시간 흐름/연관어에서 목록 10개 유지. 시간 흐름의 AI 검색 → 키보드 Enter로 AI도시 선택 → 날짜 9/1~9/7 유지 → 뒤로 가기에서 AI 검색 및 시간 흐름 복원.
- 일치 기사 없는 AI도시 기간 검색에서도 급상승 유지 및 필터 해제 동작 확인.
- 390/768/1280/1920px에서 페이지 가로 넘침 없음. 모바일 기본 접힘·상위 주제 미리보기·키보드 펼치기 확인. 1280px에서 기사 표 641px와 표 컨테이너 641px 일치, 보조 열 260px.
- 인위적으로 늦춘 급상승 응답의 독립성은 로더 테스트로 검증. 배포 환경의 렌더 p95 전후 비교와 실제 브라우저 200% 확대는 이번에 측정하지 않았으며 개선율을 주장하지 않는다.

상단 작업 목록은 원래 제안 절차를 보존한 것이다. 실제 구현 방식과 검증 범위는 이 실행 결과를 기준으로 한다.
