# SAB Trend 디자인 미리보기

shadcn/ui dashboard-01의 inset sidebar 구성을 바탕으로 만든 React 미리보기입니다.
공식 shadcn CLI로 추가한 컴포넌트와 SUIT Variable을 사용합니다.

## 실행

저장소 루트에서 API 서버를 실행합니다.

```powershell
python -m uvicorn web.server:app --host 127.0.0.1 --port 8023 --lifespan off
```

별도 터미널에서:

```powershell
cd frontend
npm.cmd ci
npm.cmd run dev
```

http://127.0.0.1:5173/preview/ 에서 확인합니다.

## 빌드

```powershell
npm.cmd run build
```

빌드 후 API 서버를 재시작하면 http://127.0.0.1:8023/preview/ 에서도 확인할 수 있습니다.
기존 `/` 화면은 변경되지 않습니다. Railway 배포 설정은 변경하지 않았습니다.
OneDrive 파일 잠금으로 인한 빌드 중단을 피하기 위해 기존 해시 자산은 자동 삭제하지 않습니다.

## 범위

- 실제 API의 주간·월간 리뷰, 기간 선택, 근거 기사, 사업 추천·과제 후보
- 월간보고서·Markdown, 기간 비교·주제별 비중·일별 기사 수
- 기사 검색·분야/기간 필터·CSV·상세 패널, 시간 흐름 오름/내림차순
- 연관 네트워크의 범위·확대·이동·노드 선택, 관심 주제·교차·기관·급상승 탐색
- 법령 전체 기간 기본 조회·페이지 이동·AI 요약·업무 적용점·부처/시행일
- 보관함 메모·주제 편집, 과제 검토·이력, 선택 자료 HTML/Markdown·백업·복원
- 라이트/다크 모드 및 선택 저장, 내부 화면에 테마 동기화
- 기존 `workspace-store.js`와 `sab-research-v1` 저장 형식 재사용
- 모델 API 호출 없음, 공유 DB 쓰기 없음

브라우저 보관함은 출처(origin)별로 분리됩니다. 5173 포트의 미리보기와
8023 포트 및 운영 사이트의 보관함은 서로 다릅니다. 같은 8023 포트의
기존 화면과 빌드된 미리보기는 보관함을 공유합니다.
탐색과 보관함은 검증된 기존 화면을 `/legacy-preview?previewEmbed=1`의
동일 출처 iframe으로 재사용합니다. `preview-bridge.js/css`는 기존 메뉴를 감추고
새 디자인과 테마를 적용합니다. 독립 실행하는 기존 `/`에는 영향을 주지 않습니다.
iframe 메시지는 발신 origin과 window를 검증하며, 테마 전환은 iframe을 다시 로드하지 않습니다.
개인 데이터 형식과 기존 백업 파일은 그대로 유지됩니다.

참고: https://ui.shadcn.com/blocks · https://github.com/sun-typeface/SUIT
