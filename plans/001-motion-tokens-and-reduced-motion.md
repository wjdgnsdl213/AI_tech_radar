# 001 — 모션 토큰과 감쇠(reduced-motion) 기반을 깐다

- **Status**: TODO
- **Commit**: 81daeb0
- **Severity**: MEDIUM
- **Category**: Cohesion & tokens / Accessibility
- **Estimated scope**: 1 파일 (`web/static/style.css`), 약 20줄 추가

## Problem

이 프로젝트에는 이징 토큰이 하나도 없다. `:root`에 색·폰트·반경은 정의돼 있는데
곡선과 시간은 없어서, 기존 전환 9곳이 전부 **브라우저 기본 이징**으로 돈다.
기본 이징(`ease`)은 의도한 모션을 표현하기엔 너무 약하다.

```css
/* web/static/style.css:14-26 — 현재 */
:root{
  /* 푸른 계열 — 강조 하나, 나머지는 회청색 무채색 */
  --blue:#2563eb; --blue-dark:#1d4ed8; --blue-soft:#eff6ff; --blue-line:#bfdbfe;
  --cyan:#0891b2;
  --bg:#f7f9fc; --card:#fff; --line:#e5e9f0; --line-soft:#eef2f7;
  --ink:#0f172a; --ink-2:#334155; --mut:#64748b; --mut-2:#94a3b8;
  --red:#dc2626; --amber:#d97706;
  /* 주제 색 — 셋이 한눈에 갈려야 한다 */
  --ax-ai:#2563eb; --ax-bigdata:#0d9488; --ax-smallbiz:#d97706;
  --r:10px;
  --font:"Pretendard Variable",Pretendard,-apple-system,BlinkMacSystemFont,
    "Segoe UI","Malgun Gothic","Apple SD Gothic Neo",sans-serif;
}
```

기본 이징으로 도는 기존 전환들: `style.css:70`, `143`, `267`, `329`, `398`,
`448`, `515`, `523`, `530`.

두 번째 문제는 접근성이다. **`prefers-reduced-motion`이 파일 전체에 0건이다.**
움직임을 줄이도록 설정한 사용자에게도 모든 전환이 그대로 돈다.
`style.css:357`의 `pulse`는 무한 반복인데도 감쇠 처리가 없다.

## Target

토큰 3개를 `:root`에 추가한다. 값은 그대로 복사할 것 — 근사하지 말 것.

```css
/* target — :root 안, --r 바로 위에 넣는다 */
  /* 모션 — 곡선은 여기서만 정의한다. 규칙마다 손으로 적으면 비슷하지만
     다른 곡선이 늘어나서 화면마다 감이 달라진다. */
  --ease-out:cubic-bezier(0.23, 1, 0.32, 1);       /* 들어오고 나가는 것 */
  --ease-in-out:cubic-bezier(0.77, 0, 0.175, 1);   /* 화면 안에서 움직이는 것 */
  --ease-drawer:cubic-bezier(0.32, 0.72, 0, 1);    /* 가장자리 패널 */
```

그리고 파일 맨 끝에 감쇠 블록의 **기반**을 만든다. 이후 계획서(002·003·005·006)가
이 블록 안에 규칙을 덧붙인다.

```css
/* target — style.css 맨 끝에 추가 */

/* ── 움직임 줄이기 ──────────────────────────────────────────────
   OS에서 '동작 줄이기'를 켠 사용자를 위한 블록이다.
   ★ 0으로 만들지 않는다. 전환을 통째로 끄면 무엇이 열리고 닫혔는지 알 수 없어
     오히려 이해가 어려워진다. **위치 변화만 걷어내고 밝기 변화는 남긴다.** */
@media (prefers-reduced-motion: reduce){
  /* 불러오는 중 깜빡임 — 무한 반복이라 가장 거슬린다. 멈추되 자리는 남긴다. */
  .empty.loading{animation:none;opacity:.6}
}
```

## Repo conventions to follow

- 이 프로젝트는 **모든 하이퍼파라미터를 한곳에서 관리**한다(`CLAUDE.md`: "코드
  하드코딩 금지"). CSS도 같은 원칙을 따라 `:root`에 토큰을 두고 규칙은 참조만 한다.
  기존 예시: `style.css:16`의 `--blue`를 `style.css:71`이 `var(--blue)`로 쓴다.
- 주석은 **한국어로, '왜'를 적는다.** 예시: `style.css:27-31`은 `[hidden]` 규칙에
  `!important`를 붙인 이유를 실측 근거와 함께 적어 두었다. 같은 톤을 유지할 것.
- CSS는 한 줄에 여러 선언을 붙여 쓰는 조밀한 스타일이다(`style.css:68-70` 참고).
  새 규칙도 그 스타일에 맞춘다.

## Steps

1. `web/static/style.css`를 연다. `:root` 블록(14-26행) 안, `--r:10px;` 줄 **바로 위**에
   위 Target의 모션 토큰 3줄과 그 위 주석 2줄을 추가한다.
2. 파일 맨 끝으로 가서 위 Target의 `@media (prefers-reduced-motion: reduce)` 블록을
   주석과 함께 추가한다.
3. 끝. 다른 줄은 건드리지 않는다.

## Boundaries

- **기존 전환에 새 토큰을 소급 적용하지 말 것.** `style.css:70`, `143`, `267`, `329`,
  `398`, `448`, `515`, `523`, `530`의 전환은 이 계획서의 범위가 아니다. 그건 별도
  검토(`review-animations`) 대상이고, 특히 `267`·`329`(사이드바)는
  `style.css:322-326`에 *`transform`을 쓰면 `position:fixed` 말풍선 기준이 깨진다*는
  실측 근거가 적혀 있는 **의도된 결정**이라 함부로 바꾸면 회귀한다.
- `web/static/app.js`는 손대지 않는다.
- 새 의존성 금지.
- 아래 계획서(002~007)가 쓸 토큰만 만든다. 여기서 화면이 눈에 띄게 달라지면 안 된다.
- 코드가 위 인용과 다르면(커밋 `81daeb0` 이후 변경) **멈추고 보고할 것.** 임의로 맞추지 말 것.

## Verification

- **기계적**: 빌드 단계가 없는 정적 CSS다. `python -c "import pathlib;
  s=pathlib.Path('web/static/style.css').read_text(encoding='utf-8');
  print(s.count('{')==s.count('}'))"` 가 `True`를 출력하면 중괄호 균형은 맞는다.
- **눈으로 확인**:
  - 브라우저 DevTools에서 `:root`를 선택해 `--ease-out`·`--ease-in-out`·`--ease-drawer`
    세 값이 계산된 스타일에 보이는지 확인한다.
  - **화면이 이전과 똑같이 보여야 한다.** 이 계획서는 아직 아무것도 움직이지 않는다.
    무언가 달라 보이면 소급 적용을 한 것이므로 되돌린다.
  - DevTools > Rendering > `prefers-reduced-motion: reduce`를 켜고, '불러오는 중'
    자리(예: 홈 첫 진입 시 급상승 카드)의 깜빡임이 멈추되 글자는 그대로 보이는지 확인한다.
- **Done when**: 토큰 3개가 `:root`에 있고, 감쇠 블록이 존재하며, 시각적 회귀가 없다.
