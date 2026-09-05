# 002 — 기사 서랍에 열기·닫기 모션을 붙인다

- **Status**: TODO
- **Commit**: 81daeb0
- **Severity**: MEDIUM
- **Category**: Missed opportunities (공간 연속성)
- **Estimated scope**: 2 파일 (`web/static/style.css`, `web/static/app.js`), 약 40줄
- **Depends on**: 001 (토큰 `--ease-drawer`, `--ease-out`이 있어야 한다)

## Problem

기사를 클릭하면 오른쪽에서 서랍이 열린다. 이 앱에서 기사를 읽는 주된 경로인데,
지금은 `hidden` 속성만 껐다 켰다 해서 **화면 폭의 절반짜리 패널이 순간이동한다.**
어디서 왔는지, 닫으면 어디로 가는지에 대한 단서가 없다.

```js
// web/static/app.js:1145-1152 — 현재
    $('#drawer').hidden = false;
  }
  if (e.target.closest('[data-close]')) $('#drawer').hidden = true;
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { $('#drawer').hidden = true; $('#kwmodal').hidden = true; }
});
```

```css
/* web/static/style.css:199-205 — 현재 */
.drawer{position:fixed;inset:0;z-index:90}
.drawer-bg{position:absolute;inset:0;background:rgba(15,23,42,.4)}
.drawer-panel{position:absolute;right:0;top:0;bottom:0;width:min(600px,94vw);
  background:var(--card);padding:26px;overflow-y:auto;box-shadow:-6px 0 24px rgba(15,23,42,.12)}
.drawer-x{position:absolute;right:16px;top:14px;background:var(--bg);color:var(--mut);
  border-radius:6px;width:32px;height:32px;padding:0;font-size:16.5px}
.drawer-x:hover{background:var(--line)}
```

### ★ 반드시 알아야 할 제약

`style.css:31`에 이런 규칙이 있고, **이건 걷어내면 안 된다**:

```css
/* web/static/style.css:27-31 — 건드리지 말 것 */
/* ★ hidden 속성이 반드시 이기게 한다.
   브라우저 기본 스타일시트의 [hidden]{display:none}은 **작성자 규칙에 진다.**
   그래서 .modal{display:flex} 같은 걸 쓰면 hidden=true로 바꿔도 계속 보인다
   (실측: 급상승 팝업이 뜬 뒤 닫히지 않았다. .graph-tools도 같은 상태였다).
   JS에서 el.hidden으로 여닫는 요소가 여럿이라 규칙 하나로 못박아 둔다. */
[hidden]{display:none !important}
```

`display:none`인 요소는 전환이 돌지 않으므로, `hidden`만으로는 **나가는 모션을
만들 수 없다.** 그렇다고 이 규칙을 지우면 위 주석이 말하는 실측 버그가 되살아난다.
그래서 해법은 `hidden`을 "완전히 없는 상태"로 남겨 두고, 보이는 동안의 상태를
`.in` 클래스로 따로 표현하는 것이다.

## Target

### CSS

```css
/* target — style.css:199-205 를 아래로 교체 */
.drawer{position:fixed;inset:0;z-index:90}
/* ★ 서랍은 hidden(=display:none)으로 완전히 치운다. display:none에는 전환이
   걸리지 않으므로, 보이는 동안의 상태만 .in으로 따로 표시한다.
   기본 규칙에 적힌 시간(280ms)이 **닫힐 때** 쓰이고, .in이 붙는 순간의
   시간(380ms)이 **열릴 때** 쓰인다 — 나갈 때 더 빠른 게 맞다. */
.drawer-bg{position:absolute;inset:0;background:rgba(15,23,42,.4);
  opacity:0;transition:opacity 280ms var(--ease-drawer)}
.drawer-panel{position:absolute;right:0;top:0;bottom:0;width:min(600px,94vw);
  background:var(--card);padding:26px;overflow-y:auto;box-shadow:-6px 0 24px rgba(15,23,42,.12);
  transform:translateX(100%);transition:transform 280ms var(--ease-drawer)}
.drawer.in .drawer-bg{opacity:1;transition-duration:380ms}
.drawer.in .drawer-panel{transform:none;transition-duration:380ms}
.drawer-x{position:absolute;right:16px;top:14px;background:var(--bg);color:var(--mut);
  border-radius:6px;width:32px;height:32px;padding:0;font-size:16.5px}
.drawer-x:hover{background:var(--line)}
```

`translateX(100%)`는 **패널 자신의 폭**을 뜻한다. `width:min(600px,94vw)`라 폭이
화면에 따라 달라지므로 픽셀값을 박으면 안 된다.

### 감쇠 (001이 만든 블록 안에 추가)

```css
/* target — style.css 맨 끝 @media (prefers-reduced-motion: reduce) 블록 안 */
  /* 서랍: 옆에서 밀려오는 움직임은 빼고 밝기만 남긴다.
     ★ 시간을 0으로 두지 않는다 — 그러면 아래 JS의 transitionend가 안 떠서
       닫기가 setTimeout에만 의존하게 된다. */
  .drawer-panel{transform:none;opacity:0;
    transition:opacity 120ms var(--ease-out)}
  .drawer.in .drawer-panel{opacity:1;transition-duration:120ms}
  .drawer-bg,.drawer.in .drawer-bg{transition-duration:120ms}
```

### JS

```js
/* target — app.js 안, 서랍 클릭 핸들러보다 위에 함수 두 개를 새로 만든다 */

/* 서랍 여닫기.
   ★ hidden=false 직후에 바로 .in을 붙이면 브라우저가 두 상태를 한 번에 계산해서
     전환이 통째로 생략된다. 사이에 한 번 강제로 레이아웃을 읽어 끊어 준다. */
let drawerClose = null;             // 닫는 중이면 {p, done, t}

function openDrawer() {
  const d = $('#drawer');
  /* ★ 닫는 중이었으면 그 예약을 먼저 거둔다.
     안 거두면 다시 연 뒤에 남아 있던 setTimeout·transitionend가 발동해서
     활짝 열린 서랍이 이유 없이 사라진다 (닫고 420ms 안에 다시 열면 재현). */
  if (drawerClose) {
    clearTimeout(drawerClose.t);
    drawerClose.p.removeEventListener('transitionend', drawerClose.done);
    drawerClose = null;
  }
  d.hidden = false;
  void d.offsetWidth;               // 강제 리플로우 — 이 줄이 없으면 안 움직인다
  d.classList.add('in');
}

function closeDrawer() {
  const d = $('#drawer');
  if (d.hidden || drawerClose) return;
  d.classList.remove('in');
  const p = d.querySelector('.drawer-panel');
  // 전환이 끝나야 display:none으로 치운다. 감쇠 설정 등으로 transitionend가
  // 안 뜨는 경우를 대비해 시간 제한도 같이 건다.
  const done = e => {
    /* ★ transitionend는 버블링한다. 서랍 안에는 전환이 걸린 자식이 있다
       (.drawer-x는 style.css:147의 button 규칙에 걸려 background가 전환된다).
       그것까지 받으면 엉뚱한 때 닫힌다. 패널 자신의 전환만 센다. */
    if (e && e.target !== p) return;
    clearTimeout(drawerClose.t);
    p.removeEventListener('transitionend', done);
    drawerClose = null;
    d.hidden = true;
  };
  drawerClose = { p, done, t: setTimeout(done, 420) };
  p.addEventListener('transitionend', done);
}
```

호출부 세 곳을 바꾼다:

```js
/* target — app.js:1145 */
    openDrawer();
  }
  if (e.target.closest('[data-close]')) closeDrawer();
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { closeDrawer(); $('#kwmodal').hidden = true; }
});
```

## Repo conventions to follow

- `$()`는 이 파일이 이미 쓰는 셀렉터 헬퍼다(`app.js` 상단 정의). `document.querySelector`를
  새로 쓰지 말고 `$()`를 쓴다.
- 함수 위 주석은 **한국어로 '왜'를 적는다.** 예시: `app.js:734-738`의
  `setPointerCapture를 쓰면 안 된다` 주석이 이 파일의 표준 톤이다.
- 이벤트는 `document.body`에 위임해서 붙인다(`app.js:1112` 참고). 새 리스너를 개별
  요소에 흩뿌리지 않는다.

## Steps

1. `web/static/style.css:199-205`를 위 Target의 CSS 블록으로 교체한다.
   `.drawer-x`·`.drawer-x:hover` 두 줄은 그대로 유지된다.
2. `style.css` 맨 끝 `@media (prefers-reduced-motion: reduce)` 블록(001이 만든 것) 안에
   위 감쇠 규칙을 추가한다.
3. `web/static/app.js`에서 서랍 클릭 핸들러(`document.body.addEventListener('click', async e => {`,
   1112행 근처)보다 **위쪽**에 `openDrawer`·`closeDrawer` 두 함수를 추가한다.
4. `app.js:1145`의 `$('#drawer').hidden = false;`를 `openDrawer();`로 바꾼다.
5. `app.js:1147`의 `if (e.target.closest('[data-close]')) $('#drawer').hidden = true;`를
   `if (e.target.closest('[data-close]')) closeDrawer();`로 바꾼다.
6. `app.js:1150`의 Escape 핸들러에서 `$('#drawer').hidden = true;`를 `closeDrawer();`로
   바꾼다. **같은 줄의 `$('#kwmodal').hidden = true;`는 그대로 둔다** — 팝업은 003의 몫이다.

## Boundaries

- **`style.css:31`의 `[hidden]{display:none !important}`를 지우거나 약화시키지 말 것.**
  실측 버그가 되살아난다. 위 Problem의 제약 설명을 다시 읽을 것.
- 서랍 **안쪽 내용**(`app.js:1132-1144`의 `$('#drawer-body').innerHTML = ...`)은
  건드리지 않는다. 마크업·구조 변경 없이 모션 속성만 다룬다.
- 팝업(`#kwmodal`)은 이 계획서의 범위가 아니다. 003이 같은 패턴으로 처리한다.
- `z-index:90`을 바꾸지 말 것. `style.css:196-198`에 *서랍은 팝업(70)보다 위여야 한다*는
  실측 근거가 적혀 있다.
- 새 의존성 금지.
- 코드가 위 인용과 다르면 **멈추고 보고할 것.**

## Verification

- **기계적**: `node --check web/static/app.js` 가 오류 없이 끝나야 한다.
- **눈으로 확인**: 앱을 띄우고(`python -m uvicorn web.server:app --reload`) 홈에서
  기사 제목을 클릭한다.
  - 서랍이 **오른쪽 화면 밖에서 미끄러져 들어와야** 한다. 그 자리에서 흐려지며
    나타나면 `transform:translateX(100%)`가 안 걸린 것이다.
  - 닫을 때 **들어온 쪽과 같은 방향(오른쪽)으로** 나가야 한다.
  - 닫는 게 여는 것보다 눈에 띄게 빨라야 한다(280ms vs 380ms).
  - 닫힌 뒤 배경의 기사 목록이 클릭되는지 확인한다 — `hidden=true`가 제때 걸리지
    않으면 투명한 층이 남아 클릭을 먹는다. **이 확인을 빠뜨리지 말 것.**
  - 서랍을 열고 **곧바로 Esc**, 다시 열기를 빠르게 반복한다. 중간에 끊겨도 현재
    위치에서 자연스럽게 되돌아가야 한다(전환은 재타겟된다). 매번 화면 밖에서
    다시 시작하면 잘못 구현된 것이다.
  - **★ 반드시 할 것**: 서랍을 닫고 **0.5초 안에** 다른 기사 제목을 클릭해 다시 연다.
    그 뒤 **1초를 그대로 기다린다.** 서랍이 열린 채로 남아 있어야 한다.
    잠시 뒤 저절로 사라지면 `openDrawer`가 이전 닫기 예약(`drawerClose`)을
    거두지 않은 것이다. 이 검사를 빠뜨리면 결함이 그대로 나간다.
  - 서랍이 **닫히는 동안** ✕ 버튼 위에 마우스를 올렸다 뗀다. 나가는 모션이
    중간에 잘리면 `transitionend`의 `e.target !== p` 검사가 빠진 것이다
    (`.drawer-x`는 `style.css:147`의 `button` 규칙 때문에 background가 전환된다).
  - DevTools > Animations 패널에서 재생 속도를 10%로 낮추고, 배경 어두워짐과 패널
    미끄러짐이 **동시에 시작해 동시에 끝나는지** 본다. 어긋나면 두 규칙의 시간이 다른 것이다.
  - DevTools > Rendering > `prefers-reduced-motion: reduce`를 켜고 다시 연다.
    옆으로 미는 움직임은 사라지고 **흐려졌다 나타나는 변화는 남아야** 한다.
    아무 변화도 없이 툭 나타나면 감쇠를 과하게 적용한 것이다.
- **Done when**: 위 7가지가 모두 만족되고, 서랍을 열고 닫은 뒤 배경 클릭이 정상이다.
