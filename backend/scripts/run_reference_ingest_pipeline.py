#!/usr/bin/env python3
"""Reference ingest one-shot pipeline.

Flow:
1) PDF -> CSV 정규화(선택, --source-pdf)
2) CSV/XLSX import to research_static_reference
3) quality checker 실행
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import List, Optional


def _run(cmd: List[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed: {' '.join(cmd)}\nSTDOUT={proc.stdout}\nSTDERR={proc.stderr}"
        )
    return proc.stdout.strip()


def run_pipeline(
    *,
    source_pdf: Optional[str],
    source_file: Optional[str],
    output_csv: Optional[str],
    industry_code: str,
    include_empty: bool,
    use_ocr: bool,
    strict: bool,
    dry_run: bool,
    min_confidence: int,
    require_review_pass: bool,
    fail_on_quality_issue: bool,
    run_quality_check: bool,
) -> None:
    root = Path(__file__).resolve().parent

    source_path = Path(source_pdf or source_file or "").expanduser().resolve()
    if not source_pdf and not source_file:
        raise ValueError("--source-pdf 또는 --source-file 중 하나는 필수입니다.")
    if not source_path.exists():
        raise FileNotFoundError(f"입력 파일이 없습니다: {source_path}")

    if source_pdf:
        if output_csv is None:
            output_csv = str(root / f"research_reference_from_pdf_{source_path.stem}.csv")
        cmd_transform = [
            ".venv/bin/python",
            str(root / "transform_research_pdf_to_csv.py"),
            "--source",
            str(source_path),
            "--output",
            output_csv,
            "--industry-code",
            industry_code,
        ]
        if include_empty:
            cmd_transform.append("--include-empty")
        if use_ocr:
            cmd_transform.append("--ocr")
        if strict:
            cmd_transform.append("--strict")
        if min_confidence and strict:
            cmd_transform.extend(["--min-confidence", str(min_confidence)])
        print(f"[PIPELINE] transform: {' '.join(cmd_transform)}")
        out = _run(cmd_transform)
        print(out)
        source_path = Path(output_csv)

    cmd_import = [
        ".venv/bin/python",
        str(root / "import_research_static_reference.py"),
        "--source",
        str(source_path),
        "--min-confidence",
        str(min_confidence),
    ]
    if require_review_pass:
        cmd_import.append("--require-review-pass")
    if fail_on_quality_issue:
        cmd_import.append("--fail-on-quality-issue")
    if dry_run:
        cmd_import.append("--dry-run")

    print(f"[PIPELINE] import: {' '.join(cmd_import)}")
    out = _run(cmd_import)
    print(out)

    if run_quality_check:
        cmd_quality = [
            ".venv/bin/python",
            str(root / "check_research_reference_quality.py"),
            "--min-confidence",
            str(min_confidence),
        ]
        if fail_on_quality_issue:
            cmd_quality.append("--fail-on-issue")
        print(f"[PIPELINE] quality check: {' '.join(cmd_quality)}")
        out = _run(cmd_quality)
        print(out)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--source-pdf", help="PDF 파일/폴더를 입력하면 CSV 변환 후 임포트")
    p.add_argument("--source-file", help="CSV/XLSX 파일 직접 임포트")
    p.add_argument(
        "--output-csv",
        default=None,
        help="PDF 정규화 결과 CSV 경로",
    )
    p.add_argument("--industry-code", default="IT")
    p.add_argument("--include-empty", action="store_true")
    p.add_argument("--ocr", action="store_true")
    p.add_argument("--strict", action="store_true")
    p.add_argument(
        "--min-confidence",
        type=int,
        default=55,
        help="임포트 품질 최소 confidence(기본 55)",
    )
    p.add_argument(
        "--require-review-pass",
        dest="require_review_pass",
        action="store_true",
        default=True,
        help="need_review=true 행 임포트 차단(기본값 적용)",
    )
    p.add_argument(
        "--no-require-review-pass",
        dest="require_review_pass",
        action="store_false",
        help="need_review=true 행 임포트 차단 해제",
    )
    p.add_argument(
        "--fail-on-quality-issue",
        dest="fail_on_quality_issue",
        action="store_true",
        default=True,
        help="품질 경고를 오류로 간주하고 종료(기본값: 실패 처리)",
    )
    p.add_argument(
        "--no-fail-on-quality-issue",
        dest="fail_on_quality_issue",
        action="store_false",
        help="품질 경고를 오류로 간주하지 않음",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="DB 반영 없이 전체 파이프라인 동작 검증",
    )
    p.add_argument(
        "--skip-quality-check",
        action="store_true",
        help="품질 점검 단계 생략",
    )
    args = p.parse_args()

    run_pipeline(
        source_pdf=args.source_pdf,
        source_file=args.source_file,
        output_csv=args.output_csv,
        industry_code=args.industry_code,
        include_empty=args.include_empty,
        use_ocr=args.ocr,
        strict=args.strict,
        dry_run=args.dry_run,
        min_confidence=args.min_confidence,
        require_review_pass=args.require_review_pass,
        fail_on_quality_issue=args.fail_on_quality_issue,
        run_quality_check=not args.skip_quality_check,
    )


if __name__ == "__main__":
    main()
