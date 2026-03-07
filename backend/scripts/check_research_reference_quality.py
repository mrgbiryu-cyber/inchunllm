#!/usr/bin/env python3
"""정적 레퍼런스 품질 검사 (운영/배포 전 점검용).

- 도메인별/산업코드별 행 수
- payload 기반 parsed_confidence/need_review 검사
- URL 포맷 검사
- 중복 키 검사
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Tuple

from app.core.config import settings
from app.core.database import AsyncSessionLocal, ResearchStaticReferenceModel
from sqlalchemy import select


def _as_dict(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    return payload


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _is_invalid_url(v: Any) -> bool:
    if v is None:
        return False
    s = str(v).strip()
    if not s:
        return False
    return not (s.startswith("http://") or s.startswith("https://"))


async def check_quality(min_confidence: int, fail_on_issue: bool) -> int:
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(ResearchStaticReferenceModel))).scalars().all()

    by_domain: Dict[str, int] = defaultdict(int)
    by_industry: Dict[str, int] = defaultdict(int)
    duplicates: List[str] = []
    seen = set()

    issues = 0
    issue_counter: Dict[str, int] = defaultdict(int)
    ocr_attempted = 0
    ocr_failed = 0
    ocr_error_counts: Dict[str, int] = defaultdict(int)
    sample_ocr_errors: List[Tuple[str, str]] = []
    source_type_counts: Dict[str, int] = defaultdict(int)

    for r in rows:
        by_domain[r.domain] += 1
        by_industry[str(r.industry_code or "NULL")] += 1
        key = (r.domain, r.industry_code or "", r.tag or "", r.title or "")
        if key in seen:
            duplicates.append("|".join(key))
        else:
            seen.add(key)

        payload = _as_dict(r.payload_json or {})
        source_type = str(payload.get("source_type") or "unknown")
        source_type_counts[source_type] += 1

        conf = _to_int(
            payload.get("parsed_confidence") or payload.get("confidence"),
            default=0,
        )
        if source_type in {"pdf_ocr", "ocr", "pdf_ocr_fallback"}:
            ocr_attempted += 1
            ocr_error = str(payload.get("ocr_error") or "").strip()
            if ocr_error:
                ocr_failed += 1
                ocr_error_counts[ocr_error] += 1
                if len(sample_ocr_errors) < 30:
                    sample_ocr_errors.append((str(r.title or ""), ocr_error))
                issue_counter["ocr_error"] += 1
                issues += 1
            if payload.get("ocr_engine") == "pdf2image+ocr_failed":
                issue_counter["ocr_failed_engine"] += 1

        if conf < min_confidence:
            issue_counter["low_confidence"] += 1
            issues += 1
        if payload.get("need_review") in {"true", True, 1, "1", "yes", "Y", "y", "on"}:
            issue_counter["needs_review"] += 1
            issues += 1
        if _is_invalid_url(r.source_url):
            issue_counter["invalid_url"] += 1
            issues += 1

    report = {
        "ts": datetime.utcnow().isoformat(),
        "db_url": settings.DATABASE_URL,
        "total": len(rows),
        "by_domain": dict(sorted(by_domain.items())),
        "by_industry": dict(sorted(by_industry.items())),
        "duplicate_count": len(duplicates),
        "ocr": {
            "attempted": ocr_attempted,
            "failed": ocr_failed,
            "error_counts": dict(sorted(ocr_error_counts.items())),
            "source_type_counts": dict(sorted(source_type_counts.items())),
            "sample_errors": sample_ocr_errors,
        },
        "quality_issue_count": issues,
        "quality_issue_breakdown": dict(sorted(issue_counter.items())),
        "sample_duplicate_keys": duplicates[:50],
        "schema": {"table": "research_static_reference"},
    }

    print(json.dumps(report, ensure_ascii=False))

    if fail_on_issue and (issues > 0 or duplicates):
        raise SystemExit(1)

    return 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--min-confidence", type=int, default=0)
    p.add_argument(
        "--fail-on-issue",
        action="store_true",
        default=False,
    )
    args = p.parse_args()
    raise SystemExit(asyncio.run(check_quality(args.min_confidence, args.fail_on_issue)))


if __name__ == "__main__":
    import asyncio

    main()
