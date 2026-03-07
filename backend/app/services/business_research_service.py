# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from sqlalchemy import cast, String

from app.core.config import settings
from app.core.database import (
    AsyncSessionLocal,
    ProjectResearchRunModel,
    ProjectResearchSourceModel,
    ProjectResearchSnapshotModel,
    ResearchKnowledgeDomain,
    ResearchStaticReferenceModel,
    ResearchStaticSourceType,
)
from app.core.search_client import search_client
from app.models.company import CompanyProfile
from app.services.growth_v1_controls import get_project_policy_version


class BusinessResearchService:
    """Collect research payloads in fixed order and persist to DB."""

    @staticmethod
    def _naive_now() -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)


    SOURCE_ORDER = (
        ResearchStaticSourceType.PUBLIC_API,
        ResearchStaticSourceType.STATIC_DB,
        ResearchStaticSourceType.USER_INPUT,
        ResearchStaticSourceType.LLM_SUPPORT,
    )

    DOMAINS = {
        ResearchKnowledgeDomain.MARKET_SIZE: {
            "query_template": "{company_name} {industry_code} 시장 규모",
            "llm_prompt": "시장 규모(TAM/SAM/SOM) 관점으로 근거형 요약을 작성",
            "count": 3,
        },
        ResearchKnowledgeDomain.INDUSTRY_TRENDS: {
            "query_template": "{company_name} {industry_code} 산업 동향 최신 트렌드",
            "llm_prompt": "산업 동향/규제/기술 변화 포인트를 3~5개 요약",
            "count": 4,
        },
        ResearchKnowledgeDomain.COMPETITOR_INFO: {
            "query_template": "{industry_code} 유사상품 경쟁사 주요 경쟁사 분석",
            "llm_prompt": "경쟁사 현황(대표사례/강점/차별점) 3개 이상 제안",
            "count": 4,
        },
        ResearchKnowledgeDomain.POLICY_SUPPORT: {
            "query_template": "{industry_code} 정책지원사업 지원요건 인증요건",
            "llm_prompt": "정책 지원 요건 및 신청 포인트를 실행 항목 중심으로 요약",
            "count": 4,
        },
    }

    @staticmethod
    def _coalesce_text(value: Any, default: str = "") -> str:
        if value is None:
            return default
        if isinstance(value, str):
            return value.strip()
        return str(value).strip()

    def _to_query(self, profile: CompanyProfile, domain: str) -> str:
        cfg = self.DOMAINS.get(domain, {})
        tpl = cfg.get("query_template", "{company_name} {industry_code} {domain}")
        return tpl.format(
            company_name=self._coalesce_text(profile.company_name, "기업"),
            industry_code=self._coalesce_text(profile.industry_code, "산업"),
            domain=domain,
        )

    async def _collect_public(self, profile: CompanyProfile, domain: str) -> List[Dict[str, Any]]:
        query = self._to_query(profile, domain)
        results = await search_client.search(query, max_results=self.DOMAINS.get(domain, {}).get("count", 3))
        out: List[Dict[str, Any]] = []
        for item in results or []:
            out.append(
                {
                    "title": self._coalesce_text(item.get("title")),
                    "url": item.get("url"),
                    "snippet": self._coalesce_text(item.get("content") or item.get("description")),
                    "score": item.get("score"),
                }
            )
        return out

    async def _collect_static_db(self, domain: str, industry_code: Optional[str]) -> List[Dict[str, Any]]:
        async with AsyncSessionLocal() as session:
            from sqlalchemy import or_, select

            stmt = select(ResearchStaticReferenceModel).where(
                ResearchStaticReferenceModel.domain == domain,
                ResearchStaticReferenceModel.is_active.is_(True),
            )
            if industry_code:
                stmt = stmt.where(
                    or_(
                        ResearchStaticReferenceModel.industry_code == industry_code,
                        ResearchStaticReferenceModel.industry_code.is_(None),
                    )
                )
            stmt = stmt.order_by(
                ResearchStaticReferenceModel.updated_at.desc(),
                ResearchStaticReferenceModel.created_at.desc(),
            ).limit(5)

            rows = (await session.execute(stmt)).scalars().all()
            return [
                {
                    "source_id": str(row.id),
                    "title": row.title,
                    "url": row.source_url,
                    "tag": row.tag,
                    "text": row.source_text,
                    "payload": row.payload_json or {},
                }
                for row in rows
            ]

    async def _collect_user_input(
        self,
        domain: str,
        manual_inputs: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not manual_inputs:
            return []
        raw = manual_inputs.get(domain) or manual_inputs.get(str(domain))
        if raw is None:
            return []
        if isinstance(raw, str):
            text = raw.strip()
            return [{"text": text}] if text else []
        if isinstance(raw, list):
            return [{"text": self._coalesce_text(v)} for v in raw if self._coalesce_text(v)]
        if isinstance(raw, dict):
            return [raw]
        return [{"text": self._coalesce_text(raw)}]

    async def _collect_llm_boost(
        self,
        profile: CompanyProfile,
        domain: str,
        fallback_items: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not (settings.OPENROUTER_API_KEY and settings.OPENROUTER_BASE_URL):
            return {
                "status": "skipped",
                "summary": "LLM API 미연결로 인한 보강 생략",
                "items": fallback_items,
            }

        cfg = self.DOMAINS.get(domain, {})
        prompt = (
            f"항목: {domain}\n"
            f"프로필: 회사명={self._coalesce_text(profile.company_name, '미입력')} "
            f"산업={self._coalesce_text(profile.industry_code, '미입력')}\n"
            f"요구: {cfg.get('llm_prompt')}\n"
            f"기초데이터: {json.dumps(fallback_items, ensure_ascii=False)}"
        )
        llm = ChatOpenAI(
            model=settings.LLM_LOW_TIER_MODEL,
            api_key=settings.OPENROUTER_API_KEY,
            base_url=settings.OPENROUTER_BASE_URL,
            temperature=0.1,
        )
        response = await llm.ainvoke([
            SystemMessage(
                content=(
                    "사업기획/인증 준비 전문가로서 한국어로만 답변."
                    "결과는 근거형 bullet 중심 3~5줄, 불명확 항목은 '[추가확인]' 표시."
                )
            ),
            HumanMessage(content=prompt),
        ])
        text = self._coalesce_text(response.content, "")
        if not text:
            return {"status": "empty", "summary": "보강 결과 없음", "items": []}
        return {
            "status": "enriched",
            "summary": text[:1200],
            "items": fallback_items or [{"text": text[:900]}],
        }

    @staticmethod
    def _confidence_from_sources(sources: List[str]) -> float:
        score_map = {
            ResearchStaticSourceType.PUBLIC_API: 0.55,
            ResearchStaticSourceType.STATIC_DB: 0.35,
            ResearchStaticSourceType.USER_INPUT: 0.3,
            ResearchStaticSourceType.LLM_SUPPORT: 0.2,
        }
        score = sum(score_map.get(s, 0.0) for s in sources)
        return 0.0 if score <= 0 else min(1.0, score / 1.6)

    async def _create_run(self, project_id: str, policy_version: str, request_payload: dict) -> str:
        async with AsyncSessionLocal() as session:
            run = ProjectResearchRunModel(
                id=uuid.uuid4(),
                project_id=project_id,
                policy_version=policy_version,
                request_payload=request_payload,
                status="running",
            )
            session.add(run)
            await session.commit()
            return str(run.id)

    async def _append_source(
        self,
        run_id: str,
        project_id: str,
        domain: str,
        source_type: str,
        payload: Dict[str, Any],
        is_success: bool,
        source_ref: Optional[str] = None,
        confidence: float = 0.0,
        error: Optional[str] = None,
    ) -> None:
        async with AsyncSessionLocal() as session:
            session.add(
                ProjectResearchSourceModel(
                    run_id=run_id,
                    project_id=project_id,
                    domain=domain,
                    source_type=source_type,
                    source_ref=source_ref,
                    source_version="v1",
                    payload_json=payload,
                    is_success=is_success,
                    confidence=confidence,
                    error_message=error,
                )
            )
            await session.commit()

    async def _set_run_status(self, run_id: str, status: str) -> None:
        async with AsyncSessionLocal() as session:
            from sqlalchemy import select

            run = (
                await session.execute(
                    select(ProjectResearchRunModel).where(
                        (ProjectResearchRunModel.id == run_id) | (cast(ProjectResearchRunModel.id, String) == str(run_id))
                    )
                )
            ).scalar_one_or_none()
            if run:
                run.status = status
                run.finished_at = self._naive_now()
                await session.commit()

    async def _save_snapshot(self, project_id: str, policy_version: str, domain: str, payload: Dict[str, Any]) -> None:
        async with AsyncSessionLocal() as session:
            from sqlalchemy import select

            existing = (
                await session.execute(
                    select(ProjectResearchSnapshotModel).where(
                        ProjectResearchSnapshotModel.project_id == project_id,
                        ProjectResearchSnapshotModel.domain == domain,
                    )
                )
            ).scalar_one_or_none()
            if existing:
                existing.summary_json = {
                    "summary": payload.get("summary"),
                    "items": payload.get("items", []),
                    "status": payload.get("status"),
                }
                existing.sources_used = payload.get("sources", [])
                existing.collected_at = self._naive_now()
                existing.policy_version = policy_version
            else:
                session.add(
                    ProjectResearchSnapshotModel(
                        project_id=project_id,
                        policy_version=policy_version,
                        domain=domain,
                        summary_json={
                            "summary": payload.get("summary"),
                            "items": payload.get("items", []),
                            "status": payload.get("status"),
                        },
                        sources_used=payload.get("sources", []),
                    )
                )
            await session.commit()

    async def collect_for_project(
        self,
        project_id: str,
        profile: CompanyProfile,
        manual_inputs: Optional[Dict[str, Any]] = None,
        requested_domains: Optional[List[str]] = None,
        requested_sources: Optional[List[str]] = None,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        if requested_sources is None:
            requested_sources = list(self.SOURCE_ORDER)

        if requested_domains is None:
            requested_domains = [
                ResearchKnowledgeDomain.MARKET_SIZE,
                ResearchKnowledgeDomain.INDUSTRY_TRENDS,
                ResearchKnowledgeDomain.COMPETITOR_INFO,
                ResearchKnowledgeDomain.POLICY_SUPPORT,
            ]

        policy_version = await get_project_policy_version(project_id)
        request_payload = {
            "project_id": project_id,
            "manual_inputs": bool(manual_inputs),
            "requested_domains": requested_domains,
            "requested_sources": requested_sources,
            "force_refresh": force_refresh,
            "created_at": self._naive_now().isoformat(),
        }
        run_id = await self._create_run(project_id, policy_version, request_payload)

        data: Dict[str, Any] = {}
        sources_used = []

        try:
            for domain in requested_domains:
                domain_payload = {
                    "domain": domain,
                    "summary": "",
                    "items": [],
                    "sources": [],
                    "status": "not_found",
                    "confidence": 0.0,
                }
                source_hits: List[Dict[str, Any]] = []
                selected_sources: List[str] = []

                for source_type in requested_sources:
                    if source_type == ResearchStaticSourceType.LLM_SUPPORT:
                        continue
                    try:
                        if source_type == ResearchStaticSourceType.PUBLIC_API:
                            source_items = await self._collect_public(profile, domain)
                        elif source_type == ResearchStaticSourceType.STATIC_DB:
                            source_items = await self._collect_static_db(domain, profile.industry_code)
                        elif source_type == ResearchStaticSourceType.USER_INPUT:
                            source_items = await self._collect_user_input(domain, manual_inputs or {})
                        else:
                            source_items = []

                        if source_items:
                            selected_sources.append(source_type)
                            source_hits.extend(source_items)
                            domain_payload["status"] = "partial"

                        await self._append_source(
                            run_id=run_id,
                            project_id=project_id,
                            domain=domain,
                            source_type=source_type,
                            source_ref=f"{domain}:{source_type}" if source_items else None,
                            payload={
                                "items": source_items,
                                "count": len(source_items),
                                "status": "ok" if source_items else "empty",
                            },
                            is_success=bool(source_items),
                            confidence=1.0 if source_items else 0.0,
                        )
                        sources_used.append(
                            {"domain": domain, "source_type": source_type, "count": len(source_items)}
                        )
                    except Exception as exc:  # noqa: BLE001
                        await self._append_source(
                            run_id=run_id,
                            project_id=project_id,
                            domain=domain,
                            source_type=source_type,
                            source_ref=f"{domain}:{source_type}:error",
                            payload={"items": [], "count": 0, "status": "error"},
                            is_success=False,
                            confidence=0.0,
                            error=str(exc),
                        )

                if not source_hits and ResearchStaticSourceType.LLM_SUPPORT in requested_sources:
                    llm_result = await self._collect_llm_boost(profile, domain, source_hits)
                    if llm_result.get("items"):
                        source_hits.extend(llm_result["items"])
                        selected_sources.append(ResearchStaticSourceType.LLM_SUPPORT)
                    domain_payload["status"] = llm_result.get("status", "enriched")
                    domain_payload["summary"] = llm_result.get("summary", "")
                    await self._append_source(
                        run_id=run_id,
                        project_id=project_id,
                        domain=domain,
                        source_type=ResearchStaticSourceType.LLM_SUPPORT,
                        source_ref="llm_boost",
                        payload=llm_result,
                        is_success=bool(llm_result.get("items")),
                        confidence=0.2,
                    )
                elif source_hits:
                    domain_payload["status"] = "ok"
                    domain_payload["summary"] = " | ".join(
                        item.get("title") or item.get("text") or "" for item in source_hits[:3]
                    ).strip()[:900]

                if not domain_payload["summary"]:
                    domain_payload["summary"] = f"{domain} 관련 근거를 추가 입력/확인 필요"

                domain_payload["items"] = source_hits
                domain_payload["sources"] = selected_sources
                domain_payload["confidence"] = self._confidence_from_sources(selected_sources)
                data[domain] = domain_payload
                await self._save_snapshot(project_id, policy_version, domain, domain_payload)

            await self._set_run_status(run_id, "success")
        except Exception:
            await self._set_run_status(run_id, "failed")
            raise

        return {
            "run_id": run_id,
            "project_id": project_id,
            "policy_version": policy_version,
            "data": data,
            "sources_used": sources_used,
        }


business_research_service = BusinessResearchService()
