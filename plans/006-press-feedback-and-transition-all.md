# 006 — 눌림 반응을 넣고 `transition: all`을 걷어낸다

- **Status**: TODO
- **Commit**: 81daeb0
- **Severity**: MEDIUM (`transition: all`은 HIGH, 눌림 반응은 LOW — 같은 줄이라 묶는다)
- **Category**: Performance / Physicality
- **Estimated scope**: 1 파일 (`web/static/style.css`), 약 15줄
- **Depends on**: 001 (토큰 `--ease-out`)

## Problem

### (a) `transition: all` — 성능 문제

```css
/* web/static/style.css:68-70 — 현재 */
.chip{border:1px solid var(--line);background:var(--card);color:var(--ink-2);
  padding:6px 14px;border-radius:6px;font-size:15px;cursor:pointer;
  font-family:inherit;font-weight:500;transition:.12s}
```

`transition:.12s`는 시간만 적었으므로 `transition-property`가 **`all`**이 된다.
칩에 걸린 모든 속성이 전환 대상이 되고, 그중 `padding`·`border-radius`·`font-size`처럼
레이아웃을 다시 계산해야 하는 것들이 GPU 밖에서 돈다. 지금은 hover가 색만 바꿔서
드러나지 않지만, 아래에서 `transform`을 얹는 순간 의도하지 않은 것까지 함께 움직인다.
칩은 축 필터·기간 필터 등 화면 곳곳에 있다.

### (b) 눌림 반응이 하나도 없다

`:active` 규칙이 **파일 전체에 0건**이다. hover는 26곳인데 눌렀을 때의 반응이 없다.
마우스에서는 hover가 대신해 주지만, **터치 기기에는 hover가 없어서** 누른 순간
아무 일도 일어나지 않는다. 이 앱은 820px 이하 레이아웃을 따로 갖고 있다.

전환이 아예 없어 눌림을 얹을 자리도 없는 규칙들:

```css
/* web/static/style.css:149-155 — 현재 (transition 없음) */
.btn-ghost{border:1px solid var(--line);padding:8px 15px;border-radius:7px;
  color:var(--ink-2);background:#fff;font-weight:500}
.btn-ghost:hover{border-color:var(--blue-line);color:var(--blue);text-decoration:none}
.preset-row{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px}
.preset{background:var(--card);border:1px solid var(--line);color:var(--mut);
  padding:5px 12px;border-radius:6px;font-size:14px;cursor:pointer;font-weight:500}
```

```css
/* web/static/style.css:457-459 — 현재 (transition 없음) */
.pager button{min-width:36px;padding:7px 10px;font-size:14px;border:1px solid var(--line);
  background:var(--card);color:var(--ink-2);border-radius:6px;cursor:pointer;
  font-family:inherit;font-variant-numeric:tabular-nums}
```

```css
/* web/static/style.css:142-144 — 현재 */
button{background:var(--blue);color:#fff;border:0;padding:9px 20px;border-radius:7px;
  cursor:pointer;font-weight:600;transition:background .12s}
button:hover{background:var(--blue-dark)}
```

## Target

### (a) `transition: all` 제거 — 속성을 명시한다

```css
/* target — style.css:70 의 마지막 선언 */
  font-family:inherit;font-weight:500;
  transition:border-color .12s,color .12s,background .12s,transform 120ms var(--ease-out)}
```

### (b) 전환이 없는 규칙에 transform 전환을 붙인다

```css
/* target — style.css:149-150 */
.btn-ghost{border:1px solid var(--line);padding:8px 15px;border-radius:7px;
  color:var(--ink-2);background:#fff;font-weight:500;
  transition:border-color .12s,color .12s,transform 120ms var(--ease-out)}
```

```css
/* target — style.css:153-155 */
.preset{background:var(--card);border:1px solid var(--line);color:var(--mut);
  padding:5px 12px;border-radius:6px;font-size:14px;cursor:pointer;font-weight:500;
  transition:border-color .12s,color .12s,transform 120ms var(--ease-out)}
```

```css
/* target — style.css:457-459 */
.pager button{min-width:36px;padding:7px 10px;font-size:14px;border:1px solid var(--line);
  background:var(--card);color:var(--ink-2);border-radius:6px;cursor:pointer;
  font-family:inherit;font-variant-numeric:tabular-nums;
  transition:border-color .12s,color .12s,transform 120ms var(--ease-out)}
```

```css
/* target — style.css:142-143 */
button{background:var(--blue);color:#fff;border:0;padding:9px 20px;border-radius:7px;
  cursor:pointer;font-weight:600;
  transition:background .12s,transform 120ms var(--ease-out)}
```

```css
/* target — style.css:467-470 (.btn-src) */
.btn-src{display:inline-flex;align-items:center;gap:7px;margin-top:14px;
  background:var(--blue);color:#fff;border-radius:8px;padding:11px 18px;
  font-size:15px;font-weight:700;text-decoration:none;line-height:1;
  box-shadow:0 1px 3px rgba(37,99,235,.28);
  transition:background .12s,transform 120ms var(--ease-out)}
```

### (c) 눌림 규칙 — `button` 규칙 바로 뒤(style.css:144 다음)에 넣는다

```css
/* target — style.css:144(button:hover) 바로 다음 줄에 추가 */
/* 눌림 반응 — 눌렸다는 사실만 알려 주면 된다. 크게 줄이면 장난스러워지고,
   자주 누르는 것에 얹으면 그만큼 굼떠 보인다. .98에서 멈춘다.
   ★ 제외 대상:
     .nav-item·.nav-fold  좌측 메뉴. 하루에도 수십 번 누르는 자리라 반응이 붙으면
                          손해만 본다.
     .linkish·.subtab     글자처럼 생긴 버튼이다. 글자가 줄었다 커지면 읽기 방해다.
     .spark               표 안의 막대다. 읽는 데이터지 누르는 표면이 아니다.
     .drawer-x            서랍 자체가 이미 움직인다(002). 겹쳐 얹지 않는다. */
.chip:active,.preset:active,.btn-ghost:active,.btn-src:active,
button:not(:disabled):not(.nav-item):not(.nav-fold):not(.linkish):not(.subtab):not(.spark):not(.drawer-x):active{
  transform:scale(.98)}
```

### (d) 감쇠 (001이 만든 블록 안에 추가)

```css
/* target — style.css 맨 끝 @media (prefers-reduced-motion: reduce) 블록 안 */
  /* 눌림: 크기 변화는 빼고 색 변화는 남긴다 — 눌렸다는 신호 자체는 필요하다 */
  .chip:active,.preset:active,.btn-ghost:active,.btn-src:active,
  button:active{transform:none}
```

## Repo conventions to follow

- `style.css:145-148`에 *`button` 규칙이 `<button>` 전부에 걸린다*는 경고와 실측 사례
  (홈의 '전체 보기'가 파란 배경에 파란 글자로 사라졌던 건)가 이미 적혀 있다.
  같은 함정을 반복하지 않기 위해 위 (c)는 `:not()`으로 예외를 명시했다. 그 목록을
  임의로 줄이지 말 것.
- 시간은 기존 표기(`.12s`)를 유지하고, 새로 넣는 transform만 `120ms var(--ease-out)`로 쓴다.
  둘은 같은 길이다 — 기존 줄을 통째로 다시 쓰지 않기 위한 것이다.
- 조밀한 한 줄 다중 선언 스타일을 유지한다.

## Steps

1. `style.css:70`의 `transition:.12s`를 (a)의 4개 속성 명시로 바꾼다.
2. `style.css:143`의 `transition:background .12s`에 `,transform 120ms var(--ease-out)`를 덧붙인다.
3. `style.css:150`(`.btn-ghost`)에 (b)의 `transition` 선언을 추가한다.
4. `style.css:155`(`.preset`)에 (b)의 `transition` 선언을 추가한다.
5. `style.css:459`(`.pager button`)에 (b)의 `transition` 선언을 추가한다.
6. `style.css:470`(`.btn-src`)에 (b)의 `transition` 선언을 추가한다.
7. `style.css:144`(`button:hover{...}`) **바로 다음 줄**에 (c)의 주석과 눌림 규칙을 추가한다.
8. `style.css` 맨 끝 감쇠 블록에 (d)를 추가한다.

## Boundaries

- **`.nav-item`·`.nav-fold`에 눌림 반응을 붙이지 말 것.** 핵심 내비게이션이라
  자주 누르는 자리다. `:not()` 목록에서 빼지 말 것.
- **`.linkish`·`.subtab`·`.spark`·`.drawer-x`도 마찬가지다.**
- `padding`·`border-radius`·`font-size`를 전환 대상에 넣지 말 것. (a)를 고치는
  목적 자체가 그것들을 전환에서 빼는 것이다.
- hover 규칙(26곳)의 **색상 값**을 바꾸지 말 것. 이 계획서는 전환 속성만 다룬다.
- `scale` 값은 `.98`이다. `.9` 이하로 내리지 말 것 — 대시보드에 어울리지 않는다.
- `@media (hover: hover)` 게이팅은 **필요 없다.** 이 계획서는 `:active`만 다루고
  `:hover`에 움직임을 넣지 않는다.
- `web/static/app.js`를 건드리지 않는다.
- 새 의존성 금지.
- 코드가 위 인용과 다르면 **멈추고 보고할 것.**

## Verification

- **기계적**: 중괄호 균형 —
  `python -c "import pathlib; s=pathlib.Path('web/static/style.css').read_text(encoding='utf-8'); print(s.count('{')==s.count('}'))"` 가 `True`.
  그리고 `grep -n "transition:\.12s" web/static/style.css` 가 **아무것도 출력하지 않아야** 한다.
- **눈으로 확인**: 앱을 띄운다.
  - 분석 > 급상승 탭의 축 칩('AI·빅데이터')을 누른 채로 있는다. **살짝 작아져야** 한다.
    떼면 돌아온다. 눈에 띄게 쪼그라들면 값이 틀린 것이다.
  - 검색 화면의 파란 '검색' 버튼, 프리셋 칩, '엑셀로 내려받기', 페이지 번호에서도
    같은 반응이 나는지 본다.
  - **좌측 메뉴 항목을 눌러 본다. 아무 크기 변화도 없어야 한다.** 작아지면 `:not()`
    목록이 빠진 것이다.
  - 홈의 '전체 보기'(`.linkish`)도 크기 변화가 없어야 한다. 여기서 파란 배경에
    파란 글자가 되는 회귀가 없는지도 같이 본다(`style.css:145-148`의 실측 사례).
  - 페이지 번호에서 **비활성(`:disabled`) 버튼**을 눌러 본다. 반응이 없어야 한다.
  - DevTools > Animations에서 10% 속도로 칩을 누른다. **크기만** 변하고
    글자 크기나 안쪽 여백이 함께 움직이지 않는지 본다. 함께 움직이면 (a)가 덜 고쳐진 것이다.
  - 터치 기기(또는 DevTools 기기 모드)에서 칩을 탭한다. 눌린 반응이 보여야 한다.
  - Rendering > `prefers-reduced-motion: reduce`를 켠다. 크기 변화는 사라지되
    **색 변화는 남아야** 한다.
- **Done when**: `transition:.12s` grep이 비고, 눌림 반응이 대상 요소에만 나며,
  좌측 메뉴에는 나지 않는다.
