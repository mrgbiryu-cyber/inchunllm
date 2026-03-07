from __future__ import annotations

from typing import Dict, List, Tuple


FIELD_INPUT_GUIDES: Dict[str, str] = {
    "company_name": "기업명을 알려주세요.",
    "representative_name": "대표자명을 알려주세요.",
    "business_registration_no": "사업자등록번호를 알려주세요.",
    "established_date": "설립일(또는 설립연도)을 알려주세요.",
    "address": "사업장 주소를 알려주세요.",
    "contact_phone": "대표 연락처를 알려주세요.",
    "email": "대표 이메일을 알려주세요.",
    "main_business": "주요 사업(제품/서비스)을 알려주세요.",
    "technology_field": "주력 기술 분야를 알려주세요.",
    "item_name": "핵심 아이템명을 알려주세요.",
    "employee_count": "현재 종사자 수를 알려주세요.",
    "recent_revenue": "최근 매출(원/만원 단위)을 알려주세요.",
    "government_fund": "정부지원금 금액을 알려주세요.",
    "matching_fund": "대응자금/자부담 금액을 알려주세요.",
    "total_budget": "총 사업예산을 알려주세요.",
    "pain_point": "해결하려는 핵심 문제(Pain Point)를 알려주세요.",
    "market_analysis": "목표 시장/고객 분석 내용을 알려주세요.",
    "implementation_plan": "구현(개발) 계획을 알려주세요.",
    "differentiation": "경쟁사 대비 차별화 포인트를 알려주세요.",
    "funding_plan": "자금 조달/투자 계획을 알려주세요.",
    "social_value": "사회적 가치 또는 ESG 실천 계획을 알려주세요.",
    "founder_experience": "대표자 핵심 경력/전문성을 알려주세요.",
    "hiring_plan": "채용 계획을 알려주세요.",
    "external_partnership": "외부 협력 계획(멘토/기관/투자사)을 알려주세요.",
    "target_market": "목표 시장을 알려주세요.",
    "core_advantage_1": "핵심 특장점 1개를 알려주세요.",
    "core_advantage_2": "핵심 특장점 2개를 알려주세요.",
    "core_advantage_3": "핵심 특장점 3개를 알려주세요.",
    "current_employment": "현재 고용 인원을 알려주세요.",
    "current_sales": "현재 매출액을 알려주세요.",
    "current_export": "현재 수출액을 알려주세요.",
    "current_investment": "현재 투자 유치액을 알려주세요.",
    "target_employment": "목표 고용 인원을 알려주세요.",
    "target_sales": "목표 매출액을 알려주세요.",
    "target_export": "목표 수출액을 알려주세요.",
    "target_investment": "목표 투자 유치액을 알려주세요.",
    "exit_strategy": "EXIT 전략(M&A/IPO/기타)을 알려주세요.",
    "social_mission": "사회적 미션/해결하려는 사회문제를 알려주세요.",
    "social_service_type": "사회적 목적 유형(사회서비스/일자리/지역사회/혼합/기타)을 알려주세요.",
    "governance_structure": "민주적 의사결정 구조를 알려주세요.",
    "vulnerable_employment_plan": "취약계층 고용 계획을 알려주세요.",
    "articles_change_plan": "정관/규약 변경 계획을 알려주세요.",
    "roadmap_after_designation": "지정 이후 단계별 추진 로드맵을 알려주세요.",
    "main_revenue_source": "주요 수익원을 알려주세요.",
    "certification_status": "기업 인증 보유 현황을 알려주세요.",
    "ip_status": "지식재산권 보유 현황을 알려주세요.",
    "rnd_status": "R&D 조직/활동 현황을 알려주세요.",
    "investment_status": "투자 유치 현황을 알려주세요.",
}


TEMPLATE_REQUIRED_FIELDS: Dict[str, List[str]] = {
    "business_plan:예비": [
        "company_name",
        "representative_name",
        "contact_phone",
        "email",
        "technology_field",
        "item_name",
        "pain_point",
        "market_analysis",
        "implementation_plan",
        "differentiation",
        "funding_plan",
        "founder_experience",
    ],
    "business_plan:초기": [
        "company_name",
        "representative_name",
        "established_date",
        "address",
        "recent_revenue",
        "item_name",
        "technology_field",
        "core_advantage_1",
        "pain_point",
        "implementation_plan",
        "target_market",
        "hiring_plan",
    ],
    "business_plan:성장": [
        "company_name",
        "representative_name",
        "business_registration_no",
        "established_date",
        "address",
        "contact_phone",
        "current_employment",
        "current_sales",
        "current_export",
        "current_investment",
        "funding_plan",
        "exit_strategy",
    ],
    "business_plan:공통": [
        "company_name",
        "representative_name",
        "address",
        "contact_phone",
        "main_business",
        "social_service_type",
        "governance_structure",
        "vulnerable_employment_plan",
        "social_mission",
        "roadmap_after_designation",
    ],
    "bm_diagnosis:공통": [
        "company_name",
        "representative_name",
        "business_registration_no",
        "established_date",
        "address",
        "contact_phone",
        "main_business",
        "employee_count",
        "recent_revenue",
        "main_revenue_source",
        "certification_status",
        "ip_status",
        "rnd_status",
        "investment_status",
    ],
}


def resolve_template_code(artifact_type: str, stage: str) -> str:
    return f"{(artifact_type or '').strip()}:{(stage or '').strip()}"


def normalize_form_fields(fields: Dict[str, object] | None) -> Dict[str, str]:
    normalized: Dict[str, str] = {}
    if not isinstance(fields, dict):
        return normalized
    for key, value in fields.items():
        k = str(key).strip()
        if not k:
            continue
        if value is None:
            continue
        v = str(value).strip()
        if not v:
            continue
        normalized[k] = v
    return normalized


def compute_missing_field_guides(template_code: str, form_fields: Dict[str, str]) -> Tuple[List[str], List[str]]:
    required = TEMPLATE_REQUIRED_FIELDS.get(template_code, [])
    missing_keys: List[str] = []
    guides: List[str] = []
    for key in required:
        value = (form_fields.get(key) or "").strip()
        if value:
            continue
        missing_keys.append(key)
        guides.append(FIELD_INPUT_GUIDES.get(key, f"{key} 값을 알려주세요."))
    return missing_keys, guides
