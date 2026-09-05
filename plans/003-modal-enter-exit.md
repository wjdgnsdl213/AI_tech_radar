# 003 — 급상승 키워드 팝업에 열기·닫기 모션을 붙인다

- **Status**: TODO
- **Commit**: 81daeb0
- **Severity**: MEDIUM
- **Category**: Missed opportunities (갑작스러운 변화 방지)
- **Estimated scope**: 2 파일 (`web/static/style.css`, `web/static/app.js`), 약 35줄
- **Depends on**: 001 (토큰 `--ease-out`), 002 (같은 `.in` 패턴 — 002를 먼저 하면 그대로 따라 하면 된다)

## Problem

급상승 키워드를 클릭하면 뜨는 팝업이 즉시 나타나고 즉시 사라진다.
차트·연관어·기사 목록이 든 큰 면적이라 튀는 정도가 크다.

```js
// web/static/app.js:1051-1053 — 현재
async function showKeywordModal(kw) {
  MKW = kw;
  $('#kwmodal').hidden = false;
```

```js
// web/static/app.js:1107 — 현재
  if (e.target.closest('[data-mclose]')) $('#kwmodal').hidden = true;
```

```js
// web/static/app.js:1149-1151 — 현재
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { $('#drawer').hidden = true; $('#kwmodal').hidden = true; }
});
```

```css
/* web/static/style.css:185-189 — 현재 */
.modal{position:fixed;inset:0;z-index:70;display:flex;align-items:center;justify-content:center}
.modal-bg{position:absolute;inset:0;background:rgba(15,23,42,.45)}
.modal-box{position:relative;background:var(--card);border-radius:12px;
  width:min(1000px,94vw);max-height:88vh;overflow-y:auto;padding:24px;
  box-shadow:0 12px 40px rgba(15,23,42,.22)}
```

002와 같은 제약이 걸린다: `style.css:31`의 `[hidden]{display:none !important}` 때문에
`hidden`만으로는 나가는 모션을 만들 수 없고, 그 규칙은 실측 버그 대응이라
**지우면 안 된다**(자세한 근거는 `plans/002-drawer-enter-exit.md`의 Problem 참고).

## Target

### CSS

```css
/* target — style.css:185-189 를 아래로 교체 */
.modal{position:fixed;inset:0;z-index:70;display:flex;align-items:center;justify-content:center}
/* ★ 서랍(.drawer)과 같은 방식이다 — hidden으로 치우고, 보이는 동안만 .in을 붙인다.
   기본 규칙의 150ms가 닫힐 때, .in의 200ms가 열릴 때 쓰인다. */
.modal-bg{position:absolute;inset:0;background:rgba(15,23,42,.45);
  opacity:0;transition:opacity 150ms var(--ease-out)}
.modal-box{position:relative;background:var(--card);border-radius:12px;
  width:min(1000px,94vw);max-height:88vh;overflow-y:auto;padding:24px;
  box-shadow:0 12px 40px rgba(15,23,42,.22);
  opacity:0;transform:scale(.97);
  transition:opacity 150ms var(--ease-out),transform 150ms var(--ease-out)}
.modal.in .modal-bg{opacity:1;transition-duration:200ms}
.modal.in .modal-box{opacity:1;transform:none;transition-duration:200ms}
```

`scale(.97)`이다 — **`scale(0)`을 쓰지 말 것.** 아무것도 없던 자리에서 생겨나는 건
현실에 없는 움직임이라 가짜처럼 보인다.

`transform-origin`은 **건드리지 않는다.** 팝업은 화면 가운데 뜨는 것이므로 기본값
(가운데)이 맞다. 트리거 위치에서 자라나게 만들면 안 된다 — 그건 드롭다운·팝오버의 규칙이다.

### 감쇠 (001이 만든 블록 안에 추가)

```css
/* target — style.css 맨 끝 @media (prefers-reduced-motion: reduce) 블록 안 */
  /* 팝업: 커지는 움직임은 빼고 밝기만 남긴다 */
  .modal-box,.modal.in .modal-box{transform:none;transition:opacity 120ms var(--ease-out)}
  .modal-bg,.modal.in .modal-bg{transition-duration:120ms}
```

### JS

```js
/* target — app.js 안, showKeywordModal 바로 위에 함수 두 개를 새로 만든다 */

/* 팝업 여닫기 — 서랍(openDrawer/closeDrawer)과 같은 방식이다.
   ★ hidden=false 직후 바로 .in을 붙이면 두 상태가 한 번에 계산돼 전환이 생략된다.
     사이에 강제 리플로우를 한 번 끼워 끊어 준다. */
let modalClose = null;              // 닫는 중이면 {b, done, t}

function openModal() {
  const m = $('#kwmodal');
  /* ★ 닫는 중이었으면 그 예약을 먼저 거둔다.
     안 거두면 다시 연 뒤에 남아 있던 setTimeout·transitionend가 발동해서
     열린 팝업이 이유 없이 사라진다 (닫고 260ms 안에 다른 키워드를 누르면 재현). */
  if (modalClose) {
    clearTimeout(modalClose.t);
    modalClose.b.removeEventListener('transitionend', modalClose.done);
    modalClose = null;
  }
  m.hidden = false;
  void m.offsetWidth;
  m.classList.add('in');
}

function closeModal() {
  const m = $('#kwmodal');
  if (m.hidden || modalClose) return;
  m.classList.remove('in');
  const b = m.querySelector('.modal-box');
  const done = e => {
    /* ★ transitionend는 버블링한다. 팝업 안에는 전환이 걸린 자식이 있다
       (.spark·.preset 등, style.css:147의 button 규칙). 패널 자신의 것만 센다.
       .modal-box는 opacity·transform 둘 다 전환하므로 두 번 뜨는데,
       아래에서 리스너를 바로 떼므로 두 번째는 무시된다. */
    if (e && e.target !== b) return;
    clearTimeout(modalClose.t);
    b.removeEventListener('transitionend', done);
    modalClose = null;
    m.hidden = true;
  };
  modalClose = { b, done, t: setTimeout(done, 260) };
  b.addEventListener('transitionend', done);
}
```

호출부 세 곳:

```js
/* target — app.js:1051-1053 */
async function showKeywordModal(kw) {
  MKW = kw;
  openModal();
```

```js
/* target — app.js:1107 */
  if (e.target.closest('[data-mclose]')) closeModal();
```

```js
/* target — app.js:1149-1151 */
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { closeDrawer(); closeModal(); }
});
```

> `closeDrawer()`는 002가 만든다. 002를 아직 안 했다면 그 자리는
> `$('#drawer').hidden = true;` 그대로 두고 `closeModal()`만 바꾼다.

## Repo conventions to follow

- `$()` 헬퍼를 쓴다(`app.js` 상단 정의). `document.querySelector`를 새로 쓰지 않는다.
- `.modal-box`에는 `transitionend`가 **두 번** 뜬다(opacity·transform). `done` 안에서
  리스너를 바로 떼므로 두 번째는 무시된다 — 위 코드가 이미 그렇게 돼 있다.
- 주석은 한국어로 '왜'를 적는다.

## Steps

1. `web/static/style.css:185-189`를 위 Target의 CSS 블록으로 교체한다.
   바로 아래 `.modal-grid`(190행)부터는 그대로 둔다.
2. `style.css` 맨 끝 감쇠 블록 안에 위 감쇠 규칙을 추가한다.
3. `web/static/app.js`의 `showKeywordModal` 정의(1051행) **바로 위**에
   `openModal`·`closeModal` 두 함수를 추가한다.
4. `app.js:1053`의 `$('#kwmodal').hidden = false;`를 `openModal();`로 바꾼다.
5. `app.js:1107`의 `$('#kwmodal').hidden = true;`를 `closeModal();`로 바꾼다.
6. `app.js:1150`의 Escape 핸들러에서 `$('#kwmodal').hidden = true;`를 `closeModal();`로 바꾼다.

## Boundaries

- **`style.css:31`의 `[hidden]{display:none !important}`를 건드리지 말 것.**
- **`transform-origin`을 추가하지 말 것.** 가운데 뜨는 팝업은 기본값이 맞다.
- `scale(0)`을 쓰지 말 것. `.97`이다.
- 팝업 **안쪽**(`#m-chart`·`#m-net`·`#m-foot`의 내용, `app.js:1056-1098`)은 건드리지
  않는다. 특히 차트가 그려지는 방식은 이 계획서와 무관하다.
- `z-index:70`을 바꾸지 말 것(`style.css:196-198`의 실측 근거).
- 서랍은 002의 몫이다.
- 새 의존성 금지.
- 코드가 위 인용과 다르면 **멈추고 보고할 것.**

## Verification

- **기계적**: `node --check web/static/app.js` 가 오류 없이 끝나야 한다.
- **눈으로 확인**: 앱을 띄우고 홈 > 급상승 키워드 카드에서 키워드를 클릭한다.
  - 팝업이 **아주 살짝 커지면서** 나타나야 한다. 확 부풀거나 점에서 자라나면
    `scale` 값이 틀린 것이다(`.97`이어야 한다).
  - 화면 **가운데**에서 자라야 한다. 클릭한 키워드 쪽에서 자라나면 `transform-origin`을
    잘못 넣은 것이다.
  - 배경 어두워짐과 상자 나타남이 동시에 시작해야 한다.
  - ✕ 또는 배경 클릭, Esc — **세 경로 모두** 같은 모션으로 닫히는지 확인한다.
  - 닫힌 뒤 뒤쪽 표가 클릭되는지 확인한다. 투명한 층이 남으면 `hidden=true`가 안 걸린 것이다.
  - 팝업에서 기사 제목을 눌러 **서랍이 팝업 위로 뜨는지** 확인한다(`style.css:196-198`이
    말하는 실측 사례). 이 계획서가 그 층 순서를 깨지 않았는지 보는 확인이다.
  - DevTools > Animations에서 10% 속도로 재생하고, 상자가 커지는 동안 **글자가 흐릿하게
    번지지 않는지** 본다. `.97`은 충분히 작아서 번지지 않아야 정상이다.
  - Rendering > `prefers-reduced-motion: reduce`를 켜고 다시 연다. 커지는 움직임은
    사라지고 흐려졌다 나타나는 변화만 남아야 한다.
- **Done when**: 위 확인이 모두 통과하고, 세 가지 닫기 경로가 동일하게 동작한다.
