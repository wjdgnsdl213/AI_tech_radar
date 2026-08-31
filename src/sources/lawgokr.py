"""법제처 국가법령정보 OPEN API 어댑터 — 규제 알림(F7)의 1차 출처.

★ 왜 이 소스인가
  F7은 "우리 일에 영향을 주는 규제가 **확정되기 전에** 안다"가 목적이다.
  뉴스로 알면 이미 늦거나, 보도되지 않고 지나가는 고시가 대부분이다.
  그래서 판정을 소스 기반으로 뒀다 — 여기서 온 항목은 제목과 무관하게
  전부 규제로 표시된다(collect.py의 _stamp_regulatory).

★ 왜 개인정보위를 직접 훑지 않는가
  훑을 필요가 없다. 개인정보위 고시·훈령은 전부 이 API의 admrul에 들어 있다.
  1차 출처를 한 곳에서 정식 API로 받는 쪽이 게시판을 긁는 것보다 낫다
  (개인정보위 robots.txt는 Googlebot만 지정하고 있어 우리에겐 적용되지 않지만,
   그렇다고 긁을 이유가 없다는 뜻이다).

★ 인증
  OC는 법제처에 등록한 이메일의 아이디 부분이다. open.law.go.kr에서 무료·즉시
  발급된다. 없으면 LAW_GO_KR_OC 미설정 상태로 도는데, 그때는 공용값을 쓰므로
  **정식 발급을 권한다** — 남의 계정으로 호출하는 셈이고 언제 막혀도 이상하지 않다.

★ 본문은 받지 않는다
  base.Item 규약대로 요약만 저장한다. 규제 항목은 메타데이터 자체가 정보다
  ("개인정보보호위원회가 …고시를 일부개정, 8/20 발령·시행"). 본문까지 받으려면
  lawService.do를 항목마다 한 번씩 더 불러야 하는데, 그만한 값을 못 한다.
"""

from __future__ import annotations

import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import requests

from src.sources.base import Batch, Item, Source

API = "http://www.law.go.kr/DRF/lawSearch.do"
PAGE_SIZE = 100          # API 상한

# target별로 필드 이름이 다르다. 하나로 다루기 위한 대응표.
#   (일련번호 필드, 이름 필드, 날짜 필드, 사람이 볼 URL 틀, 한글 이름)
TARGETS: dict[str, tuple[str, str, str, str, str]] = {
    "admrul": ("행정규칙일련번호", "행정규칙명", "발령일자",
               "https://www.law.go.kr/admRulInfoP.do?admRulSeq={}", "행정규칙"),
    "law":    ("법령일련번호", "법령명한글", "공포일자",
               "https://www.law.go.kr/lsInfoP.do?lsiSeq={}", "법령"),
}


def _iso(yyyymmdd: str) -> str:
    """'20260820' → ISO8601. 법령 API는 시각을 주지 않으므로 자정으로 둔다."""
    s = (yyyymmdd or "").strip()
    if len(s) != 8 or not s.isdigit():
        return ""
    return datetime(int(s[:4]), int(s[4:6]), int(s[6:]),
                    tzinfo=timezone.utc).isoformat()


class LawGoKrSource(Source):
    """target×query 조합을 하나의 작업 단위로 훑는다.

    검색어마다 결과 집합이 완전히 다르므로 HackerNewsSource와 같은 방식으로
    tasks()를 쪼갠다 — 하나가 실패해도 나머지 검색어는 계속 돈다.
    """

    name = "law_go_kr"

    def __init__(self, cfg: dict[str, Any], common: dict[str, Any] | None = None) -> None:
        super().__init__(cfg, common)
        self.oc = os.getenv("LAW_GO_KR_OC") or cfg.get("oc") or "test"
        self.queries: list[str] = list(cfg.get("queries") or [])
        self.targets: list[str] = [t for t in (cfg.get("targets") or ["admrul", "law"])
                                   if t in TARGETS]
        self.interval = float(cfg.get("request_interval", 0.4))
        self.timeout = float(cfg.get("timeout", 25))
        self.task = ""

    # ── 작업 분할 ────────────────────────────────────────────────
    def tasks(self) -> list[str]:
        return [f"{t}|{q}" for t in self.targets for q in self.queries] or [""]

    def with_task(self, task: str) -> "LawGoKrSource":
        s = LawGoKrSource(self.cfg, self.common)
        s.task = task
        return s

    # ── 수집 ─────────────────────────────────────────────────────
    def fetch(self, since: datetime, until: datetime, cursor: str | None) -> Batch:
        target, _, query = (self.task or "admrul|").partition("|")
        target = target if target in TARGETS else "admrul"
        page = int(cursor) if cursor else 1
        seq_f, name_f, date_f, url_t, kind = TARGETS[target]

        root = self._call(target, query, page)
        rows = [c for c in root if len(list(c)) > 0]
        if not rows:
            return Batch([], None, f"{kind}/{query} 끝")

        items: list[Item] = []
        oldest = ""
        for r in rows:
            f = {c.tag: (c.text or "").strip() for c in r}
            ymd = f.get(date_f, "")
            iso = _iso(ymd)
            if not iso:
                continue
            oldest = ymd
            pub = datetime.fromisoformat(iso)
            if pub > until:
                continue
            if pub < since:
                continue
            seq = f.get(seq_f, "")
            dept = f.get("소관부처명", "")
            sub = f.get("행정규칙종류") or f.get("법령구분명") or kind
            rev = f.get("제개정구분명", "")
            eff = f.get("시행일자", "")
            # 본문이 없으므로 메타데이터로 요약을 만든다 — 규제 항목은 이게 정보다
            summary = " · ".join(x for x in [
                dept, sub, rev,
                f"발령 {ymd[:4]}-{ymd[4:6]}-{ymd[6:]}" if len(ymd) == 8 else "",
                f"시행 {eff[:4]}-{eff[4:6]}-{eff[6:]}" if len(eff) == 8 else "",
            ] if x)
            items.append(Item(
                source=self.name,
                source_id=f"{target}:{seq}",
                title=f.get(name_f, ""),
                summary=summary,
                url=url_t.format(seq),
                published_at=iso,
                meta={"target": target, "부처": dept, "종류": sub,
                      "제개정": rev, "시행일자": eff, "검색어": query},
            ))

        # 최신순이라 마지막 행이 since보다 오래됐으면 더 볼 게 없다
        done = (len(rows) < PAGE_SIZE
                or (_iso(oldest) and datetime.fromisoformat(_iso(oldest)) < since))
        return Batch(items, None if done else str(page + 1),
                     f"{kind}/{query} p{page} {len(items)}건")

    # ── HTTP ─────────────────────────────────────────────────────
    def _call(self, target: str, query: str, page: int) -> ET.Element:
        params = {"OC": self.oc, "target": target, "type": "XML",
                  "display": PAGE_SIZE, "page": page, "sort": "ddes"}
        if query:
            params["query"] = query
        ua = self.common.get("user_agent") or "AI-tech-radar/0.1"
        time.sleep(self.interval)
        r = requests.get(f"{API}?{urlencode(params)}",
                         headers={"User-Agent": ua}, timeout=self.timeout)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        # 인증 실패도 200 + XML로 온다 — 조용히 0건이 되지 않게 여기서 잡는다
        if root.tag == "Response" or root.findtext("totalCnt") is None:
            msg = root.findtext("msg") or root.findtext("result") or "알 수 없음"
            hint = ("open.law.go.kr에서 OC를 발급받아 LAW_GO_KR_OC에 넣으세요"
                    if self.oc == "test" else
                    "open.law.go.kr → 마이페이지 → 활용신청 내역에서 **호출 IP를 등록**하세요. "
                    "법제처는 등록된 IP에서만 응답합니다(공인 IP가 바뀌면 다시 등록해야 합니다)")
            raise RuntimeError(f"법제처 API 거부: {msg} (OC={self.oc!r}) — {hint}")
        return root
