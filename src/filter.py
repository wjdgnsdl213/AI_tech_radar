"""②임베딩 필터 — 시드 centroid와의 코사인 유사도로 팀 관련도를 매긴다.

파이프라인에서의 위치:
    수집 → prefilter(무료) → **filter(임베딩, 비쌈)** → score → insight

prefilter가 재현율(놓치지 않기)을 담당했다면 여기는 정밀도 담당이다.
키워드는 '창업자'와 '창업'을 구분하지 못하지만 임베딩은 문맥으로 구분한다.

★ 임베딩 캐시가 이 파일의 핵심 설계다
    bge-m3는 4만 건에 CPU로 몇 시간, GPU로도 분 단위다. 세션이 끊기거나 임계값을
    바꿔 다시 돌릴 때마다 재계산하면 실용성이 없다(sobiz에서 파이프라인이 임베딩
    중간에 죽어 처음부터 다시 돌린 적이 있다).
    → 계산한 벡터를 data/processed/에 저장하고, 다음 실행은 새 항목만 계산한다.
      배치마다 저장하므로 중간에 죽어도 거기까지는 남는다.

    캐시를 radar.db가 아니라 별도 파일에 두는 이유: radar.db는 git으로 관리되는데
    (레포 비공개) 1024차원 float32 × 4만 건 = 약 160MB다. 커밋할 때마다 그만큼
    이력이 불어난다. 캐시는 언제든 재생성 가능하므로 data/processed/(gitignore)에 둔다.

실행:
  python -m src.filter                  # 신규분 임베딩 + 필터 적용
  python -m src.filter --tune           # 임계값 후보별 통과량만 보고 끝 (DB 미반영)
  python -m src.filter --limit 2000     # 일부만 (동작 확인용)
  python -m src.filter --no-dedup       # 소스 간 중복 제거 생략
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

import numpy as np
from sqlalchemy import bindparam, func, select

from src.db import get_engine, item_axes, items, load_config

# Windows 콘솔(cp949)에서 특수문자 출력 깨짐 방지
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

# 대비 점수(양성-부정)는 0 근처에 몰리므로 눈금이 코사인과 다르다
THRESHOLD_GRID = [0.00, 0.02, 0.04, 0.06, 0.08, 0.10, 0.12, 0.15]


def load_seeds(path: str) -> list[str]:
    """시드 문장. '#' 주석과 빈 줄은 무시한다(seed_check.py와 같은 규칙)."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    seeds = [s.strip() for s in lines if s.strip() and not s.strip().startswith("#")]
    if not seeds:
        sys.exit(f"시드 문장이 비어 있습니다: {path}")
    return seeds


def doc_text(title: str | None, summary: str | None) -> str:
    return f"{title or ''} {summary or ''}".strip()


class EmbeddingCache:
    """item_id → 벡터. npz 한 파일에 ids/vectors 두 배열로 담는다.

    모델을 바꾸면 벡터 공간이 달라져 섞으면 안 되므로 파일명에 모델을 박는다.
    """

    def __init__(self, path: Path, dim: int | None = None) -> None:
        self.path = path
        self.ids: np.ndarray = np.empty(0, dtype=np.int64)
        self.vecs: np.ndarray = np.empty((0, dim or 0), dtype=np.float32)
        self.index: dict[int, int] = {}
        # 배치마다 본 배열에 붙이면 vstack이 매번 전체를 복사해 O(n²)가 된다
        # (4만 건에서 실측 500건/초 → 51건/초로 떨어졌다). 여기 모아뒀다가 한 번에 합친다.
        self._pending_ids: list[int] = []
        self._pending_vecs: list[np.ndarray] = []
        if path.exists():
            data = np.load(path)
            self.ids, self.vecs = data["ids"], data["vectors"]
            self.index = {int(i): n for n, i in enumerate(self.ids)}

    def missing(self, wanted: list[int]) -> list[int]:
        return [i for i in wanted if i not in self.index]

    def add(self, new_ids: list[int], new_vecs: np.ndarray) -> None:
        start = len(self.index)
        self._pending_ids.extend(int(i) for i in new_ids)
        self._pending_vecs.append(new_vecs.astype(np.float32, copy=False))
        for n, i in enumerate(new_ids, start=start):
            self.index[int(i)] = n

    def _merge(self) -> None:
        """대기 중인 배치를 본 배열에 한 번에 합친다."""
        if not self._pending_ids:
            return
        self.ids = np.concatenate([self.ids, np.asarray(self._pending_ids, dtype=np.int64)])
        stacked = np.vstack(self._pending_vecs)
        self.vecs = stacked if self.vecs.size == 0 else np.vstack([self.vecs, stacked])
        self._pending_ids.clear()
        self._pending_vecs.clear()

    def save(self) -> None:
        self._merge()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 임시 파일에 쓰고 교체 — 저장 중 죽어도 기존 캐시가 살아남는다
        tmp = self.path.with_suffix(".tmp.npz")
        np.savez(tmp, ids=self.ids, vectors=self.vecs)
        tmp.replace(self.path)

    def matrix(self, wanted: list[int]) -> np.ndarray:
        self._merge()
        return self.vecs[[self.index[i] for i in wanted]]


def near_duplicate_mask(vecs: np.ndarray, threshold: float,
                        chunk: int = 1000) -> np.ndarray:
    """near-duplicate 제거 마스크 (True=남긴다). 입력은 상위 우선 정렬돼 있어야 한다.

    같은 사건을 여러 매체가 받아쓴 신디케이션이 다이제스트 상위를 도배하는 걸 막는다.
    실측(2026-08-30): KB금융 세미나 한 건에 기사 37건, 총리 AI 발언 20건,
    소진공 지능정보화위 출범 14건이 통과분에 들어 있었다.

    ★ 쌍별 비교가 아니라 **연결 성분(union-find)으로 군집을 만든다.**
      쌍별 greedy는 사슬을 못 끊는다 — A~B 0.83, B~C 0.89처럼 서로 조금씩 다른
      제목이 이어지면 컷 아래 쌍을 타고 여러 건이 살아남는다.
      실측 비교(컷 0.85, KB금융 17건 기준): greedy 6건 잔존 / 군집 1건 잔존.

    연쇄 병합으로 다른 주제가 묶일 위험은 실측으로 확인했다. 통과분 쌍별 유사도는
    무관한 쌍이 0.50~0.60에 몰려 있고 신디케이션은 0.80 이상이라 골이 뚜렷하다.
    0.85에서 만들어진 군집 201개를 눈으로 확인한 결과 전부 같은 사건이었다.

    전체 쌍 비교는 4만 건이면 16억 쌍이라 불가능하지만, 여기는 임계값을 통과한
    수천 건에만 적용하므로 청크 행렬곱으로 감당된다.
    """
    n = len(vecs)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:            # 경로 압축
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for start in range(0, n, chunk):
        end = min(start + chunk, n)
        sims = vecs[start:end] @ vecs.T   # (chunk, n) 코사인 (정규화 전제)
        for row in range(end - start):
            i = start + row
            # 자기보다 앞(더 상위)인 항목과만 이으면 대각선·중복 비교를 피한다
            for j in np.where(sims[row, :i] >= threshold)[0]:
                a, b = find(i), find(int(j))
                if a != b:
                    parent[a] = b

    # 군집마다 첫 항목(= 정렬상 가장 상위)만 남긴다
    keep = np.zeros(n, dtype=bool)
    seen: set[int] = set()
    for i in range(n):
        root = find(i)
        if root not in seen:
            seen.add(root)
            keep[i] = True
    return keep


def ensure_embeddings(ids: list[int], texts: dict[int, str], model_name: str,
                      batch_size: int, save_every: int = 20) -> EmbeddingCache:
    """캐시에 없는 항목만 인코딩해 채운 캐시를 돌려준다.

    모델 로딩(수십 초)과 GPU 인코딩이 이 파이프라인에서 제일 비싼 구간이라
    캐시가 곧 재실행 비용이다. 그래서 배치마다 중간 저장한다 — 죽어도 거기까지는 남는다.
    """
    slug = model_name.replace("/", "_").replace(":", "_")
    cache = EmbeddingCache(Path("data/processed") / f"embeddings_{slug}.npz")
    todo = cache.missing(ids)
    print(f"캐시 {len(cache.index):,}건 보유  →  새로 계산할 항목 {len(todo):,}건")
    if not todo:
        print("  전부 캐시에 있습니다 — 임베딩 생략\n")
        return cache

    from sentence_transformers import SentenceTransformer   # 로딩이 느려 지연 임포트
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    gpu = torch.cuda.get_device_name(0) if device == "cuda" else ""
    print(f"장치 {device} {gpu}  |  모델 로딩 중…")
    model = SentenceTransformer(model_name, device=device)

    t0, done = time.time(), 0
    for n, start in enumerate(range(0, len(todo), batch_size), start=1):
        chunk = todo[start:start + batch_size]
        vecs = model.encode([texts[i] for i in chunk], batch_size=batch_size,
                            convert_to_numpy=True, normalize_embeddings=True,
                            show_progress_bar=False)
        cache.add(chunk, vecs)
        done += len(chunk)
        if n % save_every == 0:
            cache.save()
            rate = done / max(time.time() - t0, 1e-9)
            left = (len(todo) - done) / max(rate, 1e-9)
            print(f"  {done:>7,}/{len(todo):,}  ({rate:.0f}건/초, 남은 시간 약 {left / 60:.1f}분)")
    cache.save()
    print(f"  완료 — {done:,}건 {time.time() - t0:.0f}초\n")
    return cache


def run_axes_mode(args, fcfg: dict) -> None:
    """축으로 통과를 정하고, 신디케이션 중복만 임베딩으로 걷어낸다.

    ★ 왜 관련성 점수를 버렸나 (사람 라벨 50건 실측, 2026-09-01)
        지금 필터(임베딩)   precision 0.560  recall 0.519
        축 1개 이상         precision 0.605  recall 0.963
      **끄는 쪽이 precision도 같거나 높고 recall은 두 배다.** 관련 있는 것의
      절반을 버리면서 정확도는 못 얻고 있었다.

      시드 탓이 아니다. 사람 라벨로 시드를 다시 만들어 반대쪽 절반을 재도
      AUC 0.677로 지금 초안(0.683)과 같았다(2겹 교차검증).
      사람이 가른 경계가 주제가 아니라 **성격**이었기 때문이다 —
      기관·정책·산업의 움직임인가, 개발자 개인의 도구·의견인가.
      둘 다 주제는 AI라서 문장 임베딩이 그 차이를 담지 못한다.

    ★ 그런데 중복 제거는 남긴다 — 같이 껐다가 급상승이 무너졌다 (실측 2026-09-01)
      축만으로 통과시킨 직후 급상승 1~5위가 전부 한 사건이었다.
      '데이터센터냉각솔루션' 'AIDV' '지능플랫폼' … 전부 LG전자 CEO 북미 인재
      채용 기사 한 건이 네이버에 50건 가까이 받아쓰이면서 딸려 올라온 n-gram이다.
      임베딩의 **관련성 점수**는 값을 못 했지만 **중복 군집**은 하고 있었다.
      그래서 관련성 컷만 버리고 dedup은 그대로 쓴다.
      digest의 제목 기반 is_syndicated는 지면 한 장만 보므로 이걸 대신하지 못한다 —
      급상승·연관어·기관은 통과분 전체를 센다.

    ★ 비교는 주차 안에서만 한다
      5만 건 전체면 12억 쌍이라 못 돌린다. 신디케이션은 같은 사건을 며칠 안에
      받아쓰는 것이라 주차 경계를 거의 넘지 않는다. 가장 큰 주가 9,003건
      (4천만 쌍)이라 청크 행렬곱으로 감당된다.

    relevance는 건드리지 않는다. 예전 값이 남아 화면·CSV가 깨지지 않게 하고,
    나중에 임베딩 모드로 되돌릴 때 다시 계산하지 않아도 되게 한다.
    """
    engine = get_engine()
    stmt = (select(items.c.id, items.c.title, items.c.summary,
                   items.c.published_week, items.c.cross_score)
            .where(items.c.id.in_(select(item_axes.c.item_id).distinct()))
            .order_by(items.c.id))
    if args.limit:
        stmt = stmt.limit(args.limit)
    with engine.connect() as conn:
        rows = conn.execute(stmt).all()
    if not rows:
        sys.exit("대상이 없습니다. 먼저 python -m src.prefilter 를 실행하세요.")
    print(f"모드 axes — 축이 하나라도 붙은 항목을 통과시킨다 ({len(rows):,}건)")

    dupes: set[int] = set()
    if args.no_dedup:
        print("  ⚠ 중복 제거 생략 (--no-dedup) — 급상승·연관어가 신디케이션에 먹힙니다")
    else:
        dedup_th = float(fcfg.get("dedup_threshold", 0.92))
        texts = {r.id: doc_text(r.title, r.summary) for r in rows}
        cache = ensure_embeddings([r.id for r in rows], texts, fcfg["model"],
                                  int(fcfg.get("batch_size", 64)), args.save_every)
        by_week: dict[str, list] = {}
        for r in rows:
            by_week.setdefault(r.published_week or "?", []).append(r)
        t0 = time.time()
        for _week, group in sorted(by_week.items()):
            if len(group) < 2:
                continue
            # 군집에서 살아남는 건 맨 앞 항목이므로 교차점수가 높은 쪽을 앞에 둔다
            group.sort(key=lambda r: (-(r.cross_score or 0.0), r.id))
            gids = [r.id for r in group]
            keep = near_duplicate_mask(cache.matrix(gids), dedup_th)
            dupes.update(i for i, k in zip(gids, keep) if not k)
        print(f"  중복 컷 {dedup_th} — 주차 {len(by_week)}개에서 신디케이션 "
              f"{len(dupes):,}건 제거 ({time.time() - t0:.0f}초)")

    if args.tune or args.limit:
        print("⏭  DB에 반영하지 않았습니다 (--tune/--limit)")
        return

    # ★ 바뀌는 행만 쓴다. 매번 8만 행을 통째로 UPDATE하면 Postgres가 죽은 행을
    #   그만큼 쌓아 DB가 붓는다 — 인덱스 테이블에서 이미 한 번 겪었다(125→136MB).
    axis_ids = select(item_axes.c.item_id).distinct()
    with engine.begin() as conn:
        conn.execute(items.update()
                     .where(items.c.id.in_(axis_ids), items.c.kept.isnot(True))
                     .values(kept=True))
        conn.execute(items.update()
                     .where(items.c.id.notin_(axis_ids), items.c.kept.isnot(False))
                     .values(kept=False))
        ordered = sorted(dupes)
        for start in range(0, len(ordered), 5000):
            conn.execute(items.update()
                         .where(items.c.id.in_(ordered[start:start + 5000]),
                                items.c.kept.isnot(False))
                         .values(kept=False))
    with engine.connect() as conn:
        kept_n = conn.execute(select(func.count()).select_from(items)
                              .where(items.c.kept.is_(True))).scalar_one()
    print(f"  완료 — DB 기준 통과 {kept_n:,}건")



def main() -> None:
    parser = argparse.ArgumentParser(description="시드 centroid 임베딩 필터")
    parser.add_argument("--mode", choices=["axes", "embedding"], default=None,
                        help="axes=축만으로 판정(기본) / embedding=시드 임베딩")
    parser.add_argument("--tune", action="store_true",
                        help="임계값 후보별 통과량만 보고 DB에 반영하지 않는다")
    parser.add_argument("--limit", type=int, default=0, help="처리 상한 (0=전체)")
    parser.add_argument("--threshold", type=float, default=None, help="임계값 덮어쓰기")
    parser.add_argument("--no-dedup", action="store_true", help="중복 제거 생략")
    parser.add_argument("--save-every", type=int, default=20,
                        help="이 배치 수마다 캐시를 디스크에 저장")
    args = parser.parse_args()

    cfg = load_config()
    fcfg = cfg["filter"]
    model_name = fcfg["model"]
    threshold = float(args.threshold if args.threshold is not None else fcfg["threshold"])
    dedup_th = float(fcfg.get("dedup_threshold", 0.92))
    batch_size = int(fcfg.get("batch_size", 64))

    # ★ 기본은 axes다. 라벨 실측에서 임베딩이 값을 못 하는 게 드러났다 —
    #   자세한 근거는 run_axes_mode의 주석과 HANDOFF.md.
    mode = (args.mode or fcfg.get("mode", "axes")).lower()
    if mode == "axes":
        run_axes_mode(args, fcfg)
        return

    print(f"모델 {model_name}  |  임계값 {threshold}  |  중복 컷 {dedup_th}")

    # ── 대상: prefilter를 통과한 항목(축이 하나라도 붙은 것) ──
    engine = get_engine()
    stmt = (select(items.c.id, items.c.title, items.c.summary)
            .where(items.c.id.in_(select(item_axes.c.item_id).distinct()))
            .order_by(items.c.id))
    if args.limit:
        stmt = stmt.limit(args.limit)
    with engine.connect() as conn:
        rows = conn.execute(stmt).all()
        total_items = conn.execute(select(func.count()).select_from(items)).scalar_one()
    if not rows:
        sys.exit("대상이 없습니다. 먼저 python -m src.prefilter 를 실행하세요.")
    print(f"대상 {len(rows):,}건 (전체 {total_items:,}건 중 prefilter 통과분)\n")

    ids = [r.id for r in rows]
    texts = {r.id: doc_text(r.title, r.summary) for r in rows}

    # ── 임베딩 (캐시 우선) ──
    slug = model_name.replace("/", "_").replace(":", "_")   # 시드 캐시 경로도 쓴다
    cache = ensure_embeddings(ids, texts, model_name, batch_size, args.save_every)

    # ── 시드 centroid (양성 / 부정) ──
    #
    # 부정 시드를 빼는 이유(실측): 양성 centroid만 쓰면 '엔비디아 M&A', '새 코딩 도구'
    # 같은 일반 AI 뉴스가 0.55~0.60을 받아 정답(0.576~0.659)과 구간이 통째로 겹친다.
    # 임계값을 어디에 둬도 안 갈라진다. 무관한 것의 centroid를 따로 만들어 빼면
    # 양쪽에 다 가까운 항목이 0 근처로 눌리면서 갈라진다.
    #     relevance = sim(양성) - sim(부정)      ← DB에 저장되는 값이 이것이다
    seeds = load_seeds(fcfg["seed_path"])
    # 시드 자체는 수십 문장이라 계산이 싸지만 모델 로딩(수십 초)이 아깝다 → 캐시한다.
    # 파일명에 시드 내용 해시를 박아 시드를 한 글자라도 고치면 자동으로 무효화되게 한다.
    # (문장 수만 비교하면 '한 문장을 다른 문장으로 교체'한 경우를 못 잡는다)
    seed_hash = hashlib.sha1("\n".join(seeds).encode("utf-8")).hexdigest()[:8]
    seed_cache = EmbeddingCache(Path("data/processed") / f"seeds_{slug}_{seed_hash}.npz")
    if len(seed_cache.index) == len(seeds):
        centroid_src = seed_cache.vecs
    else:
        from sentence_transformers import SentenceTransformer
        import torch
        model = SentenceTransformer(model_name,
                                    device="cuda" if torch.cuda.is_available() else "cpu")
        centroid_src = model.encode(seeds, convert_to_numpy=True,
                                    normalize_embeddings=True).astype(np.float32)
        seed_cache.ids = np.arange(len(seeds), dtype=np.int64)
        seed_cache.vecs = centroid_src
        seed_cache.index = {i: i for i in range(len(seeds))}
        seed_cache.save()
    centroid = centroid_src.mean(axis=0)
    centroid /= np.linalg.norm(centroid)

    doc_vecs = cache.matrix(ids)
    sims = doc_vecs @ centroid           # 정규화했으므로 내적 = 코사인

    neg_path = fcfg.get("negative_seed_path")
    if neg_path and Path(neg_path).exists():
        neg_seeds = load_seeds(neg_path)
        neg_hash = hashlib.sha1("\n".join(neg_seeds).encode("utf-8")).hexdigest()[:8]
        neg_cache = EmbeddingCache(Path("data/processed") / f"negseeds_{slug}_{neg_hash}.npz")
        if len(neg_cache.index) != len(neg_seeds):
            from sentence_transformers import SentenceTransformer
            import torch
            m = SentenceTransformer(model_name,
                                    device="cuda" if torch.cuda.is_available() else "cpu")
            vecs = m.encode(neg_seeds, convert_to_numpy=True,
                            normalize_embeddings=True).astype(np.float32)
            neg_cache.ids = np.arange(len(neg_seeds), dtype=np.int64)
            neg_cache.vecs = vecs
            neg_cache.index = {i: i for i in range(len(neg_seeds))}
            neg_cache.save()
        neg_centroid = neg_cache.vecs.mean(axis=0)
        neg_centroid /= np.linalg.norm(neg_centroid)
        neg_sims = doc_vecs @ neg_centroid
        print(f"시드 양성 {len(seeds)}문장 / 부정 {len(neg_seeds)}문장 → 대비 점수")
        print(f"  양성 평균 {sims.mean():.3f}  부정 평균 {neg_sims.mean():.3f}\n")
        sims = sims - neg_sims
    else:
        print(f"시드 {len(seeds)}문장 → centroid  (부정 시드 없음)\n")

    # ── 임계값 후보별 통과량 ──
    print(f"{'=' * 62}\n① 유사도 분포\n{'=' * 62}")
    for q in (50, 75, 90, 95, 99):
        print(f"  상위 {100 - q:>2}%  ≥ {np.percentile(sims, q):.3f}")
    print(f"  평균 {sims.mean():.3f}  최대 {sims.max():.3f}  최소 {sims.min():.3f}\n")
    for t in THRESHOLD_GRID:
        n = int((sims >= t).sum())
        mark = "  ← 현재" if abs(t - threshold) < 1e-9 else ""
        print(f"    {t:.2f}  {n:>7,}건 ({n / len(sims) * 100:5.1f}%){mark}")

    if args.tune:
        print("\n⏭  DB에 반영하지 않았습니다 (--tune)")
        return

    # ── 통과 판정 + 중복 제거 ──
    passed = sims >= threshold
    kept_idx = np.where(passed)[0]
    print(f"\n{'=' * 62}\n② 판정\n{'=' * 62}")
    print(f"  임계값 통과   {len(kept_idx):>7,}건")

    dup_removed = 0
    if not args.no_dedup and len(kept_idx) > 1:
        # 유사도 높은 순으로 정렬해 비교한다 — 중복 쌍에서 더 관련 있는 쪽이 남는다
        order = kept_idx[np.argsort(-sims[kept_idx])]
        mask = near_duplicate_mask(doc_vecs[order], dedup_th)
        dropped = set(order[~mask].tolist())
        for i in dropped:
            passed[i] = False
        dup_removed = len(dropped)
        print(f"  중복 제거    -{dup_removed:>6,}건 (코사인 ≥ {dedup_th})")
    print(f"  최종 통과    {int(passed.sum()):>7,}건 "
          f"({passed.sum() / len(sims) * 100:.1f}% of prefilter 통과분)")

    # ── 적재 ──
    print(f"\nrelevance·kept 적재 중…")
    stmt_up = (items.update()
               .where(items.c.id == bindparam("b_id"))
               .values(relevance=bindparam("b_rel"), kept=bindparam("b_kept")))
    payload = [{"b_id": int(i), "b_rel": float(s), "b_kept": bool(k)}
               for i, s, k in zip(ids, sims, passed)]
    with engine.begin() as conn:
        for i in range(0, len(payload), 5000):
            conn.execute(stmt_up, payload[i:i + 5000])
        if not args.limit:
            # 0축 항목은 명시적으로 탈락 처리한다 — 키워드를 고쳐 다시 돌렸을 때
            # 예전 kept=True가 남아 있으면 다이제스트에 유령 항목이 올라온다.
            #
            # id 목록을 notin_에 그대로 넘기면 안 된다. 4만 개가 바인드 파라미터로
            # 풀려서 SQLite 상한(기본 999)에 걸린다. 서브쿼리로 넘기면 파라미터가 0개다.
            conn.execute(items.update()
                         .where(items.c.id.notin_(select(item_axes.c.item_id)))
                         .values(kept=False))

    with engine.connect() as conn:
        kept_n = conn.execute(select(func.count()).select_from(items)
                              .where(items.c.kept.is_(True))).scalar_one()
    print(f"  완료 — DB 기준 통과 {kept_n:,}건")
    print("\n  확인: python -m src.db --summary")
    print("  다음: 수동 라벨 50건으로 precision 측정 (목표 ≥ 85%)")


if __name__ == "__main__":
    main()
