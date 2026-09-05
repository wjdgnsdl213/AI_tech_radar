# 004 — 연관어 지도의 버튼 줌을 이어지게 만든다

- **Status**: TODO
- **Commit**: 81daeb0
- **Severity**: MEDIUM
- **Category**: Missed opportunities (공간 연속성) / Interruptibility
- **Estimated scope**: 1 파일 (`web/static/app.js`), 약 45줄
- **Depends on**: 없음 (001의 CSS 토큰을 쓰지 않는다 — 아래 이유 참고)

## Problem

연관어 지도의 `+` / `−` 버튼은 배율을 한 번에 **1.35배** 갈아치운다.
'전체' 버튼은 `BASE`로 즉시 되돌린다. 지도에서 보던 자리가 어디였는지 잃는다.

```js
// web/static/app.js:782-788 — 현재
$('#graph-tools').addEventListener('click', e => {
  if (e.target.closest('[data-relayout]')) { resetLayout(); return; }
  const b = e.target.closest('[data-zoom]');
  if (!b || !VIEW) return;
  const d = +b.dataset.zoom;
  if (d === 0) { VIEW = { ...BASE }; applyView(); } else zoomBy(d > 0 ? 1.35 : 1 / 1.35);
});
```

```js
// web/static/app.js:705-722 — 현재
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
```

### ★ CSS transition으로는 못 한다

`applyView`는 `viewBox`를 **attribute**로 쓴다. `viewBox`는 CSS 속성이 아니라
`transition`이 아예 걸리지 않는다. `#graph-svg svg{transition:...}` 같은 규칙은
조용히 아무 일도 하지 않는다. **rAF로 값을 직접 보간해야 한다.**

### ★ 휠 줌과 끌기에는 절대 걸면 안 된다

`zoomBy`는 두 곳에서 불린다:

```js
// web/static/app.js:726-732 — 휠. 손가락 움직임이 곧 속도라 이미 연속적이다.
  zoomBy(Math.exp(-e.deltaY * .0018),
    (e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height);
}, { passive: false });
```

```js
// web/static/app.js:749-752 — 끌기. 포인터를 따라가야 하므로 지연이 있으면 안 된다.
    VIEW.x = pan.vx - dx * kx;
    VIEW.y = pan.vy - dy * ky;
    applyView();
```

휠·끌기에 보간을 걸면 손보다 지도가 늦게 따라와 고무줄처럼 밀린다.
**버튼으로 누른 줌에만** 건다.

## Target

`zoomBy`에서 목표 계산과 적용을 분리하고, 버튼 경로만 rAF로 잇는다.

```js
/* target — app.js:714-722 의 zoomBy 를 아래 네 조각으로 교체 */

/** fx,fy = 화면상의 고정점(0~1). 그 지점이 제자리에 남도록 확대한다 —
 *  커서 아래를 보고 있다가 휠을 굴렸는데 딴 데로 튀면 길을 잃는다. */
function viewFor(k, fx = .5, fy = .5) {
  const w = Math.min(BASE.w * 1.2, Math.max(BASE.w * .12, VIEW.w / k));
  const h = VIEW.h * (w / VIEW.w);
  return { x: VIEW.x + (VIEW.w - w) * fx, y: VIEW.y + (VIEW.h - h) * fy, w, h };
}

/* 휠 줌 — 손가락이 곧 속도다. 보간하면 오히려 밀린다. */
function zoomBy(k, fx = .5, fy = .5) {
  if (!VIEW) return;
  cancelZoom();
  VIEW = viewFor(k, fx, fy);
  applyView();
}

/* ★ viewBox는 attribute라서 CSS transition이 안 걸린다. 값을 직접 보간한다.
   곡선은 --ease-out(cubic-bezier(0.23,1,0.32,1))과 같은 성격의
   3차 ease-out이다 — 빨리 출발해서 부드럽게 선다. */
let zoomAnim = null;
function cancelZoom() {
  if (zoomAnim) { cancelAnimationFrame(zoomAnim); zoomAnim = null; }
}

function animateView(to, ms = 200) {
  if (!VIEW) return;
  cancelZoom();
  // 움직임을 줄이도록 설정했으면 그냥 옮긴다
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    VIEW = to; applyView(); return;
  }
  const from = { ...VIEW }, t0 = performance.now();
  const step = now => {
    const p = Math.min(1, (now - t0) / ms), k = 1 - Math.pow(1 - p, 3);
    VIEW = { x: from.x + (to.x - from.x) * k, y: from.y + (to.y - from.y) * k,
             w: from.w + (to.w - from.w) * k, h: from.h + (to.h - from.h) * k };
    applyView();
    zoomAnim = p < 1 ? requestAnimationFrame(step) : null;
  };
  zoomAnim = requestAnimationFrame(step);
}
```

버튼 핸들러:

```js
/* target — app.js:782-788 */
$('#graph-tools').addEventListener('click', e => {
  if (e.target.closest('[data-relayout]')) { resetLayout(); return; }
  const b = e.target.closest('[data-zoom]');
  if (!b || !VIEW) return;
  const d = +b.dataset.zoom;
  if (d === 0) animateView({ ...BASE });
  else animateView(viewFor(d > 0 ? 1.35 : 1 / 1.35));
});
```

끌기가 시작되면 돌던 보간을 끊는다 — 안 그러면 손과 보간이 서로 `VIEW`를 밀어 떤다:

```js
/* target — app.js:767-770 의 pointerdown 핸들러 앞부분 */
MAP.addEventListener('pointerdown', e => {
  if (!VIEW || e.button) return;
  cancelZoom();                             // 돌던 줌 보간을 끊는다
  const r = MAP.getBoundingClientRect();
```

## Repo conventions to follow

- 이 파일은 지도 조작을 `BASE`/`VIEW` 두 전역과 `applyView()` 하나로 다룬다
  (`app.js:703-711`). 새 상태를 만들지 말고 `VIEW`를 그대로 쓴다.
- `$()` 헬퍼를 쓴다.
- 주석은 한국어로 '왜'를 적는다. `app.js:734-738`(`setPointerCapture를 쓰면 안 된다`)이
  이 파일의 표준 톤이다 — **하지 말아야 할 것과 그 실측 근거**를 적는 방식.
- 새 함수는 `applyView` 바로 아래, 즉 지도 조작 구역 안에 모아 둔다.

## Steps

1. `web/static/app.js:713-722`(주석 `/** fx,fy = ... */` 부터 `zoomBy` 끝 `}` 까지)를
   위 Target의 네 조각(`viewFor`, `zoomBy`, `cancelZoom`, `animateView`)으로 교체한다.
   원래 주석은 `viewFor` 위로 옮겨 붙인다.
2. `app.js:787`의 `if (d === 0) { VIEW = { ...BASE }; applyView(); } else zoomBy(d > 0 ? 1.35 : 1 / 1.35);`
   두 줄을 위 Target의 `if (d === 0) animateView(...)` / `else animateView(...)`로 바꾼다.
3. `app.js:768`(`if (!VIEW || e.button) return;`) 바로 다음 줄에 `cancelZoom();`을 넣는다.
4. **휠 핸들러(`app.js:726-732`)는 그대로 둔다.** `zoomBy`가 내부에서 `cancelZoom()`을
   부르므로 자동으로 안전해진다.

## Boundaries

- **CSS는 건드리지 않는다.** `viewBox`에 transition을 걸려는 시도를 하지 말 것 — 안 된다.
- **휠 줌(`app.js:726-732`)과 끌기(`app.js:744-757`)에 보간을 넣지 말 것.**
  이 계획서의 핵심 제약이다.
- 더블클릭 재중심(그래프를 다시 그리는 경로)은 범위 밖이다. 그건 `innerHTML` 통째
  교체라 성격이 다르다.
- `zoomBy`의 배율 상·하한(`BASE.w * 1.2` / `BASE.w * .12`)과 1.35 배율을 바꾸지 말 것.
- `applyView` 안의 `#zoom-label` 갱신을 빼지 말 것 — 보간 중에 퍼센트가 같이 올라가야 한다.
- 새 의존성 금지.
- 코드가 위 인용과 다르면 **멈추고 보고할 것.**

## Verification

- **기계적**: `node --check web/static/app.js` 가 오류 없이 끝나야 한다.
- **눈으로 확인**: 앱을 띄우고 연관어 탭에서 키워드를 검색해 지도를 띄운다.
  - `+`를 한 번 누른다. 배율이 **이어지며** 올라가야 한다. 오른쪽 `100%` 라벨의
    숫자도 같이 올라가야 한다 — 숫자만 튀면 `applyView`가 보간 밖에서 불린 것이다.
  - `전체`를 누른다. 전체 보기로 **미끄러져 돌아가야** 한다.
  - **`+`를 빠르게 여러 번 연타한다.** 매번 현재 배율에서 이어서 확대돼야 한다.
    누를 때마다 이전 배율로 튕겼다가 다시 가면 `cancelZoom`이 빠진 것이다.
  - **휠을 굴린다. 반드시 즉각적이어야 한다.** 조금이라도 미끄러지듯 따라오면
    잘못 구현한 것이다 — 되돌린다.
  - **`+`를 누르자마자 곧바로 지도를 끌어 옮긴다.** 지도가 손을 정확히 따라와야 한다.
    끌면서 배율이 계속 변하거나 손과 지도가 어긋나면 3번 단계(`cancelZoom`)가 빠진 것이다.
  - 노드를 클릭·더블클릭해 본다. `app.js:734-738`의 주석대로 노드 선택이 살아 있어야 한다.
    이 계획서는 포인터 처리를 바꾸지 않으므로 그대로여야 정상이다.
  - DevTools > Performance로 `+` 누르는 구간을 녹화하고 프레임 드랍이 없는지 본다.
    노드가 많은(3홉) 그래프에서도 확인할 것.
  - Rendering > `prefers-reduced-motion: reduce`를 켜고 `+`를 누른다. **즉시** 확대돼야 한다.
- **Done when**: 버튼 줌만 이어지고, 휠과 끌기는 예전 그대로 즉각적이다.
