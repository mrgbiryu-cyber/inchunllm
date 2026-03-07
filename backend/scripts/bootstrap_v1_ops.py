#!/usr/bin/env python3
"""운영용 V1.0 부트스트랩 스크립트.

실행 순서:
1) 키/환경 가드
2) 필수 테이블·컬럼 계약 점검
3) 시드 SQL 적용
4) 정적 레퍼런스 업로드(CSV/XLSX)
5) v1.0 연구 수집 smoke 실행
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
from datetime import datetime
from pathlib import Path

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.company import CompanyProfile
from app.services.business_research_service import business_research_service
from app.services.growth_v1_controls import POLICY_VERSION_V1, set_project_policy_version
from sqlalchemy import text


REQUIRED_TABLES = (
    "conversation_state",
    "artifact_approval_state",
    "growth_templates",
    "research_static_reference",
)


def _resolve_repo_root() -> Path:
    return Path(__file__).resolve().parent


def run_pdf_transform(
    source_pdf: Path, output_csv: Path, strict: bool = False, use_ocr: bool = False
) -> Path:
    if not source_pdf.exists():
        raise FileNotFoundError(f"PDF 파일/폴더가 없습니다: {source_pdf}")

    transform_script = _resolve_repo_root() / "transform_research_pdf_to_csv.py"
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        ".venv/bin/python",
        str(transform_script),
        "--source",
        str(source_pdf),
        "--output",
        str(output_csv),
    ]
    if strict:
        cmd.append("--strict")
    if use_ocr:
        cmd.append("--ocr")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"PDF 정규화 실패: source={source_pdf}\nSTDOUT={proc.stdout}\nSTDERR={proc.stderr}"
        )
    return output_csv


def _psql_url() -> str:
    db_url = (settings.DATABASE_URL or "").strip()
    if not db_url:
        raise RuntimeError("DATABASE_URL이 설정되어 있지 않습니다.")
    return db_url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _exists_table(session, table_name: str) -> bool:
    res = await session.execute(
        text(
            """
            SELECT EXISTS (
              SELECT 1
              FROM information_schema.tables
              WHERE table_schema = :schema
                AND table_name = :table_name
            )
            """
        ),
        {"schema": settings.DATABASE_SCHEMA, "table_name": table_name},
    )
    return bool(res.scalar_one())


async def _exists_column(session, table_name: str, column_name: str) -> bool:
    res = await session.execute(
        text(
            """
            SELECT EXISTS (
              SELECT 1
              FROM information_schema.columns
              WHERE table_schema = :schema
                AND table_name = :table_name
                AND column_name = :column_name
            )
            """
        ),
        {
            "schema": settings.DATABASE_SCHEMA,
            "table_name": table_name,
            "column_name": column_name,
        },
    )
    return bool(res.scalar_one())


async def check_db_contracts() -> list[str]:
    issues: list[str] = []
    async with AsyncSessionLocal() as session:
        for table in REQUIRED_TABLES:
            if not await _exists_table(session, table):
                issues.append(f"필수 테이블 없음: {table}")

        if not await _exists_column(session, "growth_templates", "sections_keys_ordered"):
            issues.append("필수 컬럼 없음: growth_templates.sections_keys_ordered")
        if not await _exists_column(session, "growth_templates", "source_pdf"):
            issues.append("필수 컬럼 없음: growth_templates.source_pdf")

    return issues


def _check_keys() -> list[str]:
    alerts: list[str] = []
    if not settings.TAVILY_API_KEY:
        alerts.append("TAVILY_API_KEY 미설정")
    if not settings.OPENROUTER_API_KEY:
        alerts.append("OPENROUTER_API_KEY 미설정")
    return alerts


def run_seed_sql(seed_file: Path) -> None:
    if not seed_file.exists():
        raise FileNotFoundError(f"Seed 파일이 없습니다: {seed_file}")

    schema = settings.DATABASE_SCHEMA or "public"
    cmd = [
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        f"SET search_path TO {schema}, public;",
        _psql_url(),
        "-f",
        str(seed_file),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Seed 실행 실패: {seed_file}\nSTDOUT={proc.stdout}\nSTDERR={proc.stderr}"
        )


def run_reference_import(
    source_file: Path,
    dry_run: bool = False,
    min_confidence: int = 55,
    require_review_pass: bool = True,
    fail_on_quality_issue: bool = True,
) -> None:
    if not source_file.exists():
        raise FileNotFoundError(f"레퍼런스 파일이 없습니다: {source_file}")
    cmd = [
        str(Path(__file__).resolve().parent / "import_research_static_reference.py"),
        "--source",
        str(source_file),
    ]
    cmd.extend(["--min-confidence", str(min_confidence)])
    if require_review_pass:
        cmd.append("--require-review-pass")
    if fail_on_quality_issue:
        cmd.append("--fail-on-quality-issue")
    if dry_run:
        cmd.append("--dry-run")

    proc = subprocess.run([".venv/bin/python", *cmd], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"레퍼런스 임포트 실패: {source_file}\nSTDOUT={proc.stdout}\nSTDERR={proc.stderr}"
        )
    print(proc.stdout.strip())


def run_reference_reconcile(min_confidence: int = 55, dry_run: bool = False) -> None:
    script = _resolve_repo_root() / "reconcile_research_static_reference.py"
    cmd = [
        ".venv/bin/python",
        str(script),
        "--min-confidence",
        str(min_confidence),
    ]
    if dry_run:
        cmd.append("--dry-run")

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"레퍼런스 정합성 보정 실패: script={script}\nSTDOUT={proc.stdout}\nSTDERR={proc.stderr}"
        )
    print(proc.stdout.strip())


def run_reference_quality_check(min_confidence: int = 55, fail_on_issue: bool = True) -> None:
    script = _resolve_repo_root() / "check_research_reference_quality.py"
    cmd = [".venv/bin/python", str(script), "--min-confidence", str(min_confidence)]
    if fail_on_issue:
        cmd.append("--fail-on-issue")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"레퍼런스 품질 점검 실패: script={script}\nSTDOUT={proc.stdout}\nSTDERR={proc.stderr}"
        )
    print(proc.stdout.strip())


async def run_research_smoke(project_id: str, industry_code: str = "IT") -> None:
    await set_project_policy_version(
        project_id=project_id,
        policy_version=POLICY_VERSION_V1,
        consultation_mode="예비",
    )

    profile = CompanyProfile(
        company_name="운영_smoke_사전테스트",
        item_description="AI 기반 서비스로 수익화를 검증하는 비즈니스 모델",
        industry_code=industry_code,
        annual_revenue=1_000_000,
        years_in_business=1,
    )

    result = await business_research_service.collect_for_project(
        project_id=project_id,
        profile=profile,
        requested_sources=["public_api", "static_db", "user_input", "llm_support"],
        force_refresh=True,
    )

    status = [f"{k}:{v['status']}:{','.join(v.get('sources', []))}" for k, v in result["data"].items()]
    print("[RESEARCH_SMOKE_OK]", " | ".join(status))


async def main_async(args: argparse.Namespace) -> int:
    print("[STEP] 키/환경 체크")
    for alert in _check_keys():
        print(f"[WARN] {alert}")

    issues = await check_db_contracts()
    if issues:
        print("[ERROR] DB 필수 계약 위반")
        for issue in issues:
            print(f" - {issue}")
        return 2
    print("[OK] DB 계약 확인 완료")

    if args.seed_catalog:
        print("[STEP] 템플릿 시드 반영")
        run_seed_sql(args.seed_catalog_file)
        print(f"[OK] seed_catalog 적용: {args.seed_catalog_file}")

    if args.seed_static:
        print("[STEP] 정적 레퍼런스 기본 시드 반영")
        run_seed_sql(args.seed_static_file)
        print(f"[OK] seed_research_static_reference 적용: {args.seed_static_file}")
        if not args.reference_skip_reconcile:
            print("[STEP] 정적 레퍼런스 정합성 보정")
            run_reference_reconcile(
                min_confidence=args.reference_min_confidence,
                dry_run=args.dry_run_reference,
            )
            print("[OK] 정적 레퍼런스 정합성 보정 완료")
            print("[STEP] 정적 레퍼런스 품질 게이트 검증")
            run_reference_quality_check(
                min_confidence=args.reference_min_confidence,
                fail_on_issue=True,
            )
            print("[OK] 정적 레퍼런스 품질 게이트 검증 완료")

    if args.reference_file:
        print("[STEP] 정적 레퍼런스 업로드")
        run_reference_import(
            args.reference_file,
            dry_run=args.dry_run_reference,
            min_confidence=args.reference_min_confidence,
            require_review_pass=args.reference_require_review_pass,
            fail_on_quality_issue=args.reference_fail_on_quality_issue,
        )
        print(f"[OK] reference 업로드 파일 적용: {args.reference_file}")
        if not args.reference_skip_reconcile:
            print("[STEP] 정적 레퍼런스 정합성 보정")
            run_reference_reconcile(
                min_confidence=args.reference_min_confidence,
                dry_run=args.dry_run_reference,
            )
            print("[OK] 정적 레퍼런스 정합성 보정 완료")
            print("[STEP] 정적 레퍼런스 품질 게이트 검증")
            run_reference_quality_check(
                min_confidence=args.reference_min_confidence,
                fail_on_issue=True,
            )
            print("[OK] 정적 레퍼런스 품질 게이트 검증 완료")

    if args.pdf_to_csv:
        print("[STEP] PDF 정규화 CSV 변환")
        output = args.pdf_to_csv_output
        if output is None:
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            output = _resolve_repo_root() / f"research_reference_from_pdf_{timestamp}.csv"
        generated_csv = run_pdf_transform(
            source_pdf=args.pdf_to_csv,
            output_csv=output,
            strict=args.pdf_strict,
            use_ocr=args.pdf_ocr,
        )
        print(f"[OK] PDF 정규화 결과: {generated_csv}")
        if args.pdf_to_csv_import:
            print("[STEP] PDF 정규화 결과를 정적 레퍼런스로 임포트")
            run_reference_import(
                generated_csv,
                dry_run=args.dry_run_reference,
                min_confidence=args.reference_min_confidence,
                require_review_pass=args.reference_require_review_pass,
                fail_on_quality_issue=args.reference_fail_on_quality_issue,
            )
            print(f"[OK] PDF 정규화 결과 임포트 완료: {generated_csv}")
            if not args.reference_skip_reconcile:
                print("[STEP] PDF 정규화 결과 정합성 보정")
                run_reference_reconcile(
                    min_confidence=args.reference_min_confidence,
                    dry_run=args.dry_run_reference,
                )
                print("[OK] PDF 정규화 결과 정합성 보정 완료")
                print("[STEP] PDF 정규화 결과 품질 게이트 검증")
                run_reference_quality_check(
                    min_confidence=args.reference_min_confidence,
                    fail_on_issue=True,
                )
                print("[OK] PDF 정규화 결과 품질 게이트 검증 완료")

    if args.run_smoke:
        print("[STEP] v1 연구 수집 smoke 실행")
        await run_research_smoke(args.smoke_project_id, industry_code=args.industry_code)
        print(f"[OK] research smoke complete: project={args.smoke_project_id}")

    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--seed-catalog", action="store_true", default=False, help="seed_catalog_v1_5forms.sql 적용")
    p.add_argument(
        "--seed-catalog-file",
        type=Path,
        default=Path("scripts/seed_catalog_v1_5forms.sql"),
    )
    p.add_argument("--seed-static", action="store_true", default=False, help="seed_research_static_reference.sql 적용")
    p.add_argument(
        "--seed-static-file",
        type=Path,
        default=Path("scripts/seed_research_static_reference.sql"),
    )
    p.add_argument("--reference-file", type=Path, default=None, help="CSV/XLSX 정적 레퍼런스 입력")
    p.add_argument("--dry-run-reference", action="store_true", default=False)
    p.add_argument("--run-smoke", action="store_true", default=False, help="v1.0 프로젝트 연구 수집 smoke 실행")
    p.add_argument("--smoke-project-id", type=str, default="00000000-0000-4000-8000-000000001001")
    p.add_argument("--industry-code", type=str, default="IT")
    p.add_argument(
        "--pdf-to-csv",
        type=Path,
        default=None,
        help="PDF 파일 또는 폴더를 research_reference_template.csv 스키마 CSV로 정규화",
    )
    p.add_argument(
        "--pdf-to-csv-output",
        type=Path,
        default=None,
        help="PDF 정규화 결과 CSV 경로(미지정 시 scripts/research_reference_from_pdf_YYYYMMDD_HHMMSS.csv)",
    )
    p.add_argument(
        "--pdf-strict",
        action="store_true",
        default=False,
        help="PDF 정규화 strict 모드(need_review/저신뢰 행 스킵)",
    )
    p.add_argument(
        "--pdf-ocr",
        action="store_true",
        default=False,
        help="텍스트 추출 실패 페이지 OCR 시도(pytesseract/pdf2image 필요)",
    )
    p.add_argument(
        "--pdf-to-csv-import",
        action="store_true",
        default=False,
        help="PDF 정규화 CSV를 즉시 정적 레퍼런스에 임포트",
    )
    p.add_argument(
        "--reference-min-confidence",
        type=int,
        default=55,
        help="정적 레퍼런스 임포트 품질 게이트 최소 confidence (기본 55)",
    )
    p.add_argument(
        "--reference-require-review-pass",
        dest="reference_require_review_pass",
        action="store_true",
        default=True,
        help="need_review=true 행은 임포트에서 제외(기본값: 적용)",
    )
    p.add_argument(
        "--no-reference-require-review-pass",
        dest="reference_require_review_pass",
        action="store_false",
        help="need_review=true 행 임포트 제한 해제",
    )
    p.add_argument(
        "--reference-fail-on-quality-issue",
        dest="reference_fail_on_quality_issue",
        action="store_true",
        default=True,
        help="임포트 품질 경고를 실패로 처리(기본값: 실패 처리)",
    )
    p.add_argument(
        "--no-reference-fail-on-quality-issue",
        dest="reference_fail_on_quality_issue",
        action="store_false",
        help="임포트 품질 경고 실패 처리 해제",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    print("=== bootstrap_v1_ops (start) ===")
    try:
        code = asyncio.run(main_async(args))
    except Exception as exc:
        print(f"[FAIL] {exc}")
        raise SystemExit(1)
    print("=== bootstrap_v1_ops (done) ===")
    raise SystemExit(code)


if __name__ == "__main__":
    main()
