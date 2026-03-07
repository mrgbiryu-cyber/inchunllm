#!/usr/bin/env python3
"""PDF → 운영 템플릿 CSV 정규화 스텝(A안).

입력: PDF 파일 또는 PDF 폴더
출력: research_reference_template.csv 형태의 CSV
운영: 변환 후 인력 검수/수정 후 임포트 파이프라인으로 전달
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


ALLOWED_DOMAINS = {
    "market_size",
    "industry_trends",
    "competitor_info",
    "policy_support",
}

PDF_HINT_BY_FILENAME = {
    "market": "market_size",
    "시장": "market_size",
    "trend": "industry_trends",
    "동향": "industry_trends",
    "competitor": "competitor_info",
    "경쟁": "competitor_info",
    "policy": "policy_support",
    "지원": "policy_support",
}

DOMAIN_HINTS = {
    "market_size": ["시장규모", "market size", "시장 규모", "매출", "규모", "매출액"],
    "industry_trends": ["동향", "trend", "산업동향", "시장동향", "기술", "규제"],
    "competitor_info": ["경쟁사", "경쟁", "competitor", "비교", "시장 점유율", "벤치마크"],
    "policy_support": ["정책", "지원", "policy", "지원사업", "지원금", "인증", "요건"],
}

DEFAULT_MAX_SOURCE_TEXT = 4000


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _to_bool_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    v = str(value).strip().lower()
    if v in {"0", "false", "f", "no", "n", "off", "비활성"}:
        return "false"
    return "true"


def _detect_domain(text: str, filename: str) -> str:
    lowered_filename = filename.lower()
    for keyword, domain in PDF_HINT_BY_FILENAME.items():
        if keyword in lowered_filename:
            return domain

    lowered_text = (text or "").lower()
    best_domain = "market_size"
    best_score = 0
    for domain, hints in DOMAIN_HINTS.items():
        score = 0
        for hint in hints:
            if hint.lower() in lowered_text:
                score += 1
        if score > best_score:
            best_domain = domain
            best_score = score
    return best_domain


def _split_lines(raw_text: str) -> List[str]:
    lines: List[str] = []
    for row in raw_text.splitlines():
        row = row.strip()
        if row:
            lines.append(row)
    return lines


def _safe_ocr_from_pdf_page(path: Path, page_no: int, dpi: int = 180) -> Tuple[str, str, int]:
    """
    스캔 PDF 대응 OCR.
    pytesseract/pdf2image가 설치되지 않은 환경에서는 빈 문자열 반환.
    """
    try:
        from pdf2image import convert_from_path
        import pytesseract
    except Exception:
        return "", "missing_ocr_dependencies", 0

    try:
        images = convert_from_path(
            str(path),
            dpi=dpi,
            first_page=page_no,
            last_page=page_no,
            fmt="png",
            thread_count=1,
        )
    except Exception:
        return "", "ocr_convert_error", 0

    if not images:
        return "", "ocr_no_image", 0

    try:
        text = pytesseract.image_to_string(images[0], lang="kor+eng")
        return (text or "").strip(), "", 1
    except Exception:
        return "", "ocr_runtime_error", 0


def _infer_tag(text: str, domain: str) -> str:
    lowered = (f"{text}").lower()
    if domain == "market_size":
        if "해외" in lowered or "global" in lowered:
            return "market_overview_global"
        return "market_overview"
    if domain == "industry_trends":
        if "해외" in lowered or "global" in lowered:
            return "trend_global"
        return "industry_trend"
    if domain == "competitor_info":
        if "사례" in lowered or "사례소개" in lowered:
            return "competitor_examples"
        return "competitor_list"
    return "policy_support"


def _calc_confidence(line_count: int, text_len: int, has_domain: bool) -> int:
    score = 20
    if has_domain:
        score += 20
    if line_count >= 2:
        score += 15
    if text_len >= 120:
        score += 20
    if text_len >= 500:
        score += 15
    return min(100, score)


def _make_row(
    page_no: int,
    path: Path,
    text: str,
    industry_code: str,
    source_type: str = "pdf_text",
    ocr_engine: str = "pdf_text",
    ocr_error: str = "",
) -> Dict[str, Any]:
    lines = _split_lines(text)
    content = " ".join(lines).strip()
    domain = _detect_domain(content, path.name)
    title = lines[0].strip()[:120] if lines else f"{path.stem} p{page_no}"
    confidence = _calc_confidence(len(lines), len(content), bool(domain))
    if source_type == "pdf_ocr":
        confidence = max(15, min(100, int(confidence * 0.75)))

    return {
        "domain": domain,
        "title": title,
        "industry_code": industry_code,
        "tag": _infer_tag(content, domain),
        "source_url": "",
        "source_text": content[:DEFAULT_MAX_SOURCE_TEXT] or f"{path.name} 텍스트 추출 실패",
        "payload_json": json.dumps(
            {
                "source_file": path.name,
                "page": page_no,
                "source_type": source_type,
                "ocr_engine": ocr_engine,
                "ocr_error": ocr_error,
                "parsed_at": datetime.utcnow().isoformat(),
                "source_lines": len(lines),
            },
            ensure_ascii=False,
        ),
        "is_active": _to_bool_text(True),
        "parsed_confidence": confidence,
        "need_review": "true" if confidence < 55 else "false",
        "source_file": path.name,
        "page": page_no,
    }


def _read_pdf_rows(path: Path, industry_code: str, use_ocr: bool = False) -> List[Dict[str, Any]]:
    try:
        from pypdf import PdfReader
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("pypdf 미설치: requirements.txt + pip install pypdf") from exc

    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise RuntimeError(f"PDF 열기 실패: {path}") from exc

    rows: List[Dict[str, Any]] = []
    for page_no, page in enumerate(reader.pages, start=1):
        raw_text = (page.extract_text() or "").strip()
        source_type = "pdf_text"
        ocr_engine = "none"
        ocr_error = ""
        ocr_retries = 0
        if not raw_text and use_ocr:
            ocr_text, ocr_error, ocr_retries = _safe_ocr_from_pdf_page(path, page_no)
            if not ocr_text:
                ocr_engine = "pdf2image+ocr_failed"
            else:
                source_type = "pdf_ocr"
                ocr_engine = "pytesseract"
            raw_text = ocr_text
        else:
            ocr_engine = "pypdf"
        if not raw_text:
            continue
        if source_type == "pdf_ocr":
            ocr_error = ocr_error or ""

        row = _make_row(
            page_no=page_no,
            path=path,
            text=raw_text,
            industry_code=industry_code,
            source_type=source_type,
            ocr_engine=ocr_engine,
            ocr_error=ocr_error,
        )
        row["payload_json"] = json.loads(row["payload_json"])
        row["payload_json"]["ocr_retries"] = ocr_retries
        row["payload_json"] = json.dumps(row["payload_json"], ensure_ascii=False)
        if row["domain"] in ALLOWED_DOMAINS:
            rows.append(row)

    if not rows:
        # 텍스트 추출이 안되는 페이지가 있어도 최소 1개 레코드 생성해 추적 가능하게 함
        rows.append(
            {
                "domain": "market_size",
                "title": f"{path.name} 텍스트 추출 없음",
                "industry_code": industry_code,
                "tag": "market_overview",
                "source_url": "",
                "source_text": f"{path.name}에서 텍스트를 추출하지 못했습니다.",
                "payload_json": json.dumps(
                    {"source_file": path.name, "source_type": "pdf_empty"},
                    ensure_ascii=False,
                ),
                "is_active": _to_bool_text(True),
                "parsed_confidence": 10,
                "need_review": "true",
                "source_file": path.name,
                "page": 0,
            }
        )
    return rows


def read_source(source: Path, industry_code: str, use_ocr: bool = False) -> List[Dict[str, Any]]:
    if source.is_dir():
        pdfs = sorted(source.glob("*.pdf"))
        if not pdfs:
            raise FileNotFoundError(f"PDF 파일이 없습니다: {source}")
    else:
        if source.suffix.lower() != ".pdf":
            raise ValueError(f"PDF 파일만 지원됩니다: {source}")
        if not source.exists():
            raise FileNotFoundError(f"PDF 파일이 없습니다: {source}")
        pdfs = [source]

    rows: List[Dict[str, Any]] = []
    for p in pdfs:
        rows.extend(
            _read_pdf_rows(
                path=p,
                industry_code=industry_code,
                use_ocr=use_ocr,
            )
        )
    return rows


def _normalize_row(row: Dict[str, Any], strict: bool) -> Dict[str, Any] | None:
    if row.get("domain") not in ALLOWED_DOMAINS:
        return None
    if not _normalize_text(row.get("title")):
        return None

    need_review = str(row.get("need_review", "")).strip().lower()
    if strict and need_review in {"1", "true", "t", "yes", "y", "on"}:
        return None

    confidence = row.get("parsed_confidence") or 0
    try:
        confidence = int(float(confidence))
    except Exception:
        confidence = 0
    row["parsed_confidence"] = str(confidence)
    if strict and confidence < 30:
        return None

    return {k: _normalize_text(v) if k != "is_active" else _to_bool_text(v) for k, v in row.items()}


def write_csv(rows: Iterable[Dict[str, Any]], output: Path) -> int:
    headers = [
        "domain",
        "title",
        "industry_code",
        "tag",
        "source_url",
        "source_text",
        "payload_json",
        "is_active",
        "parsed_confidence",
        "need_review",
        "source_file",
        "page",
    ]
    with output.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        count = 0
        for row in rows:
            row_out = {k: row.get(k, "") for k in headers}
            writer.writerow(row_out)
            count += 1
    return count


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, help="PDF 파일 또는 PDF 폴더")
    parser.add_argument("--output", required=True, help="출력 CSV 경로")
    parser.add_argument("--industry-code", default="IT")
    parser.add_argument("--include-empty", action="store_true", help="빈 추출 건도 CSV에 포함")
    parser.add_argument(
        "--ocr",
        action="store_true",
        help="텍스트 추출 실패 페이지에 대해 OCR 시도(pytesseract/pdf2image 필요)",
    )
    parser.add_argument(
        "--min-confidence",
        type=int,
        default=0,
        help="미만 행 필터링 기준(0이면 필터 없음). strict 모드에서만 동작.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="정합성 미달 행은 스킵 (need_review 또는 parsed_confidence<30)",
    )
    args = parser.parse_args(argv)

    source_path = Path(args.source).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()

    rows_raw = read_source(source=source_path, industry_code=args.industry_code, use_ocr=args.ocr)
    rows = [r for r in (_normalize_row(r, strict=args.strict) for r in rows_raw) if r]

    if args.include_empty:
        rows = rows_raw  # 운영자가 빈 레코드도 확인 원할 때만 사용
        rows = [r for r in rows if _normalize_row(r, strict=False)]

    if args.min_confidence and args.strict:
        rows = [r for r in rows if int(r.get("parsed_confidence") or 0) >= args.min_confidence]

    wrote = write_csv(rows, output_path)
    if not wrote:
        raise RuntimeError("정규화 결과가 0건입니다. 입력 PDF/파라미터를 점검하세요.")

    print(f"[OK] PDF 정규화 완료: {output_path} (rows={wrote})")


if __name__ == "__main__":
    main()
