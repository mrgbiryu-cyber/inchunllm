#!/usr/bin/env python3
"""정적 레퍼런스 정합성 보정 스크립트.

- legacy 데이터에서 누적된 quality 이슈(낮은 confidence / need_review 미설정 / payload 미완성)를 보정
- 산업코드 NULL 기준 중복(유효 키) 제거 (domain + coalesce(industry_code,'') + tag + title)
"""

from __future__ import annotations

import argparse
from typing import Any, Dict

from app.core.config import settings
from app.core.database import AsyncSessionLocal, ResearchStaticReferenceModel
from sqlalchemy import text


def _normalize_payload(payload: Any, min_confidence: int) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}
    changed = False

    if "parsed_confidence" not in payload:
        payload["parsed_confidence"] = min_confidence
        changed = True
    else:
        try:
            int(float(payload.get("parsed_confidence")))
        except Exception:
            payload["parsed_confidence"] = min_confidence
            changed = True

        if "need_review" not in payload:
            payload["need_review"] = False
            changed = True
        else:
            val = str(payload.get("need_review")).strip().lower()
            if val in {"1", "true", "y", "yes", "on"}:
                # 필요 리뷰 true인 데이터는 유지
                changed = False
            else:
                payload["need_review"] = False
                if val not in {"false", "0", "", "none", "null", "false", "no", "off"}:
                    changed = True

    if not isinstance(payload.get("source_type"), str):
        payload["source_type"] = "seed_reference"
        changed = True

    if payload.get("source_type") not in {"pdf_text", "pdf_ocr", "seed_reference"}:
        payload["source_type"] = str(payload.get("source_type"))
        changed = True

    return payload, changed


async def repair(dry_run: bool, min_confidence: int) -> None:
    async with AsyncSessionLocal() as session:
        if settings.DATABASE_SCHEMA != "public":
            await session.execute(text(f'SET search_path TO "{settings.DATABASE_SCHEMA}", public'))

        rows = (await session.execute(text("SELECT COUNT(*) FROM research_static_reference"))).scalar_one()
        print(f"[INFO] 대상 행 전체: {rows}")

        from sqlalchemy import select

        result = await session.execute(select(ResearchStaticReferenceModel))
        items = result.scalars().all()
        if not items:
            print("[INFO] 데이터가 없어 종료")
            return

        updated = 0
        for r in items:
            payload, changed = _normalize_payload(r.payload_json or {}, min_confidence=min_confidence)
            if changed:
                r.payload_json = payload
                updated += 1

        # 중복 key(산업코드 NULL 동일취급) 대상 식별 및 삭제
        dup_rows = (await session.execute(
            text(
                """
                WITH grouped AS (
                    SELECT
                        domain,
                        COALESCE(industry_code, '') AS ic,
                        tag,
                        title,
                        COUNT(*) AS cnt
                    FROM research_static_reference
                    WHERE domain IS NOT NULL AND title IS NOT NULL AND tag IS NOT NULL
                    GROUP BY domain, COALESCE(industry_code, ''), tag, title
                    HAVING COUNT(*) > 1
                )
                SELECT domain, ic, tag, title, cnt
                FROM grouped
                ORDER BY domain, ic, tag, title
                """
            )
        )).all()

        print("[INFO] 중복 키 개수:", len(dup_rows))
        for d in dup_rows:
            ic = d[1]
            print(
                f"[DUP] domain={d[0]} industry={ic or 'NULL'} tag={d[2]} title={d[3]} cnt={d[4]}"
            )

        if dry_run:
            print("[DRY-RUN] 중복 삭제/커밋을 수행하지 않습니다.")
            print(f"[DRY-RUN] payload 보정 대상: {updated}개")
            await session.rollback()
            return

        deleted = 0
        # keep is_active 우선, then 최신(created_at) 선호
        del_rows = (await session.execute(
            text(
                """
                WITH ranked AS (
                    SELECT
                        id,
                        ROW_NUMBER() OVER (
                            PARTITION BY domain, COALESCE(industry_code, ''), tag, title
                            ORDER BY is_active DESC NULLS LAST, COALESCE(updated_at, created_at) DESC, id DESC
                        ) AS rn
                    FROM research_static_reference
                    WHERE domain IS NOT NULL AND title IS NOT NULL AND tag IS NOT NULL
                )
                SELECT id FROM ranked WHERE rn > 1
                """
            )
        )).scalars().all()

        if del_rows:
            # SQLAlchemy는 IN 파라미터 목록 정리
            for idx in range(0, len(del_rows), 500):
                chunk = del_rows[idx : idx + 500]
                for row_id in chunk:
                    await session.execute(
                        text("DELETE FROM research_static_reference WHERE id = :id"),
                        {"id": str(row_id)},
                    )
            deleted = len(del_rows)

        print(f"[INFO] payload 보정 대상: {updated}개")
        print(f"[INFO] 중복 삭제 대상: {deleted}개")

        await session.commit()
        print("[OK] 정합성 보정 커밋 완료")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="적용 전 시뮬레이션만 수행")
    p.add_argument(
        "--min-confidence",
        type=int,
        default=55,
        help="legacy 데이터 채움 기본 confidence(기본 55)",
    )
    args = p.parse_args()
    import asyncio

    asyncio.run(repair(dry_run=args.dry_run, min_confidence=args.min_confidence))


if __name__ == "__main__":
    main()
