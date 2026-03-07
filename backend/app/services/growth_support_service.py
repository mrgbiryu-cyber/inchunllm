from __future__ import annotations

import json
import re
from html import unescape
from typing import Any, Awaitable, Callable, Dict, Optional

from fastapi.encoders import jsonable_encoder

from app.core.database import get_latest_growth_artifact, get_latest_growth_run, save_growth_run
from app.models.company import CompanyProfile
from app.services.agents.business_plan_agent import BusinessPlanAgent
from app.services.agents.classification_agent import ClassificationAgent
from app.services.agents.matching_agent import MatchingAgent
from app.services.agents.roadmap_agent import RoadmapAgent
from app.services.business_research_service import business_research_service
from app.services.templates.artifact_renderer import (
    render_matching_html,
    render_roadmap_html,
)
from app.services.growth_v1_controls import (
    POLICY_VERSION_V1,
    SECTION_SCHEMA_V1,
    get_project_policy_version,
    require_pdf_approval,
    set_question_type_from_profile,
    validate_growth_mode_policy,
    render_business_plan_with_template,
    _to_html,
)
from app.services.templates.pdf_renderer import render_pdf_from_html
from app.services.templates.template_form_mapping import normalize_form_fields


class GrowthSupportService:
    """E2E growth support pipeline with in-memory cache and DB persistence."""

    def __init__(self):
        self.classifier = ClassificationAgent()
        self.plan_agent = BusinessPlanAgent()
        self.matching_agent = MatchingAgent()
        self.roadmap_agent = RoadmapAgent()
        self.artifact_cache: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _looks_like_markdown(text: str) -> bool:
        body = (text or "").strip()
        if not body:
            return False
        patterns = [
            r"^\s{0,3}#{1,6}\s+\S+",
            r"^\s*>+\s+\S+",
            r"^\s*[-*+]\s+\S+",
            r"^\s*\d+\.\s+\S+",
            r"^\s*\|.+\|\s*$",
            r"\n\s*\|[-:\s|]+\|\s*\n",
        ]
        return any(re.search(pattern, body, re.MULTILINE) for pattern in patterns)

    def _normalize_html_artifact(self, html_content: Any) -> str:
        text = str(html_content or "")
        if not text:
            return text

        lower = text.lower()
        if "<html" not in lower and "<body" not in lower and self._looks_like_markdown(text):
            return _to_html(text)

        old_wrapper = re.search(
            r"<html><body[^>]*>(?P<body>[\s\S]*?)</body></html>",
            text,
            flags=re.IGNORECASE,
        )
        if old_wrapper:
            body = old_wrapper.group("body")
            markdown_candidate = re.sub(r"<br\s*/?>", "\n", body, flags=re.IGNORECASE)
            markdown_candidate = re.sub(r"<[^>]+>", "", markdown_candidate)
            markdown_candidate = unescape(markdown_candidate).strip()
            if self._looks_like_markdown(markdown_candidate):
                return _to_html(markdown_candidate)

        return text

    async def run_pipeline(
        self,
        project_id: str,
        profile: CompanyProfile,
        input_text: str = "",
        research_request: Optional[Dict[str, Any]] = None,
        progress_callback: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        async def _emit_progress(step_message: str) -> None:
            if progress_callback:
                await progress_callback(step_message)

        await _emit_progress("분류 중")
        # v1.0은 성장단계/연도 기준으로 상담 모드 자동 반영
        annual_revenue = getattr(profile, "annual_revenue", 0)
        growth_mode = await set_question_type_from_profile(
            project_id=project_id,
            annual_revenue=annual_revenue,
            classified_stage=getattr(profile.classified_stage, "value", None),
        )
        policy_version = await get_project_policy_version(project_id)

        research = None
        if policy_version == POLICY_VERSION_V1:
            await _emit_progress("자료 수집 중")
            req = research_request or {}
            research = await business_research_service.collect_for_project(
                project_id=project_id,
                profile=profile,
                manual_inputs=req.get("manual_inputs"),
                requested_domains=req.get("requested_domains"),
                requested_sources=req.get("requested_sources"),
                force_refresh=bool(req.get("force_refresh", False)),
            )

        classification = await self.classifier.analyze(profile)
        business_plan = await self.plan_agent.generate_or_reconstruct(
            profile,
            input_text=input_text,
            policy_version=policy_version,
            growth_stage=growth_mode,
            research_context=research,
            progress_callback=progress_callback,
        )
        plan_text = "\n".join([s.get("content", "") for s in business_plan.get("sections", [])])
        matching = await self.matching_agent.calculate_suitability(profile, plan_text)
        roadmap = await self.roadmap_agent.generate_roadmap(profile, matching)

        if policy_version == POLICY_VERSION_V1:
            validate_growth_mode_policy(growth_mode, roadmap=roadmap, matching=matching)

        rendered_business_plan = await render_business_plan_with_template(project_id, business_plan)
        business_plan_markdown = rendered_business_plan["markdown"]
        business_plan_html = rendered_business_plan["html"]
        bm_diagnosis_artifact = None

        if policy_version == POLICY_VERSION_V1:
            bm_plan = self._build_bm_diagnosis_payload(profile, research_context=research)
            rendered_bm = await render_business_plan_with_template(
                project_id,
                bm_plan,
                artifact_type="bm_diagnosis",
            )
            bm_diagnosis_artifact = {
                "json": bm_plan,
                "html": rendered_bm["html"],
                "markdown": rendered_bm["markdown"],
                "template_id": rendered_bm.get("template_id"),
                "missing_field_keys": rendered_bm.get("missing_field_keys", []),
                "missing_field_guides": rendered_bm.get("missing_field_guides", []),
            }

        artifacts = {
            "business_plan": {
                "json": business_plan,
                "html": business_plan_html,
                "markdown": business_plan_markdown,
                "template_id": rendered_business_plan.get("template_id"),
                "missing_field_keys": rendered_business_plan.get("missing_field_keys", []),
                "missing_field_guides": rendered_business_plan.get("missing_field_guides", []),
            },
            "matching": {
                "json": matching,
                "html": render_matching_html(matching),
                "markdown": self._to_markdown(matching),
            },
            "roadmap": {
                "json": roadmap,
                "html": render_roadmap_html(roadmap),
                "markdown": self._to_markdown(roadmap),
            },
        }
        if bm_diagnosis_artifact:
            artifacts["bm_diagnosis"] = bm_diagnosis_artifact

        payload = {
            "project_id": project_id,
            "classification": classification,
            "business_plan": business_plan,
            "bm_diagnosis": (bm_diagnosis_artifact or {}).get("json"),
            "matching": matching,
            "roadmap": roadmap,
            "consultation_mode": growth_mode,
            "research": research,
            "artifacts": artifacts,
        }
        payload = json.loads(json.dumps(jsonable_encoder(payload), ensure_ascii=False))

        self.artifact_cache[project_id] = payload
        await save_growth_run(project_id, result_json=payload, artifacts=artifacts)
        return payload

    async def get_latest(self, project_id: str) -> Dict[str, Any] | None:
        cached = self.artifact_cache.get(project_id)
        if cached:
            return cached
        return await get_latest_growth_run(project_id)

    async def get_artifact(
        self,
        project_id: str,
        artifact_type: str,
        format_name: str = "html",
        thread_id: Optional[str] = None,
    ) -> Any:
        artifact_type = (artifact_type or "").strip().lower()
        format_name = (format_name or "").strip().lower()
        data = await self.get_latest(project_id)
        if not data:
            raise KeyError("No pipeline result found")

        artifacts = data.get("artifacts", {})
        item = artifacts.get(artifact_type)
        if not item:
            raise KeyError(f"Artifact not found: {artifact_type}")

        if format_name == "pdf":
            if thread_id:
                await require_pdf_approval(
                    project_id=project_id,
                    artifact_type=artifact_type,
                    thread_id=thread_id,
                )
            else:
                await require_pdf_approval(project_id=project_id, artifact_type=artifact_type)
            html = item.get("html")
            if not html:
                html = await get_latest_growth_artifact(project_id, artifact_type, "html")
            if not html:
                raise KeyError("HTML source for PDF not found")
            html = self._normalize_html_artifact(html)
            return render_pdf_from_html(html)

        if format_name == "html":
            html = item.get("html")
            if not html:
                html = await get_latest_growth_artifact(project_id, artifact_type, "html")
            if not html:
                raise KeyError("HTML source not found")
            return self._normalize_html_artifact(html)

        if format_name not in item:
            stored = await get_latest_growth_artifact(project_id, artifact_type, format_name)
            if not stored:
                raise KeyError(f"Format not found: {format_name}")
            return stored

        return item[format_name]

    def _to_markdown(self, data: Dict[str, Any]) -> str:
        lines = ["# Generated Artifact", ""]
        for k, v in data.items():
            if isinstance(v, list):
                lines.append(f"## {k}")
                for row in v:
                    lines.append(f"- {row}")
            else:
                lines.append(f"- **{k}**: {v}")
        return "\n".join(lines)

    def _build_bm_diagnosis_payload(
        self,
        profile: CompanyProfile,
        research_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        section_keys = SECTION_SCHEMA_V1.get("bm_diagnosis", {}).get("공통", [])
        research_data = research_context.get("data", {}) if isinstance(research_context, dict) else {}
        fallback_desc = (profile.item_description or "BM 진단을 위한 기본 정보가 필요합니다.").strip()

        source_map = {
            "company_profile_core": "market_size",
            "business_and_financials": "industry_trends",
            "cert_ip_rnd_invest_esg": "policy_support",
            "support_items_checklist": "policy_support",
            "notes_and_consultant": "competitor_info",
        }
        section_label_map = {
            "company_profile_core": "기업 기본정보",
            "business_and_financials": "사업/재무/인력",
            "cert_ip_rnd_invest_esg": "인증/지재권/R&D/투자/ESG",
            "support_items_checklist": "지원항목 체크리스트",
            "notes_and_consultant": "의견 및 상담정보",
        }
        section_plain: Dict[str, str] = {}
        for key in section_keys:
            source_key = source_map.get(key)
            source_summary = ""
            if source_key and isinstance(research_data.get(source_key), dict):
                source_summary = str(research_data[source_key].get("summary") or "").strip()
            label = section_label_map.get(key, "진단 항목")
            base = f"{fallback_desc}를 기준으로 {label} 항목을 점검합니다."
            section_plain[key] = f"{base}\n{source_summary}".strip()

        type_map = {
            "PRE_ENTREPRENEUR": "예비창업자",
            "EARLY_STAGE": "초기기업",
            "GROWTH_STAGE": "성장기업",
            "TRANSITION": "전환단계",
        }
        raw_type = profile.classified_type.value if profile.classified_type else "UNKNOWN"
        company_type_display = type_map.get(raw_type, raw_type)
        profile_metadata = profile.metadata if isinstance(profile.metadata, dict) else {}
        profile_raw = str(profile_metadata.get("company_profile_raw") or "")
        extracted_fields = self._extract_kv_facts(profile_raw)
        form_fields = normalize_form_fields(
            {
                "company_name": profile.company_name or extracted_fields.get("company_name", ""),
                "representative_name": extracted_fields.get("representative_name", ""),
                "business_registration_no": extracted_fields.get("business_registration_no", ""),
                "established_date": extracted_fields.get("established_date", ""),
                "address": extracted_fields.get("address", ""),
                "contact_phone": extracted_fields.get("contact_phone", ""),
                "main_business": profile.item_description or extracted_fields.get("main_business", ""),
                "employee_count": (
                    str(profile.employee_count)
                    if profile.employee_count
                    else extracted_fields.get("employee_count", "")
                ),
                "recent_revenue": (
                    f"{int(profile.last_fiscal_year_revenue):,}원"
                    if profile.last_fiscal_year_revenue
                    else (
                        f"{int(profile.annual_revenue):,}원"
                        if profile.annual_revenue
                        else extracted_fields.get("recent_revenue", "")
                    )
                ),
                "main_revenue_source": extracted_fields.get("main_revenue_source", ""),
                "certification_status": ", ".join(profile.existing_certifications or []),
                "ip_status": ", ".join(profile.ip_assets or []),
                "rnd_status": (
                    "보유"
                    if profile.has_rnd_org is True
                    else ("미보유" if profile.has_rnd_org is False else "")
                ),
                "investment_status": extracted_fields.get("investment_status", ""),
            }
        )

        return {
            "title": "BM 진단 및 설계 양식",
            "company_name": profile.company_name or "회사명 미입력",
            "company_type": company_type_display,
            "growth_stage": "공통",
            "sections_markdown": {k: v for k, v in section_plain.items()},
            "sections_html": {k: f"<p>{v.replace(chr(10), '<br/>')}</p>" for k, v in section_plain.items()},
            "form_fields": form_fields,
            "analysis": {
                "needs": ["지원항목 매핑", "증빙 보강", "담당자 확인"],
                "risk_flags": [],
            },
        }

    @staticmethod
    def _extract_kv_facts(text: str) -> Dict[str, str]:
        raw = (text or "").strip()
        if not raw:
            return {}
        patterns = {
            "company_name": [r"(?:회사명|기업명)\s*(?:은|는|:)?\s*([^\n,]+)"],
            "representative_name": [r"(?:대표자명|대표자|대표)\s*(?:은|는|:)?\s*([^\n,]+)"],
            "business_registration_no": [r"(?:사업자등록번호|사업자번호)\s*(?:은|는|:)?\s*([0-9\-\*]{6,})"],
            "established_date": [r"(?:설립일|설립년도|설립연도)\s*(?:은|는|:)?\s*([0-9]{2,4}[년\.\-/\s0-9월일]*)"],
            "address": [r"(?:주소|소재지)\s*(?:은|는|:)?\s*([^\n,]+)"],
            "contact_phone": [r"(?:연락처|전화번호|휴대폰)\s*(?:은|는|:)?\s*([0-9\-\+]{8,})"],
            "main_business": [r"(?:주요사업|주력사업|사업내용)\s*(?:은|는|:)?\s*([^\n]+)"],
            "employee_count": [r"(?:종사자수|직원수|고용인원)\s*(?:은|는|:)?\s*([0-9]{1,4})\s*명?"],
            "recent_revenue": [r"(?:최근매출|매출액|연매출)\s*(?:은|는|:)?\s*([0-9,\.]+(?:\s*(?:원|만원|억원|백만원))?)"],
            "main_revenue_source": [r"(?:주요수익원|수익원)\s*(?:은|는|:)?\s*([^\n,]+)"],
            "investment_status": [r"(?:투자현황|투자유치)\s*(?:은|는|:)?\s*([^\n,]+)"],
        }
        result: Dict[str, str] = {}
        for key, candidates in patterns.items():
            for pattern in candidates:
                match = re.search(pattern, raw, flags=re.IGNORECASE)
                if match and match.group(1):
                    value = match.group(1).strip()
                    if value:
                        result[key] = value
                        break
        return result


growth_support_service = GrowthSupportService()
