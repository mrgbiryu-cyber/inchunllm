from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.services.growth_v1_controls import POLICY_VERSION_V1, SECTION_SCHEMA_V1
from app.models.company import CompanyProfile
from app.core.config import settings
from app.services.templates.template_form_mapping import TEMPLATE_REQUIRED_FIELDS, normalize_form_fields


class BusinessPlanAgent:
    """Generates structured business plan output for new or existing businesses."""

    def _build_sections_for_new(self, profile: CompanyProfile) -> List[Dict[str, str]]:
        return [
            {
                "title": "문제 정의",
                "content": f"{profile.item_description}를 통해 해결하려는 시장 문제를 정리하고 고객 세그먼트를 명확히 정의합니다.",
            },
            {
                "title": "솔루션 및 BM",
                "content": "핵심 제공가치, 수익모델, 초기 유료전환 가설을 수립합니다.",
            },
            {
                "title": "실행 전략",
                "content": "초기 파일럿 고객 확보, MVP 개발, 정책지원사업 연계를 중심으로 실행 계획을 작성합니다.",
            },
        ]

    def _build_sections_for_existing(self, profile: CompanyProfile, input_text: str) -> List[Dict[str, str]]:
        excerpt = (input_text or "")[:500]
        return [
            {
                "title": "현황 분석",
                "content": f"기존 사업계획 텍스트를 기반으로 현재 사업현황을 요약합니다: {excerpt}",
            },
            {
                "title": "보완 포인트",
                "content": "정책 기준/인증 요건 대비 누락 항목(증빙, 성과지표, 실행일정)을 보완합니다.",
            },
            {
                "title": "재구성 계획",
                "content": "시장-기술-조직-재무 항목을 재정렬해 심사 친화적인 문서 구조로 재구성합니다.",
            },
        ]

    @staticmethod
    def _normalize_text(value: Optional[str]) -> str:
        return (value or "확인 필요").strip()

    @staticmethod
    def _company_type_display(raw: Optional[str]) -> str:
        mapping = {
            "PRE_ENTREPRENEUR": "예비창업자",
            "EARLY_STAGE": "초기기업",
            "GROWTH_STAGE": "성장기업",
            "TRANSITION": "전환단계",
        }
        value = (raw or "").strip()
        return mapping.get(value, value or "미분류")

    @staticmethod
    def _section_label(section_key: str) -> str:
        return section_key.replace("_", " ").strip()

    @staticmethod
    def _to_html(text: str) -> str:
        safe_text = (
            text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\r\n", "\n")
        )
        return f"<p>{safe_text.replace(chr(10), '<br/>')}</p>"

    @staticmethod
    def _first_nonempty(*values: Optional[str]) -> str:
        for value in values:
            candidate = (value or "").strip()
            if candidate:
                return candidate
        return ""

    @staticmethod
    def _extract_first_group(text: str, patterns: List[str]) -> str:
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match and match.group(1):
                value = match.group(1).strip()
                if value:
                    return value
        return ""

    def _extract_form_fields_from_text(self, text: str) -> Dict[str, str]:
        raw = (text or "").strip()
        if not raw:
            return {}

        fields: Dict[str, str] = {}
        normalized = raw.replace("\r\n", "\n")
        field_patterns: Dict[str, List[str]] = {
            "company_name": [
                r"(?:회사명|기업명)\s*(?:은|는|:)?\s*([^\n,]+)",
            ],
            "representative_name": [
                r"(?:대표자명|대표자|대표)\s*(?:은|는|:)?\s*([^\n,]+)",
            ],
            "business_registration_no": [
                r"(?:사업자등록번호|사업자번호)\s*(?:은|는|:)?\s*([0-9\-\*]{6,})",
            ],
            "established_date": [
                r"(?:설립일|설립년도|설립연도|창업일)\s*(?:은|는|:)?\s*([0-9]{2,4}[년\.\-/\s0-9월일]*)",
            ],
            "address": [
                r"(?:주소|소재지)\s*(?:은|는|:)?\s*([^\n,]+)",
            ],
            "contact_phone": [
                r"(?:연락처|전화번호|휴대폰)\s*(?:은|는|:)?\s*([0-9\-\+]{8,})",
            ],
            "email": [
                r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})",
            ],
            "main_business": [
                r"(?:주요사업|주력사업|사업내용|아이템)\s*(?:은|는|:)?\s*([^\n]+)",
            ],
            "technology_field": [
                r"(?:기술분야|주력기술|기술 분야|기술)\s*(?:은|는|:)?\s*([^\n,]+)",
                r"(?:업종|산업)\s*(?:은|는|:)?\s*([^\n,]+)",
            ],
            "employee_count": [
                r"(?:종사자수|직원수|고용인원|인력)\s*(?:은|는|:)?\s*([0-9]{1,4})\s*명?",
            ],
            "recent_revenue": [
                r"(?:최근매출|매출액|매출|연매출)\s*(?:은|는|:)?\s*([0-9,\.]+(?:\s*(?:원|만원|억원|백만원))?)",
            ],
            "government_fund": [
                r"(?:정부지원금)\s*(?:은|는|:)?\s*([0-9,\.]+(?:\s*(?:원|만원|억원|백만원))?)",
            ],
            "matching_fund": [
                r"(?:대응자금|자부담)\s*(?:은|는|:)?\s*([0-9,\.]+(?:\s*(?:원|만원|억원|백만원))?)",
            ],
            "total_budget": [
                r"(?:총사업비|총예산|합계)\s*(?:은|는|:)?\s*([0-9,\.]+(?:\s*(?:원|만원|억원|백만원))?)",
            ],
            "current_sales": [
                r"(?:현재\s*매출)\s*(?:은|는|:)?\s*([0-9,\.]+(?:\s*(?:원|만원|억원|백만원))?)",
            ],
            "current_export": [
                r"(?:현재\s*수출)\s*(?:은|는|:)?\s*([0-9,\.]+(?:\s*(?:달러|천달러|만달러))?)",
            ],
            "current_investment": [
                r"(?:현재\s*투자)\s*(?:은|는|:)?\s*([0-9,\.]+(?:\s*(?:원|만원|억원|백만원))?)",
            ],
            "target_sales": [
                r"(?:목표\s*매출)\s*(?:은|는|:)?\s*([0-9,\.]+(?:\s*(?:원|만원|억원|백만원))?)",
            ],
            "target_export": [
                r"(?:목표\s*수출)\s*(?:은|는|:)?\s*([0-9,\.]+(?:\s*(?:달러|천달러|만달러))?)",
            ],
            "target_investment": [
                r"(?:목표\s*투자)\s*(?:은|는|:)?\s*([0-9,\.]+(?:\s*(?:원|만원|억원|백만원))?)",
            ],
            "current_employment": [
                r"(?:현재\s*고용)\s*(?:은|는|:)?\s*([0-9]{1,4})\s*명?",
            ],
            "target_employment": [
                r"(?:목표\s*고용)\s*(?:은|는|:)?\s*([0-9]{1,4})\s*명?",
            ],
        }

        for key, patterns in field_patterns.items():
            value = self._extract_first_group(normalized, patterns)
            if value:
                fields[key] = value

        return normalize_form_fields(fields)

    @staticmethod
    def _to_one_line(value: Optional[str], limit: int = 300) -> str:
        text = (value or "").replace("\r\n", "\n").strip()
        if not text:
            return ""
        line = " ".join(part.strip() for part in text.split("\n") if part.strip())
        return line[:limit]

    async def _extract_form_fields_with_llm(self, raw_text: str, target_keys: List[str]) -> Dict[str, str]:
        if not raw_text or not target_keys:
            return {}
        if not settings.OPENROUTER_API_KEY:
            return {}
        try:
            from langchain_openai import ChatOpenAI
            from langchain_core.messages import HumanMessage, SystemMessage
        except Exception:
            return {}

        system_prompt = (
            settings.BUSINESS_PLAN_FIELD_EXTRACTION_SYSTEM_PROMPT
            or (
                "당신은 한국어 사업계획서 폼 입력 추출기입니다.\n"
                "입력 텍스트에서 요청된 키만 찾아 JSON으로 반환하세요.\n"
                "형식: {\"fields\": {\"key\": \"value\"}}\n"
                "값이 확실하지 않으면 빈 문자열을 넣고, 키는 절대 추가/삭제/변경하지 마세요."
            )
        )
        user_prompt = (
            f"대상 키: {json.dumps(target_keys, ensure_ascii=False)}\n"
            f"텍스트:\n{raw_text[:4000]}"
        )

        try:
            llm = ChatOpenAI(
                model=settings.BUSINESS_PLAN_FIELD_EXTRACTION_MODEL,
                api_key=settings.OPENROUTER_API_KEY,
                base_url=settings.OPENROUTER_BASE_URL,
                temperature=0.0,
            )
            response = await llm.ainvoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ])
            parsed = self._extract_json_object((response.content or "").strip())
            if not isinstance(parsed, dict):
                return {}
            fields = parsed.get("fields")
            if not isinstance(fields, dict):
                return {}
            result: Dict[str, str] = {}
            for key in target_keys:
                value = fields.get(key)
                if value is None:
                    continue
                text_value = str(value).strip()
                if text_value:
                    result[key] = text_value
            return normalize_form_fields(result)
        except Exception:
            return {}

    async def _build_form_fields(
        self,
        profile: CompanyProfile,
        growth_stage: str,
        sections_markdown: Dict[str, str],
        input_text: str = "",
    ) -> Dict[str, str]:
        profile_metadata = profile.metadata if isinstance(profile.metadata, dict) else {}
        slot_texts = [
            str(profile_metadata.get("company_profile_raw") or ""),
            str(profile_metadata.get("target_problem_raw") or ""),
            str(profile_metadata.get("revenue_funding_raw") or ""),
            str(profile_metadata.get("financing_topic_raw") or ""),
        ]
        merged_slot_text = "\n".join(t for t in slot_texts if t.strip())

        fields: Dict[str, str] = {}
        for text in slot_texts:
            parsed = self._extract_form_fields_from_text(text)
            for key, value in parsed.items():
                fields.setdefault(key, value)

        profile_defaults = {
            "company_name": (profile.company_name or "").strip(),
            "employee_count": str(profile.employee_count) if profile.employee_count else "",
            "recent_revenue": (
                f"{int(profile.last_fiscal_year_revenue):,}원"
                if profile.last_fiscal_year_revenue
                else (f"{int(profile.annual_revenue):,}원" if profile.annual_revenue else "")
            ),
            "technology_field": str(profile_metadata.get("industry") or "").strip(),
            "main_business": (profile.item_description or "").strip(),
        }
        for key, value in profile_defaults.items():
            if value and not fields.get(key):
                fields[key] = value

        section_map_by_stage: Dict[str, Dict[str, str]] = {
            "예비": {
                "pain_point": "problem_2_need_for_development",
                "market_analysis": "problem_1_market_status_and_issues",
                "implementation_plan": "solution_1_development_plan",
                "differentiation": "solution_2_differentiation_competitiveness",
                "funding_plan": "scaleup_3_funding_investment_strategy",
                "social_value": "scaleup_4_roadmap_and_social_value_plan",
                "founder_experience": "team_1_founder_capability",
                "hiring_plan": "team_2_team_members_and_hiring_plan",
                "external_partnership": "team_3_assets_facilities_and_partners",
                "item_name": "summary_overview",
            },
            "초기": {
                "pain_point": "problem_1_background_and_necessity",
                "target_market": "problem_2_target_market_and_requirements",
                "implementation_plan": "solution_1_preparation_status",
                "differentiation": "solution_2_realization_and_detail_plan",
                "funding_plan": "scaleup_3_schedule_and_fund_plan_roadmap",
                "hiring_plan": "team_2_current_hires_and_hiring_plan",
                "external_partnership": "team_3_external_partners",
                "social_value": "team_4_esg_mid_long_term_plan",
                "item_name": "summary_overview",
            },
            "성장": {
                "pain_point": "problem_1_tasks_to_solve",
                "differentiation": "problem_2_competitor_gap_tasks",
                "target_market": "problem_3_customer_needs_tasks",
                "implementation_plan": "solution_1_dev_improve_plan_and_schedule",
                "funding_plan": "scaleup_1_fund_need_and_financing",
                "exit_strategy": "scaleup_4_exit_strategy_investment_ma_ipo_gov",
                "founder_experience": "team_1_founder_and_staff_capabilities_and_hiring",
                "external_partnership": "team_2_partners_and_collaboration",
                "social_value": "team_4_social_value_and_performance_sharing",
                "item_name": "product_service_summary",
            },
            "공통": {
                "social_mission": "plan_1_business_purpose",
                "main_business": "plan_2_business_content_and_revenue",
                "founder_experience": "plan_3_business_capability",
                "funding_plan": "plan_4_business_goals",
                "roadmap_after_designation": "other_execution_plan",
                "social_service_type": "cert_eligibility_1_social_purpose_type",
                "governance_structure": "cert_eligibility_2_org_form_and_governance",
                "vulnerable_employment_plan": "cert_eligibility_3_employment_plan",
                "articles_change_plan": "cert_eligibility_4_articles_and_rules",
            },
        }
        fallback_map = section_map_by_stage.get(growth_stage, {})
        for field_key, section_key in fallback_map.items():
            value = self._to_one_line(sections_markdown.get(section_key))
            if value and not fields.get(field_key):
                fields[field_key] = value

        if growth_stage == "초기":
            core_source = self._to_one_line(sections_markdown.get("summary_overview"), limit=600)
            if core_source:
                chunks = [c.strip() for c in re.split(r"[.;]\s+|•|\n", core_source) if c.strip()]
                if chunks:
                    fields.setdefault("core_advantage_1", chunks[0][:120])
                if len(chunks) > 1:
                    fields.setdefault("core_advantage_2", chunks[1][:120])
                if len(chunks) > 2:
                    fields.setdefault("core_advantage_3", chunks[2][:120])

        required_keys = TEMPLATE_REQUIRED_FIELDS.get(f"business_plan:{growth_stage}", [])
        missing_keys = [key for key in required_keys if not (fields.get(key) or "").strip()]
        if missing_keys:
            llm_raw_text = "\n".join(
                [
                    merged_slot_text,
                    input_text or "",
                    "\n".join(
                        f"{k}: {self._to_one_line(v, limit=400)}"
                        for k, v in sections_markdown.items()
                        if isinstance(v, str) and v.strip()
                    ),
                ]
            ).strip()
            llm_fields = await self._extract_form_fields_with_llm(llm_raw_text, missing_keys)
            for key, value in llm_fields.items():
                if value and not fields.get(key):
                    fields[key] = value

        return normalize_form_fields(fields)

    async def _build_growth_sections_payload(
        self,
        growth_stage: str,
        profile: CompanyProfile,
        research_context: Optional[Dict[str, Any]] = None,
        progress_callback: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> tuple[Dict[str, str], Dict[str, str]]:
        required_keys = SECTION_SCHEMA_V1.get("business_plan", {}).get(growth_stage, []) or []
        if not required_keys:
            required_keys = SECTION_SCHEMA_V1.get("business_plan", {}).get("예비", [])

        research_data = research_context.get("data", {}) if isinstance(research_context, dict) else {}
        research_notes = {
            "market_size": self._summarize_research(research_data.get("market_size", {})),
            "industry_trends": self._summarize_research(research_data.get("industry_trends", {})),
            "competitor_info": self._summarize_research(research_data.get("competitor_info", {})),
            "policy_support": self._summarize_research(research_data.get("policy_support", {})),
        }

        company_desc = self._normalize_text(profile.item_description)
        profile_metadata = profile.metadata if isinstance(profile.metadata, dict) else {}
        industry = self._normalize_text(profile_metadata.get("industry") or "")
        team = self._normalize_text(profile_metadata.get("team") or "")
        company_type = self._company_type_display(profile.classified_type.value if profile.classified_type else "UNKNOWN")
        company_name = self._normalize_text(profile.company_name)

        generated = await self._generate_sections_with_llm(
            required_keys=required_keys,
            growth_stage=growth_stage,
            company_name=company_name,
            company_type=company_type,
            industry=industry,
            team=team,
            company_desc=company_desc,
            research_notes=research_notes,
            progress_callback=progress_callback,
        )

        sections_markdown: Dict[str, str] = {}
        sections_html: Dict[str, str] = {}
        for key in required_keys:
            content = (generated.get(key) or "").strip()
            if not content:
                key_research = self._research_append_for_key(key, research_notes)
                label = self._section_label(key)
                content = (
                    f"{company_desc}를 기준으로 {label}에 대한 실행 내용을 작성합니다.\n"
                    f"기업 개요: 회사명 {company_name}, 업종 {industry}, 팀 구성 {team}.\n"
                    f"{key_research}"
                ).strip()
            sections_markdown[key] = content
            sections_html[key] = self._to_html(content)

        return sections_markdown, sections_html

    async def _generate_sections_with_llm(
        self,
        required_keys: List[str],
        growth_stage: str,
        company_name: str,
        company_type: str,
        industry: str,
        team: str,
        company_desc: str,
        research_notes: Dict[str, str],
        progress_callback: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> Dict[str, str]:
        try:
            from langchain_openai import ChatOpenAI
            from langchain_core.messages import HumanMessage, SystemMessage
        except Exception:
            return {}

        if not settings.OPENROUTER_API_KEY:
            return {}

        async def _emit_progress(step_message: str) -> None:
            if progress_callback:
                await progress_callback(step_message)

        def _extract_sections(payload: Dict[str, Any] | None) -> Dict[str, str]:
            if not isinstance(payload, dict):
                return {}
            sections = payload.get("sections")
            if not isinstance(sections, dict):
                return {}
            result: Dict[str, str] = {}
            for key in required_keys:
                value = sections.get(key)
                if value is None:
                    continue
                clean = str(value).strip()
                if clean:
                    result[key] = clean
            return result

        async def _invoke_json_sections(
            model_name: str,
            system_prompt: str,
            user_prompt: str,
            temperature: float = 0.25,
        ) -> Dict[str, str]:
            llm = ChatOpenAI(
                model=model_name,
                api_key=settings.OPENROUTER_API_KEY,
                base_url=settings.OPENROUTER_BASE_URL,
                temperature=temperature,
            )
            response = await llm.ainvoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ])
            parsed = self._extract_json_object((response.content or "").strip())
            return _extract_sections(parsed)

        section_guidance = "\n".join(
            f"- {key}: {self._research_append_for_key(key, research_notes)[:600]}"
            for key in required_keys
        )
        draft_system_prompt = (
            settings.BUSINESS_PLAN_DRAFT_SYSTEM_PROMPT
            or (
                "당신은 한국 정부 지원사업 제출용 사업계획서 작성 전문가입니다.\n"
                "반드시 한국어로 작성하고, 섹션별로 서로 다른 구체 내용을 작성하세요.\n"
                "금지: 섹션 키 이름 반복, 템플릿 변수명 노출, '정리합니다' 같은 메타 문장 반복.\n"
                "각 섹션은 4~7문장으로 작성하고, 실무 문체로 작성하세요.\n"
                "출력은 JSON만 반환하세요. 형식: {\"sections\": {\"키\": \"본문\"}}\n"
            )
        )
        draft_user_prompt = (
            "당신은 한국 정부 지원사업 제출용 사업계획서 작성 전문가입니다.\n"
            f"성장단계: {growth_stage}\n"
            f"회사명: {company_name}\n"
            f"기업유형: {company_type}\n"
            f"업종: {industry}\n"
            f"팀 구성: {team}\n"
            f"핵심 아이템/문제: {company_desc}\n\n"
            f"섹션 키:\n{json.dumps(required_keys, ensure_ascii=False)}\n\n"
            f"조사 근거 요약:\n{section_guidance}\n\n"
            "위 입력만으로 작성 가능한 범위에서 사실성 있게 문장을 구성하세요."
        )

        await _emit_progress("초안 작성 중")
        draft_sections = await _invoke_json_sections(
            model_name=settings.BUSINESS_PLAN_DRAFT_MODEL,
            system_prompt=draft_system_prompt,
            user_prompt=draft_user_prompt,
            temperature=0.35,
        )
        if not draft_sections:
            return {}

        polish_system_prompt = (
            settings.BUSINESS_PLAN_POLISH_SYSTEM_PROMPT
            or (
                "당신은 한국어 사업계획서 문장 편집자입니다.\n"
                "입력 JSON의 sections를 그대로 유지하되, 문장만 명확하고 간결하게 다듬으세요.\n"
                "섹션 키는 절대 추가/삭제/변경하지 말고 JSON만 출력하세요."
            )
        )
        polish_user_prompt = (
            "아래 사업계획서 초안 섹션 문장을 다듬어 주세요.\n"
            f"섹션 키 목록: {json.dumps(required_keys, ensure_ascii=False)}\n"
            f"초안 JSON: {json.dumps({'sections': draft_sections}, ensure_ascii=False)}"
        )
        await _emit_progress("문장 다듬기 중")
        polished_sections = await _invoke_json_sections(
            model_name=settings.BUSINESS_PLAN_POLISH_MODEL,
            system_prompt=polish_system_prompt,
            user_prompt=polish_user_prompt,
            temperature=0.2,
        ) or draft_sections

        format_system_prompt = (
            settings.BUSINESS_PLAN_FORMAT_SYSTEM_PROMPT
            or (
                "당신은 사업계획서 포맷 정리 전문가입니다.\n"
                "sections JSON의 키는 그대로 유지하고, 문단 구성/숫자 표기/문장 흐름을 제출용으로 최종 정리하세요.\n"
                "반드시 JSON만 출력하세요."
            )
        )
        format_user_prompt = (
            "아래 문장을 최종 제출 포맷으로 정리해 주세요.\n"
            f"섹션 키 목록: {json.dumps(required_keys, ensure_ascii=False)}\n"
            f"입력 JSON: {json.dumps({'sections': polished_sections}, ensure_ascii=False)}"
        )
        await _emit_progress("최종 포맷 변환 중")
        formatted_sections = await _invoke_json_sections(
            model_name=settings.BUSINESS_PLAN_FORMAT_MODEL,
            system_prompt=format_system_prompt,
            user_prompt=format_user_prompt,
            temperature=0.15,
        )
        return formatted_sections or polished_sections or draft_sections

    @staticmethod
    def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
        if not text:
            return None
        content = text.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?", "", content).strip()
            content = re.sub(r"```$", "", content).strip()
        try:
            return json.loads(content)
        except Exception:
            match = re.search(r"\{[\s\S]*\}", content)
            if not match:
                return None
            try:
                return json.loads(match.group(0))
            except Exception:
                return None

    @staticmethod
    def _summarize_research(raw: Any) -> str:
        if not isinstance(raw, dict):
            return ""
        summary = (raw.get("summary") or "").strip()
        if summary:
            return summary[:1000]
        return ""

    @staticmethod
    def _research_append_for_key(section_key: str, research_notes: Dict[str, str]) -> str:
        mapping = {
            "problem_1_market_status_and_issues": "market_size",
            "problem_2_need_for_development": "industry_trends",
            "solution_1_development_plan": "industry_trends",
            "scaleup_1_competitor_analysis_and_entry_strategy": "competitor_info",
            "scaleup_2_business_model_revenue": "competitor_info",
            "scaleup_3_funding_investment_strategy": "policy_support",
            "scaleup_4_roadmap_and_social_value_plan": "policy_support",
            "scaleup_5_schedule_full_phases": "policy_support",
            "application_status": "policy_support",
            "solution_1_preparation_status": "industry_trends",
            "solution_2_realization_and_detail_plan": "industry_trends",
            "team_1_org_and_capabilities": "policy_support",
            "team_2_current_hires_and_hiring_plan": "policy_support",
            "team_3_external_partners": "competitor_info",
            "team_4_esg_mid_long_term_plan": "policy_support",
            "product_service_summary": "competitor_info",
            "problem_1_tasks_to_solve": "market_size",
            "problem_2_competitor_gap_tasks": "competitor_info",
            "problem_3_customer_needs_tasks": "industry_trends",
            "solution_1_dev_improve_plan_and_schedule": "industry_trends",
            "solution_2_customer_requirements_response": "market_size",
            "solution_3_competitiveness_strengthening": "competitor_info",
            "scaleup_1_fund_need_and_financing": "policy_support",
            "scaleup_2_market_entry_and_results_domestic": "industry_trends",
            "scaleup_3_market_entry_and_results_global": "industry_trends",
            "scaleup_4_exit_strategy_investment_ma_ipo_gov": "policy_support",
            "team_1_founder_and_staff_capabilities_and_hiring": "policy_support",
            "team_2_partners_and_collaboration": "competitor_info",
            "team_3_rnd_capability_and_security": "policy_support",
            "team_4_social_value_and_performance_sharing": "policy_support",
            "company_overview": "policy_support",
            "cert_eligibility_1_social_purpose_type": "policy_support",
            "cert_eligibility_2_org_form_and_governance": "policy_support",
            "cert_eligibility_3_employment_plan": "policy_support",
            "cert_eligibility_4_articles_and_rules": "policy_support",
            "plan_1_business_purpose": "industry_trends",
            "plan_2_business_content_and_revenue": "market_size",
            "plan_3_business_capability": "competitor_info",
            "plan_4_business_goals": "policy_support",
            "post_designation_1_year_plan": "policy_support",
            "post_designation_2_year_plan": "policy_support",
            "post_designation_3_year_plan": "policy_support",
            "other_execution_plan": "policy_support",
        }
        source_key = mapping.get(section_key)
        if not source_key:
            return ""
        note = research_notes.get(source_key)
        if not note:
            return ""
        return f"[자동 조사 반영]\n{note}"

    def _build_business_plan_sections(self, company_type: str) -> List[Dict[str, str]]:
        return [
            {
                "title": "문제 정의",
                "content": f"{company_type} 기반의 사업 아이템과 해결 과제를 중심으로 시장 문제를 정의합니다.",
            },
            {
                "title": "솔루션 및 BM",
                "content": "핵심 가치제안, 수익모델, 초기 고객 반응을 반영한 실행 가능한 비즈니스 모델을 수립합니다.",
            },
            {
                "title": "실행 전략",
                "content": "기술/영업/운영 기반의 실행 전략과 우선순위를 정리해 정책지원사업과 연계를 고려합니다.",
            },
        ]

    async def generate_or_reconstruct(
        self,
        profile: CompanyProfile,
        input_text: str = "",
        policy_version: Optional[str] = None,
        growth_stage: Optional[str] = None,
        research_context: Optional[Dict[str, Any]] = None,
        progress_callback: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        if policy_version == POLICY_VERSION_V1 and growth_stage:
            sections_markdown, sections_html = await self._build_growth_sections_payload(
                growth_stage,
                profile,
                research_context=research_context,
                progress_callback=progress_callback,
            )
            form_fields = await self._build_form_fields(
                profile=profile,
                growth_stage=growth_stage,
                sections_markdown=sections_markdown,
                input_text=input_text,
            )
            is_new = profile.classified_type and profile.classified_type.value == "PRE_ENTREPRENEUR"
            research_summary = "\n".join(
                f"- {k}: {self._summarize_research(v)}"
                for k, v in (research_context or {}).get("data", {}).items()
                if isinstance(v, dict) and self._summarize_research(v)
            )
            return {
                "mode": "generate" if is_new else "reconstruct",
                "title": "사업계획서 초안" if is_new else "사업계획서 재구성안",
                "company_name": self._normalize_text(profile.company_name),
                "company_type": (
                    self._normalize_text((profile.metadata or {}).get("industry"))
                    if isinstance(profile.metadata, dict) and (profile.metadata or {}).get("industry")
                    else self._company_type_display(profile.classified_type.value if profile.classified_type else "UNKNOWN")
                ),
                "growth_stage": growth_stage,
                "sections": self._build_business_plan_sections(
                    profile.classified_type.value if profile.classified_type else "UNKNOWN"
                ),
                "sections_markdown": sections_markdown,
                "sections_html": sections_html,
                "form_fields": form_fields,
                "research": research_summary,
                "analysis": {
                    "needs": ["기술 명확화", "성과지표 정의", "로드맵 정합성"],
                    "risk_flags": ["근거 데이터 부족"],
                },
            }

        is_new = profile.classified_type and profile.classified_type.value == "PRE_ENTREPRENEUR"
        sections = self._build_sections_for_new(profile) if is_new else self._build_sections_for_existing(profile, input_text)

        return {
            "mode": "generate" if is_new else "reconstruct",
            "title": "사업계획서 초안" if is_new else "사업계획서 재구성안",
            "company_name": self._normalize_text(profile.company_name),
            "company_type": profile.classified_type.value if profile.classified_type else "UNKNOWN",
            "growth_stage": profile.classified_stage.value if profile.classified_stage else "UNKNOWN",
            "sections": sections,
            "analysis": {
                "needs": ["기술 명확화", "성과지표 정의", "로드맵 정합성"],
                "risk_flags": ["근거 데이터 부족"],
            },
        }
