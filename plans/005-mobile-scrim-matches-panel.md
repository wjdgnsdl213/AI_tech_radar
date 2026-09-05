# 005 — 모바일 메뉴의 어두운 배경을 패널과 같이 움직이게 한다

- **Status**: TODO
- **Commit**: 81daeb0
- **Severity**: MEDIUM
- **Category**: Cohesion (한 동작인데 반쪽만 움직인다)
- **Estimated scope**: 1 파일 (`web/static/style.css`), 약 12줄
- **Depends on**: 001 (토큰 `--ease-out`)

## Problem

좁은 화면(820px 이하)에서 ☰를 누르면 사이드바가 왼쪽에서 미끄러져 나온다.
그런데 뒤를 덮는 어두운 배경(`.navscrim`)은 `display:none` ↔ `display:block`이라
**즉시 나타났다 즉시 사라진다.** 한 동작인데 절반은 미끄러지고 절반은 튄다.

```css
/* web/static/style.css:302 — 현재 */
.navscrim{display:none}
```

```css
/* web/static/style.css:397-405 — 현재 */
@media (max-width:820px){
  .sidenav{transform:translateX(-100%);transition:transform .18s;
    box-shadow:0 0 24px rgba(15,23,42,.16);width:var(--nav-w)}
  .sidenav.open{transform:none}
  /* 좁은 화면에서는 메뉴가 겹쳐 뜨므로 좌우 보정이 필요 없다 */
  .wrap,.shell.fold .wrap{margin-left:0;padding:16px 14px 52px}
  .navscrim.open{display:block;position:fixed;inset:0;
    background:rgba(15,23,42,.32);z-index:39}
}
```

JS는 이미 `.open` 클래스를 두 요소에 함께 붙였다 뗀다 — 고칠 게 없다:

```js
// web/static/app.js:97-110 — 현재 (건드리지 않는다)
function closeNav() {
  $('#sidenav').classList.remove('open');
  $('#navscrim').classList.remove('open');
}
...
    $('#sidenav').classList.toggle('open');
    $('#navscrim').classList.toggle('open');
```

`display`는 전환되지 않는다. 그래서 `opacity` + `visibility`로 바꾼다.
`visibility`를 함께 쓰는 이유는, `opacity:0`만으로는 투명한 층이 남아
**뒤쪽 내용의 클릭을 먹기 때문**이다.

## Target

```css
/* target — style.css:302 는 그대로 둔다 */
.navscrim{display:none}
```

```css
/* target — style.css:403-404 의 .navscrim.open 규칙을 아래 두 규칙으로 교체 */
  /* ★ display로 여닫으면 전환이 안 걸린다. 넓은 화면에서는 위(302행)의
     display:none이 그대로 살아 있고, 좁은 화면에서만 자리를 잡아 둔 뒤
     밝기로 여닫는다. visibility를 같이 쓰는 이유는 opacity:0만으로는
     투명한 층이 남아 뒤쪽 클릭을 먹기 때문이다.
     닫힐 때 visibility는 .18s **뒤에** 꺼져야 페이드가 보인다. */
  .navscrim{display:block;position:fixed;inset:0;
    background:rgba(15,23,42,.32);z-index:39;
    opacity:0;visibility:hidden;
    transition:opacity .18s var(--ease-out),visibility 0s .18s}
  .navscrim.open{opacity:1;visibility:visible;
    transition:opacity .18s var(--ease-out),visibility 0s}
```

같은 동작의 나머지 절반인 사이드바에도 같은 곡선을 준다:

```css
/* target — style.css:398 */
  .sidenav{transform:translateX(-100%);transition:transform .18s var(--ease-out);
    box-shadow:0 0 24px rgba(15,23,42,.16);width:var(--nav-w)}
```

### 감쇠 (001이 만든 블록 안에 추가)

```css
/* target — style.css 맨 끝 @media (prefers-reduced-motion: reduce) 블록 안 */
  /* 모바일 메뉴: 화면 밖에 있는 패널이라 위치 변화 자체가 '있고 없음'이다.
     움직임을 뺄 수 없으므로 시간만 줄인다. */
  .sidenav{transition-duration:120ms}
  .navscrim{transition:opacity 120ms var(--ease-out),visibility 0s 120ms}
  .navscrim.open{transition:opacity 120ms var(--ease-out),visibility 0s}
```

## Repo conventions to follow

- 반응형 분기는 `@media (max-width:820px)` 하나로 모여 있다(`style.css:397-405`).
  새 분기를 만들지 말고 그 안에 넣는다. `app.js:92-94`에 *CSS의 820px 분기와 같은 값*이라는
  주석이 있으니 숫자를 바꾸면 JS와 어긋난다.
- 이 프로젝트는 **CSS가 폭에 맞는 표현을 고르고 JS는 상태만 켠다**(`app.js:88-91` 주석).
  그러니 이 수정은 CSS만으로 끝나야 한다.
- 주석은 한국어로 '왜'를 적는다.

## Steps

1. `web/static/style.css`의 `@media (max-width:820px)` 블록(397행)을 찾는다.
2. 398행 `.sidenav{...transition:transform .18s;...}`의 `.18s`를 `.18s var(--ease-out)`로 바꾼다.
   같은 줄의 나머지(`transform:translateX(-100%)`, `box-shadow`, `width`)는 그대로 둔다.
3. 403-404행의 `.navscrim.open{display:block;...}` 규칙을 위 Target의 두 규칙
   (`.navscrim`, `.navscrim.open`)으로 교체한다. 주석도 함께 넣는다.
4. `style.css:302`의 `.navscrim{display:none}`은 **그대로 둔다.** 이게 넓은 화면에서
   스크림을 완전히 없애는 규칙이다.
5. `style.css` 맨 끝 감쇠 블록에 위 감쇠 규칙 3줄을 추가한다.

## Boundaries

- **`web/static/app.js`를 건드리지 말 것.** `closeNav`와 `#nav-fold` 핸들러는 이미
  올바르게 `.open`을 두 요소에 함께 붙였다 뗀다.
- `style.css:302`의 `.navscrim{display:none}`을 지우지 말 것. 지우면 넓은 화면에서도
  스크림이 자리를 차지해 전체 화면 클릭을 먹는다.
- `z-index:39`를 바꾸지 말 것. 사이드바(40)보다 낮아야 메뉴가 위에 뜬다.
- 넓은 화면의 접기 동작(`style.css:267`·`329`의 `width`/`margin-left` 전환)은
  범위 밖이다. `style.css:322-326`에 *`transform`을 쓰면 `position:fixed` 말풍선 기준이
  깨진다*는 실측 근거가 있는 **의도된 결정**이다.
- 새 의존성 금지.
- 코드가 위 인용과 다르면 **멈추고 보고할 것.**

## Verification

- **기계적**: 중괄호 균형 확인 —
  `python -c "import pathlib; s=pathlib.Path('web/static/style.css').read_text(encoding='utf-8'); print(s.count('{')==s.count('}'))"` 가 `True`.
- **눈으로 확인**: DevTools에서 기기 폭을 **820px 이하**(예: iPhone 프리셋)로 맞춘다.
  - ☰를 누른다. 메뉴가 미끄러져 나오는 **동안** 뒷배경도 같이 어두워져야 한다.
    배경이 먼저 툭 깔리고 메뉴가 따라오면 아직 `display` 방식이다.
  - 배경을 눌러 닫는다. 메뉴가 들어가는 **동안** 배경도 같이 밝아져야 한다.
    배경만 먼저 사라지면 `visibility` 지연(`0s .18s`)이 빠진 것이다.
  - **닫힌 뒤 화면 아무 데나 클릭해 본다.** 링크·버튼이 정상 동작해야 한다.
    아무것도 안 눌리면 투명한 스크림이 남은 것이다 — `visibility:hidden`을 확인한다.
  - DevTools > Animations에서 10% 속도로 재생하고, 두 전환이 **같은 시점에 시작해
    같은 시점에 끝나는지** 본다.
  - 기기 폭을 **821px 이상**으로 늘린다. 스크림이 화면에 전혀 없어야 하고,
    ☰는 기둥을 좁히는 접기 동작으로 돌아가야 한다.
  - Rendering > `prefers-reduced-motion: reduce`를 켜고 다시 열고 닫는다.
    빨라지되 **여전히 미끄러져야** 한다(툭 나타나면 과하게 적용한 것이다).
- **Done when**: 좁은 화면에서 메뉴와 배경이 한 동작으로 보이고, 닫은 뒤 클릭이 정상이다.
