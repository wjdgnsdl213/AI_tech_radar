# 007 — '배치 되돌리기'가 제자리로 미끄러져 돌아가게 한다

- **Status**: TODO
- **Commit**: 81daeb0
- **Severity**: LOW
- **Category**: Missed opportunities (갑작스러운 변화 방지)
- **Estimated scope**: 1 파일 (`web/static/app.js`), 약 30줄
- **Depends on**: 없음 (004와 같은 파일·같은 구역을 만지므로 **004 다음에 하는 편이 낫다**)

## Problem

연관어 지도에서 노드를 끌어 옮긴 뒤 '배치 되돌리기'를 누르면 모든 노드가
**한 프레임에 순간이동**한다. 무엇이 어디로 돌아갔는지 보이지 않아, 되돌린 건지
새로 그린 건지 구분이 안 된다.

```js
// web/static/app.js:693-696 — 현재
function resetLayout() {
  if (!LAYOUT) return;
  Object.keys(LAYOUT.home).forEach(kw => moveNode(kw, ...LAYOUT.home[kw]));
}
```

### ★ CSS transition으로는 못 한다

`moveNode`는 노드·라벨은 `transform` **attribute**로 옮기지만, 연결된 간선은
`x1`/`y1`/`x2`/`y2` **attribute**를 직접 쓴다:

```js
// web/static/app.js:680-691 — 현재 (건드리지 않는다)
function moveNode(kw, x, y) {
  if (!LAYOUT) return;
  LAYOUT.pos[kw] = [x, y];
  const h = LAYOUT.home[kw], g = LAYOUT.el[kw], lb = LAYOUT.lab[kw];
  const tr = `translate(${(x - h[0]).toFixed(1)} ${(y - h[1]).toFixed(1)})`;
  if (g) g.setAttribute('transform', tr);
  if (lb) lb.setAttribute('transform', tr);   // 라벨은 별도 층이라 따로 옮긴다
  (LAYOUT.inc[kw] || []).forEach(([el, end]) => {
    el.setAttribute('x' + end, x.toFixed(1));
    el.setAttribute('y' + end, y.toFixed(1));
  });
}
```

`.gnode`에 `transition:transform`을 걸면 **노드와 라벨만 미끄러지고 간선은 즉시
튀어서**, 움직이는 동안 선이 점에서 떨어져 나간다. 지금보다 더 나쁘다.
게다가 `moveNode`는 노드를 끌 때 매 프레임 불리므로(`app.js:748`),
CSS 전환을 걸면 **끌기가 손보다 늦게 따라오게 된다.**

그러므로 CSS가 아니라 **JS에서 좌표를 보간하고 `moveNode`를 그대로 부른다.**
그러면 간선도 같은 프레임에 함께 따라온다.

## Target

```js
/* target — app.js:693-696 의 resetLayout 을 아래로 교체 */

/* 배치 되돌리기.
   ★ CSS transition을 쓸 수 없다. moveNode가 간선의 x1/y1/x2/y2를 attribute로
     직접 쓰는데, .gnode에만 전환을 걸면 점과 라벨만 미끄러지고 선은 튀어서
     움직이는 동안 선이 점에서 떨어진다. 게다가 moveNode는 노드를 끌 때
     매 프레임 불리므로(onPointerMove) 전환을 걸면 끌기가 손보다 늦어진다.
     그래서 좌표를 여기서 보간하고 moveNode는 그대로 부른다 — 간선도 같이 온다. */
let layoutAnim = null;

function cancelLayoutAnim() {
  if (layoutAnim) { cancelAnimationFrame(layoutAnim); layoutAnim = null; }
}

function resetLayout() {
  if (!LAYOUT) return;
  cancelLayoutAnim();
  const keys = Object.keys(LAYOUT.home);
  const jump = () => keys.forEach(kw => moveNode(kw, ...LAYOUT.home[kw]));
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { jump(); return; }

  const from = {};
  keys.forEach(kw => { const p = LAYOUT.pos[kw]; if (p) from[kw] = [p[0], p[1]]; });
  const t0 = performance.now(), ms = 320;
  const step = now => {
    const p = Math.min(1, (now - t0) / ms), k = 1 - Math.pow(1 - p, 3);   // ease-out
    keys.forEach(kw => {
      const a = from[kw], b = LAYOUT.home[kw];
      if (!a) return;
      moveNode(kw, a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k);
    });
    layoutAnim = p < 1 ? requestAnimationFrame(step) : null;
  };
  layoutAnim = requestAnimationFrame(step);
}
```

되돌아가는 도중에 사용자가 노드를 잡으면 보간을 끊는다 — 안 그러면 손과 보간이
서로 좌표를 밀어 노드가 떤다:

```js
/* target — app.js:767-769 의 pointerdown 핸들러 앞부분 */
MAP.addEventListener('pointerdown', e => {
  if (!VIEW || e.button) return;
  cancelLayoutAnim();                       // 되돌아가는 중이면 끊는다
  const r = MAP.getBoundingClientRect();
```

> 004도 같은 자리에 `cancelZoom();`을 넣는다. 둘 다 할 경우 두 줄이 나란히 온다.

## Repo conventions to follow

- `LAYOUT`은 `pos`(현재 좌표)·`home`(원래 좌표)·`el`·`lab`·`inc`를 들고 있는 전역이다
  (`app.js:680-691`, `658-660` 참고). 새 상태를 만들지 말고 이 구조를 그대로 쓴다.
- **`moveNode`를 수정하지 말 것.** 끌기·되돌리기 양쪽이 같은 함수를 쓰는 게 이 코드의
  구조다. 보간은 호출 쪽에서 한다.
- 주석은 한국어로 '왜'를 적되, 특히 **하지 말아야 할 것과 그 이유**를 적는다.
  `app.js:734-738`(`setPointerCapture를 쓰면 안 된다`)이 이 파일의 표준이다.
- 004가 `cancelZoom`을 같은 패턴(전역 핸들 + cancel 함수)으로 만든다. 이름과 모양을 맞춘다.

## Steps

1. `web/static/app.js:693-696`의 `resetLayout` 함수를 위 Target의 세 조각
   (`layoutAnim` 선언, `cancelLayoutAnim`, `resetLayout`)으로 교체한다.
2. `app.js:768`(`if (!VIEW || e.button) return;`) 다음 줄에 `cancelLayoutAnim();`을 넣는다.
   004를 이미 했다면 `cancelZoom();` 옆에 나란히 둔다.
3. `$('#graph-tools')` 클릭 핸들러(`app.js:783`)의 `resetLayout()` 호출은 **그대로 둔다.**

## Boundaries

- **`moveNode`(`app.js:680-691`)를 수정하지 말 것.**
- **CSS를 건드리지 말 것.** 특히 `.gnode`(`style.css:521`)나 `.glabel`(`style.css:528`)에
  `transition:transform`을 추가하지 말 것 — 위 Problem이 설명한 이유로 더 나빠진다.
- `#graph-svg .gedge`의 기존 전환(`style.css:514-515`: `stroke-opacity`·`stroke-width`·`stroke`)은
  그대로 둔다. 그건 강조 표시용이고 위치와 무관하다.
- 320ms를 늘리지 말 것. 노드가 많으면 길게 느껴진다.
- 되돌아가는 동안 노드를 다시 배치하는(force layout을 다시 도는) 일을 하지 말 것.
  이 계획서는 이미 정해진 `LAYOUT.home` 좌표로 옮기기만 한다.
- 새 의존성 금지.
- 코드가 위 인용과 다르면 **멈추고 보고할 것.**

## Verification

- **기계적**: `node --check web/static/app.js` 가 오류 없이 끝나야 한다.
- **눈으로 확인**: 연관어 탭에서 키워드를 검색해 지도를 띄운다.
  - 노드 서너 개를 서로 다른 방향으로 멀리 끌어 옮긴다.
  - '배치 되돌리기'를 누른다. 모든 노드가 **동시에 제자리로 미끄러져** 돌아가야 한다.
  - **움직이는 내내 선이 점에 붙어 있어야 한다.** 선이 점에서 떨어져 따로 노는 순간이
    한 프레임이라도 보이면 CSS 전환을 잘못 얹은 것이다 — 되돌린다.
  - **라벨도 점을 따라와야 한다.** 라벨만 뒤처지면 `moveNode`를 우회한 것이다.
  - 되돌아가는 **도중에 노드를 잡아 끈다.** 잡은 노드가 손을 정확히 따라와야 하고,
    떨거나 원래 자리로 끌려가면 2번 단계(`cancelLayoutAnim`)가 빠진 것이다.
  - '배치 되돌리기'를 **연타한다.** 매번 현재 위치에서 이어서 돌아가야 한다.
  - 노드를 옮기지 않은 상태에서 눌러 본다. 아무 일도 일어나지 않아야 한다(이미 제자리).
  - DevTools > Performance로 녹화하고, 노드가 많은 3홉 그래프에서도 프레임이
    유지되는지 본다. 떨어지면 `ms`를 늘리지 말고 **보고할 것** — 노드 수에 따라
    다른 접근이 필요하다는 신호다.
  - Rendering > `prefers-reduced-motion: reduce`를 켜고 누른다. **즉시** 돌아가야 한다.
- **Done when**: 점·선·라벨이 한 덩어리로 미끄러지고, 도중에 끌기가 가능하다.
