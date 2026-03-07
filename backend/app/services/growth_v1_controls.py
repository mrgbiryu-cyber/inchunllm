from __future__ import annotations

import json
from html import escape
import asyncio
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from jinja2 import Environment, BaseLoader, StrictUndefined, select_autoescape, UndefinedError
try:
    import markdown as _py_markdown
except Exception:  # pragma: no cover - optional dependency fallback
    _py_markdown = None

from app.core.database import (
    AsyncSessionLocal,
    ConversationStateModel,
    ConversationStateThreadModel,
    ArtifactApprovalStateModel,
    GrowthTemplateModel,
    ensure_artifact_approval_columns,
    ensure_conversation_state_slot_columns,
)
from app.models.schemas import ConsultationMode, GrowthTemplate
from app.services.templates.artifact_renderer import render_business_plan_html
from app.services.templates.template_form_mapping import (
    TEMPLATE_REQUIRED_FIELDS,
    compute_missing_field_guides,
    normalize_form_fields,
    resolve_template_code,
)


CONSULTATION_MODE_PRELIMINARY = ConsultationMode.PRELIMINARY.value
CONSULTATION_MODE_EARLY = ConsultationMode.EARLY.value
CONSULTATION_MODE_GROWTH = ConsultationMode.GROWTH.value

POLICY_VERSION_LEGACY = "v0_legacy"
POLICY_VERSION_V1 = "v1_0"

REQUIRED_APPROVAL_STEPS = (
    "key_figures_approved",
    "certification_path_approved",
    "template_selected",
    "summary_confirmed",
)

APPROVAL_STEP_GUIDES = {
    "key_figures_approved": "매출/비용/자금 최신값을 알려주세요.",
    "certification_path_approved": "인증/지원 방향(충족·미충족·추가확인)을 알려주세요.",
    "template_selected": "원하는 양식(템플릿)을 알려주세요.",
    "summary_confirmed": "요약본 내용을 확인해 주세요. 맞으면 확정, 아니면 수정 요청해 주세요.",
}

_QUESTION_COUNTER_LOCKS: Dict[str, asyncio.Lock] = {}
_APPROVAL_STEP_LOCKS: Dict[str, asyncio.Lock] = {}

def _question_lock_key(project_id: str, thread_id: str | None = None) -> str:
    return f"{project_id}:{thread_id or 'project'}"


def _get_question_counter_lock(project_id: str, thread_id: str | None = None) -> asyncio.Lock:
    key = _question_lock_key(project_id, thread_id)
    lock = _QUESTION_COUNTER_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _QUESTION_COUNTER_LOCKS[key] = lock
    return lock


def _normalize_thread_scope(thread_id: str | None) -> str:
    return (thread_id or "").strip()


def _approval_lock_key(
    project_id: str,
    artifact_type: str,
    thread_id: str | None = None,
    requirement_version: int | None = None,
) -> str:
    thread_scope = _normalize_thread_scope(thread_id)
    version_scope = "*" if requirement_version is None else str(int(requirement_version))
    return f"{project_id}:{thread_scope}:{artifact_type}:{version_scope}"


def _get_approval_step_lock(
    project_id: str,
    artifact_type: str,
    thread_id: str | None = None,
    requirement_version: int | None = None,
) -> asyncio.Lock:
    key = _approval_lock_key(project_id, artifact_type, thread_id=thread_id, requirement_version=requirement_version)
    lock = _APPROVAL_STEP_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _APPROVAL_STEP_LOCKS[key] = lock
    return lock

QUESTION_LIMITS = {
    CONSULTATION_MODE_PRELIMINARY: {"required": 8, "optional": 3, "special": 1},
    CONSULTATION_MODE_EARLY: {"required": 7, "optional": 2, "special": 1},
    CONSULTATION_MODE_GROWTH: {"required": 6, "optional": 1, "special": 1},
}

QUESTION_LABELS = {
    "필수": "required",
    "필수항목": "required",
    "required": "required",
    "option": "optional",
    "optional": "optional",
    "선택": "optional",
    "extra": "special",
    "special": "special",
    "특이사항": "special",
}


GROWTH_MODE_POLICY_RULES = [
    "R&D",
    "전략",
    "투자",
    "M&A",
    "IPO",
    "엑싯",
    "엑시트",
    "M&A 확장",
]


SECTION_SCHEMA_V1 = {
    "business_plan": {
        "예비": [
            "general_status",
            "summary_overview",
            "problem_1_market_status_and_issues",
            "problem_2_need_for_development",
            "solution_1_development_plan",
            "solution_2_differentiation_competitiveness",
            "solution_3_gov_fund_execution_plan_stage1",
            "solution_4_gov_fund_execution_plan_stage2",
            "solution_5_schedule_within_agreement",
            "scaleup_1_competitor_analysis_and_entry_strategy",
            "scaleup_2_business_model_revenue",
            "scaleup_3_funding_investment_strategy",
            "scaleup_4_roadmap_and_social_value_plan",
            "scaleup_5_schedule_full_phases",
            "team_1_founder_capability",
            "team_2_team_members_and_hiring_plan",
            "team_3_assets_facilities_and_partners",
        ],
        "초기": [
            "application_status",
            "general_status",
            "summary_overview",
            "problem_1_background_and_necessity",
            "problem_2_target_market_and_requirements",
            "solution_1_preparation_status",
            "solution_2_realization_and_detail_plan",
            "scaleup_1_business_model_and_results",
            "scaleup_2_market_entry_and_strategy",
            "scaleup_3_schedule_and_fund_plan_roadmap",
            "scaleup_4_schedule_and_fund_plan_within_agreement",
            "scaleup_5_budget_execution_plan",
            "team_1_org_and_capabilities",
            "team_2_current_hires_and_hiring_plan",
            "team_3_external_partners",
            "team_4_esg_mid_long_term_plan",
        ],
        "성장": [
            "general_status",
            "product_service_summary",
            "problem_1_tasks_to_solve",
            "problem_2_competitor_gap_tasks",
            "problem_3_customer_needs_tasks",
            "solution_1_dev_improve_plan_and_schedule",
            "solution_2_customer_requirements_response",
            "solution_3_competitiveness_strengthening",
            "scaleup_1_fund_need_and_financing",
            "scaleup_2_market_entry_and_results_domestic",
            "scaleup_3_market_entry_and_results_global",
            "scaleup_4_exit_strategy_investment_ma_ipo_gov",
            "team_1_founder_and_staff_capabilities_and_hiring",
            "team_2_partners_and_collaboration",
            "team_3_rnd_capability_and_security",
            "team_4_social_value_and_performance_sharing",
        ],
    },
    "bm_diagnosis": {
        "공통": [
            "company_profile_core",
            "business_and_financials",
            "cert_ip_rnd_invest_esg",
            "support_items_checklist",
            "notes_and_consultant",
        ]
    },
}

_V1_TEMPLATE_RENDERER = Environment(
    loader=BaseLoader(),
    autoescape=select_autoescape(["html", "xml"]),
    undefined=StrictUndefined,
    auto_reload=False,
)


def _resolve_mode_limits(mode: str) -> Dict[str, int]:
    if mode not in QUESTION_LIMITS:
        mode = CONSULTATION_MODE_EARLY
    cfg = QUESTION_LIMITS[mode]
    return {
        "required": cfg["required"],
        "optional": cfg["optional"],
        "special": cfg["special"],
        "total": cfg["required"] + cfg["optional"] + cfg["special"],
    }


def _to_question_slot(raw_type: Optional[str]) -> Optional[str]:
    if not raw_type:
        return None
    return QUESTION_LABELS.get(raw_type.strip(), None)


def _build_question_snapshot(state: ConversationStateModel) -> dict:
    return {
        "consultation_mode": state.consultation_mode,
        "question_required_count": state.question_required_count,
        "question_optional_count": state.question_optional_count,
        "question_special_count": state.question_special_count,
        "question_total_count": state.question_total_count,
        "question_required_limit": state.question_required_limit,
        "question_optional_limit": state.question_optional_limit,
        "question_special_limit": state.question_special_limit,
    }


def _resolve_limits_from_state(state: ConversationStateModel) -> dict:
    if state.question_required_limit and state.question_optional_limit and state.question_special_limit:
        return {
            "required": state.question_required_limit,
            "optional": state.question_optional_limit,
            "special": state.question_special_limit,
            "total": state.question_required_limit + state.question_optional_limit + state.question_special_limit,
        }
    limits = _resolve_mode_limits(state.consultation_mode)
    state.question_required_limit = limits["required"]
    state.question_optional_limit = limits["optional"]
    state.question_special_limit = limits["special"]
    return limits


async def get_or_create_conversation_state(
    project_id: str,
    policy_version: str = POLICY_VERSION_LEGACY,
    thread_id: str | None = None,
) -> ConversationStateModel:
    if thread_id:
        async with AsyncSessionLocal() as session:
            row = (await session.execute(
                select(ConversationStateThreadModel).where(
                    ConversationStateThreadModel.project_id == project_id,
                    ConversationStateThreadModel.thread_id == thread_id,
                )
            )).scalar_one_or_none()
            if row:
                return row

            project_row = (await session.execute(
                select(ConversationStateModel).where(ConversationStateModel.project_id == project_id)
            )).scalar_one_or_none()

            base_mode = getattr(project_row, "consultation_mode", CONSULTATION_MODE_PRELIMINARY)
            base_policy = getattr(project_row, "policy_version", policy_version) if project_row else policy_version
            if base_policy not in {POLICY_VERSION_LEGACY, POLICY_VERSION_V1}:
                base_policy = POLICY_VERSION_LEGACY

            row = ConversationStateThreadModel(
                project_id=project_id,
                thread_id=thread_id,
                policy_version=base_policy,
                consultation_mode=base_mode,
                profile_stage=base_mode,
                active_mode=getattr(project_row, "active_mode", "NATURAL") if project_row else "NATURAL",
            )
            if row.policy_version == POLICY_VERSION_V1:
                limits = _resolve_mode_limits(row.consultation_mode)
                row.question_required_limit = limits["required"]
                row.question_optional_limit = limits["optional"]
                row.question_special_limit = limits["special"]
            session.add(row)
            await session.commit()
            return row

    async with AsyncSessionLocal() as session:
        row = (await session.execute(
            select(ConversationStateModel).where(ConversationStateModel.project_id == project_id)
        )).scalar_one_or_none()

        if row:
            return row

        # 프로젝트 컨설팅 모드는 정책과 무관하게 대화 진행 기본값으로 초기화
        init_mode = CONSULTATION_MODE_PRELIMINARY
        row = ConversationStateModel(
            project_id=project_id,
            policy_version=policy_version,
            consultation_mode=init_mode,
            profile_stage=init_mode,
            question_mode="server",
        )
        if policy_version == POLICY_VERSION_V1:
            limits = _resolve_mode_limits(init_mode)
            row.question_required_limit = limits["required"]
            row.question_optional_limit = limits["optional"]
            row.question_special_limit = limits["special"]
        session.add(row)
        await session.commit()
        return row


async def touch_plan_data_version(
    project_id: str,
    thread_id: str | None = None,
    invalidate_summary: bool = True,
) -> ConversationStateModel:
    """Conversation facts changed: bump version and optionally invalidate summary confirmation."""
    state = await get_or_create_conversation_state(
        project_id=project_id,
        policy_version=POLICY_VERSION_V1,
        thread_id=thread_id,
    )
    if not state:
        return None

    async with AsyncSessionLocal() as session:
        state = (await session.merge(state))

        state.plan_data_version = int(getattr(state, "plan_data_version", 0) or 0) + 1
        if invalidate_summary:
            state.summary_revision = 0

        await session.commit()
        return state


async def set_project_policy_version(
    project_id: str,
    policy_version: str,
    consultation_mode: Optional[str] = None,
    thread_id: str | None = None,
) -> ConversationStateModel:
    if thread_id:
        async with AsyncSessionLocal() as session:
            state = (await session.execute(
                select(ConversationStateThreadModel).where(
                    ConversationStateThreadModel.project_id == project_id,
                    ConversationStateThreadModel.thread_id == thread_id,
                )
            )).scalar_one_or_none()
            if not state:
                state = await get_or_create_conversation_state(
                    project_id=project_id,
                    policy_version=policy_version,
                    thread_id=thread_id,
                )
                session.merge(state)

            state.policy_version = policy_version
            if consultation_mode:
                state.consultation_mode = consultation_mode
                state.profile_stage = consultation_mode
                limits = _resolve_mode_limits(consultation_mode)
                state.question_required_limit = limits["required"]
                state.question_optional_limit = limits["optional"]
                state.question_special_limit = limits["special"]
                state.question_required_count = 0
                state.question_optional_count = 0
                state.question_special_count = 0
                state.question_total_count = 0
            elif not state.consultation_mode:
                state.consultation_mode = CONSULTATION_MODE_PRELIMINARY
                state.profile_stage = CONSULTATION_MODE_PRELIMINARY
            await session.commit()
            return state

    async with AsyncSessionLocal() as session:
        state = (await session.execute(
            select(ConversationStateModel).where(ConversationStateModel.project_id == project_id)
        )).scalar_one_or_none()

        if not state:
            state = ConversationStateModel(
                project_id=project_id,
                policy_version=policy_version,
            )
            session.add(state)

        state.policy_version = policy_version

        if consultation_mode:
            state.consultation_mode = consultation_mode
            state.profile_stage = consultation_mode
            limits = _resolve_mode_limits(consultation_mode)
            state.question_required_limit = limits["required"]
            state.question_optional_limit = limits["optional"]
            state.question_special_limit = limits["special"]
            state.question_required_count = 0
            state.question_optional_count = 0
            state.question_special_count = 0
            state.question_total_count = 0
        elif not state.consultation_mode:
            state.consultation_mode = CONSULTATION_MODE_PRELIMINARY
            state.profile_stage = CONSULTATION_MODE_PRELIMINARY

        await session.commit()
        return state


async def get_project_policy_state(
    project_id: str,
    thread_id: str | None = None,
) -> Optional[ConversationStateModel]:
    try:
        if thread_id:
            return await get_thread_policy_state(project_id, thread_id)

        async with AsyncSessionLocal() as session:
            stmt = select(ConversationStateModel).where(ConversationStateModel.project_id == project_id)
            return (await session.execute(stmt)).scalar_one_or_none()
    except SQLAlchemyError:
        return None


async def get_thread_policy_state(project_id: str, thread_id: str) -> Optional[ConversationStateModel]:
    try:
        async with AsyncSessionLocal() as session:
            row = (await session.execute(
                select(ConversationStateThreadModel).where(
                    ConversationStateThreadModel.project_id == project_id,
                    ConversationStateThreadModel.thread_id == thread_id,
                )
            )).scalar_one_or_none()
            return row
    except SQLAlchemyError:
        return None


async def get_project_or_thread_policy_state(project_id: str, thread_id: str | None = None):
    if thread_id:
        thread_state = await get_thread_policy_state(project_id, thread_id)
        if thread_state is not None:
            return thread_state
    return await get_project_policy_state(project_id)


async def get_project_policy_version(project_id: str, thread_id: str | None = None) -> str:
    state = await get_project_or_thread_policy_state(project_id, thread_id)
    if state and state.policy_version:
        return state.policy_version
    return POLICY_VERSION_LEGACY


def _normalize_profile_slots(raw: Any) -> Dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    normalized: Dict[str, str] = {}
    for key, value in raw.items():
        if value is None:
            continue
        k = str(key).strip()
        if not k:
            continue
        v = str(value).strip()
        if not v:
            continue
        normalized[k] = v
    return normalized


async def get_plan_profile_slots(
    project_id: str,
    thread_id: str | None = None,
) -> Dict[str, str]:
    await ensure_conversation_state_slot_columns()
    state = await get_or_create_conversation_state(
        project_id=project_id,
        policy_version=POLICY_VERSION_V1,
        thread_id=thread_id,
    )
    return _normalize_profile_slots(getattr(state, "profile_slots_json", {}) or {})


async def merge_plan_profile_slots(
    project_id: str,
    slot_updates: Dict[str, Any],
    thread_id: str | None = None,
    touch_plan_data_version: bool = True,
) -> Dict[str, str]:
    await ensure_conversation_state_slot_columns()
    updates = _normalize_profile_slots(slot_updates)
    state = await get_or_create_conversation_state(
        project_id=project_id,
        policy_version=POLICY_VERSION_V1,
        thread_id=thread_id,
    )
    merged = _normalize_profile_slots(getattr(state, "profile_slots_json", {}) or {})
    changed = False
    for key, value in updates.items():
        if merged.get(key) != value:
            merged[key] = value
            changed = True
    if not changed:
        return merged

    async with AsyncSessionLocal() as session:
        state = await session.merge(state)
        state.profile_slots_json = merged
        state.plan_suspended = False
        if touch_plan_data_version and state.policy_version == POLICY_VERSION_V1:
            state.plan_data_version = int(getattr(state, "plan_data_version", 0) or 0) + 1
            state.summary_revision = 0
        await session.commit()
    return merged


async def replace_plan_profile_slots(
    project_id: str,
    new_slots: Dict[str, Any],
    thread_id: str | None = None,
    touch_plan_data_version: bool = False,
) -> Dict[str, str]:
    await ensure_conversation_state_slot_columns()
    state = await get_or_create_conversation_state(
        project_id=project_id,
        policy_version=POLICY_VERSION_V1,
        thread_id=thread_id,
    )

    normalized: Dict[str, str] = {}
    for key, value in (new_slots or {}).items():
        k = str(key).strip()
        if not k:
            continue
        normalized[k] = "" if value is None else str(value).strip()

    async with AsyncSessionLocal() as session:
        state = await session.merge(state)
        state.profile_slots_json = normalized
        if touch_plan_data_version and state.policy_version == POLICY_VERSION_V1:
            state.plan_data_version = int(getattr(state, "plan_data_version", 0) or 0) + 1
            state.summary_revision = 0
        await session.commit()
    return normalized


async def set_plan_suspended(
    project_id: str,
    suspended: bool,
    thread_id: str | None = None,
) -> bool:
    await ensure_conversation_state_slot_columns()
    state = await get_or_create_conversation_state(
        project_id=project_id,
        policy_version=POLICY_VERSION_V1,
        thread_id=thread_id,
    )
    async with AsyncSessionLocal() as session:
        state = await session.merge(state)
        state.plan_suspended = bool(suspended)
        await session.commit()
    return bool(state.plan_suspended)


async def set_last_asked_slot(
    project_id: str,
    slot_name: Optional[str],
    thread_id: str | None = None,
) -> None:
    await ensure_conversation_state_slot_columns()
    state = await get_or_create_conversation_state(
        project_id=project_id,
        policy_version=POLICY_VERSION_V1,
        thread_id=thread_id,
    )
    async with AsyncSessionLocal() as session:
        state = await session.merge(state)
        state.last_asked_slot = slot_name
        await session.commit()


async def set_project_active_mode(
    project_id: str,
    active_mode: str,
    thread_id: str | None = None,
) -> Optional[ConversationStateModel]:
    """Persist runtime mode (NATURAL/REQUIREMENT/FUNCTION)."""
    if not active_mode:
        return await get_project_policy_state(project_id, thread_id=thread_id)

    normalized_mode = str(active_mode).strip().upper()
    if normalized_mode not in {"NATURAL", "REQUIREMENT", "FUNCTION"}:
        return await get_project_policy_state(project_id, thread_id=thread_id)

    if thread_id:
        async with AsyncSessionLocal() as session:
            state = (await session.execute(
                select(ConversationStateThreadModel).where(
                    ConversationStateThreadModel.project_id == project_id,
                    ConversationStateThreadModel.thread_id == thread_id,
                )
            )).scalar_one_or_none()
            if not state:
                state = await get_or_create_conversation_state(
                    project_id=project_id,
                    thread_id=thread_id,
                )
                session.merge(state)
            state.active_mode = normalized_mode
            await session.commit()
            return state

    async with AsyncSessionLocal() as session:
        state = (await session.execute(
            select(ConversationStateModel).where(ConversationStateModel.project_id == project_id)
        )).scalar_one_or_none()
        if not state:
            state = ConversationStateModel(
                project_id=project_id,
                policy_version=POLICY_VERSION_V1,
                consultation_mode=CONSULTATION_MODE_PRELIMINARY,
                profile_stage=CONSULTATION_MODE_PRELIMINARY,
                active_mode=normalized_mode,
            )
            session.add(state)
        else:
            state.active_mode = normalized_mode

        await session.commit()
        return state


async def set_project_consultation_mode(
    project_id: str,
    consultation_mode: str,
    thread_id: str | None = None,
) -> ConversationStateModel:
    if thread_id:
        async with AsyncSessionLocal() as session:
            state = (await session.execute(
                select(ConversationStateThreadModel).where(
                    ConversationStateThreadModel.project_id == project_id,
                    ConversationStateThreadModel.thread_id == thread_id,
                )
            )).scalar_one_or_none()
            if not state:
                state = await get_or_create_conversation_state(
                    project_id=project_id,
                    policy_version=POLICY_VERSION_V1,
                    thread_id=thread_id,
                )
                session.merge(state)
            state.consultation_mode = consultation_mode
            state.profile_stage = consultation_mode
            limits = _resolve_mode_limits(consultation_mode)
            state.question_required_limit = limits["required"]
            state.question_optional_limit = limits["optional"]
            state.question_special_limit = limits["special"]
            if state.question_mode == "legacy":
                state.question_mode = "server"
            state.question_required_count = 0
            state.question_optional_count = 0
            state.question_special_count = 0
            state.question_total_count = 0
            await session.commit()
            return state

    async with AsyncSessionLocal() as session:
        state = (await session.execute(
            select(ConversationStateModel).where(ConversationStateModel.project_id == project_id)
        )).scalar_one_or_none()
        if not state:
            state = ConversationStateModel(
                project_id=project_id,
                policy_version=POLICY_VERSION_LEGACY,
                consultation_mode=consultation_mode,
                profile_stage=consultation_mode,
            )
            session.add(state)
        else:
            state.consultation_mode = consultation_mode
            state.profile_stage = consultation_mode

        if state.policy_version == POLICY_VERSION_V1:
            limits = _resolve_mode_limits(consultation_mode)
            state.question_required_limit = limits["required"]
            state.question_optional_limit = limits["optional"]
            state.question_special_limit = limits["special"]
        if state.question_mode == "legacy":
            state.question_mode = "server"
        if state.policy_version == POLICY_VERSION_V1:
            # 모드 변경 시 질문 카운터 리셋(재질문 흐름에서 합의)
            state.question_required_count = 0
            state.question_optional_count = 0
            state.question_special_count = 0
            state.question_total_count = 0

        await session.commit()
        return state


async def update_question_counters(
    project_id: str,
    requested_question_type: Optional[str],
    thread_id: str | None = None,
    touch_plan_data_version: bool = False,
) -> dict:
    async with _get_question_counter_lock(project_id, thread_id):
        if thread_id:
            state = await get_or_create_conversation_state(project_id, thread_id=thread_id)
        else:
            state = await get_or_create_conversation_state(project_id)

        if state.policy_version != POLICY_VERSION_V1:
            return {
                "policy_version": state.policy_version,
                "requested_question_type": requested_question_type,
                "allocated_question_type": requested_question_type or "legacy",
                "consultation_mode": state.consultation_mode,
                **_build_question_snapshot(state),
            }

        limits = _resolve_limits_from_state(state)
        if not state.question_required_limit:
            state.question_required_limit = limits["required"]
        if not state.question_optional_limit:
            state.question_optional_limit = limits["optional"]
        if not state.question_special_limit:
            state.question_special_limit = limits["special"]

        mode = state.consultation_mode
        preferred = _to_question_slot(requested_question_type)

        def _available(slot: str) -> bool:
            if slot == "required":
                return state.question_required_count < state.question_required_limit
            if slot == "optional":
                return state.question_optional_count < state.question_optional_limit
            if slot == "special":
                return state.question_special_count < state.question_special_limit
            return False

        allocated = None
        if preferred and _available(preferred):
            allocated = preferred
        else:
            for slot in ["required", "optional", "special"]:
                if _available(slot):
                    allocated = slot
                    break

        if not allocated:
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "QUESTION_LIMIT_REACHED",
                    "message": f"{mode} 모드 질문 상한을 초과했습니다.",
                    "counters": _build_question_snapshot(state),
                    "limits": {
                        "required": state.question_required_limit,
                        "optional": state.question_optional_limit,
                        "special": state.question_special_limit,
                        "total": limits["total"],
                    },
                },
            )

        if allocated == "required":
            state.question_required_count += 1
        elif allocated == "optional":
            state.question_optional_count += 1
        else:
            state.question_special_count += 1
        state.question_total_count += 1
        if touch_plan_data_version:
            state.plan_data_version = int(getattr(state, "plan_data_version", 0) or 0) + 1
            state.summary_revision = 0

        async with AsyncSessionLocal() as session:
            await session.merge(state)
            await session.commit()

        snapshot = _build_question_snapshot(state)
        return {
            "policy_version": state.policy_version,
            "consultation_mode": state.consultation_mode,
            "requested_question_type": requested_question_type,
            "allocated_question_type": allocated,
            "question_required_count": snapshot["question_required_count"],
            "question_optional_count": snapshot["question_optional_count"],
            "question_special_count": snapshot["question_special_count"],
            "question_total_count": snapshot["question_total_count"],
            "question_required_limit": state.question_required_limit,
            "question_optional_limit": state.question_optional_limit,
            "question_special_limit": state.question_special_limit,
            "plan_data_version": int(getattr(state, "plan_data_version", 0) or 0),
            "summary_revision": int(getattr(state, "summary_revision", 0) or 0),
        }


async def set_question_type_from_profile(project_id: str, annual_revenue: float | int | None, classified_stage: str | None = None) -> str:
    if classified_stage in ["SCALEUP", "ADVANCED", "TRANSITION"] or (annual_revenue or 0) > 1_000_000_000:
        mode = CONSULTATION_MODE_GROWTH
    elif (annual_revenue or 0) <= 0:
        mode = CONSULTATION_MODE_PRELIMINARY
    else:
        mode = CONSULTATION_MODE_EARLY

    await set_project_consultation_mode(project_id, mode)
    return mode


def _approval_missing_guides(missing_steps: list[str]) -> list[str]:
    guides: list[str] = []
    for step in missing_steps:
        guide = APPROVAL_STEP_GUIDES.get(step)
        if guide:
            guides.append(guide)
    return guides


async def _resolve_requirement_version(
    project_id: str,
    thread_id: str | None = None,
    requirement_version: int | None = None,
) -> int:
    if requirement_version is not None:
        return max(0, int(requirement_version))
    if thread_id:
        conv_state = await get_project_policy_state(project_id, thread_id=thread_id)
    else:
        conv_state = await get_project_policy_state(project_id)
    if not conv_state:
        return 0
    return max(0, int(getattr(conv_state, "plan_data_version", 0) or 0))


async def _get_or_create_approval_state_compat(
    project_id: str,
    artifact_type: str,
    thread_id: str | None = None,
    requirement_version: int | None = None,
) -> ArtifactApprovalStateModel:
    if thread_id is None and requirement_version is None:
        return await get_or_create_approval_state(project_id, artifact_type)
    try:
        return await get_or_create_approval_state(
            project_id,
            artifact_type,
            thread_id=thread_id,
            requirement_version=requirement_version,
        )
    except TypeError:
        # 테스트 monkeypatch(구 시그니처) 호환
        return await get_or_create_approval_state(project_id, artifact_type)


async def get_or_create_approval_state(
    project_id: str,
    artifact_type: str,
    thread_id: str | None = None,
    requirement_version: int | None = None,
) -> ArtifactApprovalStateModel:
    await ensure_artifact_approval_columns()
    thread_scope = _normalize_thread_scope(thread_id)
    version_scope = await _resolve_requirement_version(
        project_id,
        thread_id=thread_id,
        requirement_version=requirement_version,
    )
    async with AsyncSessionLocal() as session:
        stmt = select(ArtifactApprovalStateModel).where(
            ArtifactApprovalStateModel.project_id == project_id,
            ArtifactApprovalStateModel.artifact_type == artifact_type,
            ArtifactApprovalStateModel.thread_id == thread_scope,
            ArtifactApprovalStateModel.requirement_version == version_scope,
        )
        state = (await session.execute(stmt)).scalar_one_or_none()
        if state:
            return state

        # Legacy DB unique(project_id, artifact_type)가 남아있는 경우를 위한 호환 처리.
        legacy_stmt = select(ArtifactApprovalStateModel).where(
            ArtifactApprovalStateModel.project_id == project_id,
            ArtifactApprovalStateModel.artifact_type == artifact_type,
        )
        legacy_state = (await session.execute(legacy_stmt)).scalar_one_or_none()
        if legacy_state:
            scope_changed = (
                legacy_state.thread_id != thread_scope
                or int(getattr(legacy_state, "requirement_version", 0) or 0) != version_scope
            )
            legacy_state.thread_id = thread_scope
            legacy_state.requirement_version = version_scope
            legacy_state.plan_data_version = version_scope
            if scope_changed:
                legacy_state.key_figures_approved = False
                legacy_state.certification_path_approved = False
                legacy_state.template_selected = False
                legacy_state.summary_confirmed = False
                legacy_state.summary_revision = 0
            await session.commit()
            return legacy_state

        state = ArtifactApprovalStateModel(
            project_id=project_id,
            artifact_type=artifact_type,
            thread_id=thread_scope,
            requirement_version=version_scope,
            plan_data_version=version_scope,
        )
        session.add(state)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            # 경쟁 조건/legacy unique 충돌 시 최신 row를 재조회 후 scope 기준으로 재초기화
            fallback = (await session.execute(
                select(ArtifactApprovalStateModel).where(
                    ArtifactApprovalStateModel.project_id == project_id,
                    ArtifactApprovalStateModel.artifact_type == artifact_type,
                )
            )).scalar_one_or_none()
            if not fallback:
                raise
            scope_changed = (
                fallback.thread_id != thread_scope
                or int(getattr(fallback, "requirement_version", 0) or 0) != version_scope
            )
            fallback.thread_id = thread_scope
            fallback.requirement_version = version_scope
            fallback.plan_data_version = version_scope
            if scope_changed:
                fallback.key_figures_approved = False
                fallback.certification_path_approved = False
                fallback.template_selected = False
                fallback.summary_confirmed = False
                fallback.summary_revision = 0
            await session.commit()
            return fallback
        return state


def _approval_missing_flags(state: ArtifactApprovalStateModel) -> list[str]:
    missing = []
    for step in REQUIRED_APPROVAL_STEPS:
        if not bool(getattr(state, step, False)):
            missing.append(step)
    return missing


async def get_approval_state_dict(
    project_id: str,
    artifact_type: str,
    thread_id: str | None = None,
    requirement_version: int | None = None,
) -> Dict[str, Any]:
    state = await _get_or_create_approval_state_compat(
        project_id,
        artifact_type,
        thread_id=thread_id,
        requirement_version=requirement_version,
    )
    if thread_id:
        conv_state = await get_project_policy_state(project_id, thread_id=thread_id)
    else:
        conv_state = await get_project_policy_state(project_id)
    missing_steps = _approval_missing_flags(state)
    return {
        "project_id": project_id,
        "thread_id": state.thread_id,
        "artifact_type": artifact_type,
        "requirement_version": int(getattr(state, "requirement_version", 0) or 0),
        "key_figures_approved": state.key_figures_approved,
        "certification_path_approved": state.certification_path_approved,
        "template_selected": state.template_selected,
        "summary_confirmed": state.summary_confirmed,
        "summary_revision": int(getattr(state, "summary_revision", 0) or 0),
        "plan_data_version": int(getattr(state, "plan_data_version", 0) or 0),
        "current_requirement_version": int(getattr(conv_state, "plan_data_version", 0) or 0) if conv_state else 0,
        "missing_steps": missing_steps,
        "missing_step_guides": _approval_missing_guides(missing_steps),
    }


async def update_approval_step(
    project_id: str,
    artifact_type: str,
    step: str,
    approved: bool,
    thread_id: str | None = None,
    requirement_version: int | None = None,
) -> Dict[str, Any]:
    if step not in REQUIRED_APPROVAL_STEPS:
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": "POLICY_VALIDATION_FAILED",
                "message": "허용되지 않은 승인 스텝입니다.",
                "violations": [f"allowed_steps={','.join(REQUIRED_APPROVAL_STEPS)}"],
            },
        )

    resolved_requirement_version = await _resolve_requirement_version(
        project_id,
        thread_id=thread_id,
        requirement_version=requirement_version,
    )
    lock = _get_approval_step_lock(
        project_id,
        artifact_type,
        thread_id=thread_id,
        requirement_version=resolved_requirement_version,
    )
    async with lock:
        state = await _get_or_create_approval_state_compat(
            project_id,
            artifact_type,
            thread_id=thread_id,
            requirement_version=resolved_requirement_version,
        )
        if thread_id:
            conv_state = await get_project_policy_state(project_id, thread_id=thread_id)
        else:
            conv_state = await get_project_policy_state(project_id)
        state.plan_data_version = resolved_requirement_version
        setattr(state, step, bool(approved))

        if step == "summary_confirmed":
            if approved:
                state.summary_confirmed = True
                state.summary_revision = resolved_requirement_version
                state.plan_data_version = state.summary_revision
            else:
                state.summary_confirmed = False
                state.summary_revision = 0
                state.plan_data_version = resolved_requirement_version
        async with AsyncSessionLocal() as session:
            await session.merge(state)
            await session.commit()
        data = await get_approval_state_dict(
            project_id,
            artifact_type,
            thread_id=thread_id,
            requirement_version=resolved_requirement_version,
        )
        data["policy_version"] = (
            conv_state.policy_version
            if conv_state
            else POLICY_VERSION_LEGACY
        )
        return data


async def require_pdf_approval(
    project_id: str,
    artifact_type: str = "business_plan",
    thread_id: str | None = None,
    requirement_version: int | None = None,
) -> None:
    if artifact_type != "business_plan":
        return

    if thread_id:
        state = await get_project_policy_state(project_id, thread_id=thread_id)
    else:
        state = await get_project_policy_state(project_id)
    if not state or state.policy_version != POLICY_VERSION_V1:
        return

    approval_state = await _get_or_create_approval_state_compat(
        project_id,
        artifact_type,
        thread_id=thread_id,
        requirement_version=requirement_version,
    )
    missing = _approval_missing_flags(approval_state)
    if missing:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "APPROVAL_INCOMPLETE",
                "message": "사업계획서 PDF 승인 단계가 미완료입니다.",
                "missing_steps": missing,
                "missing_step_guides": _approval_missing_guides(missing),
                "thread_id": getattr(approval_state, "thread_id", ""),
                "requirement_version": int(getattr(approval_state, "requirement_version", 0) or 0),
            },
        )


async def list_growth_templates(
    artifact_type: str | None = None,
    stage: str | None = None,
    active_only: bool = False,
) -> list[GrowthTemplate]:
    async with AsyncSessionLocal() as session:
        stmt = select(GrowthTemplateModel).order_by(
            GrowthTemplateModel.artifact_type.asc(),
            GrowthTemplateModel.stage.asc(),
            GrowthTemplateModel.version.desc(),
            GrowthTemplateModel.updated_at.desc(),
        )
        if artifact_type:
            stmt = stmt.where(GrowthTemplateModel.artifact_type == artifact_type)
        if stage:
            stmt = stmt.where(GrowthTemplateModel.stage == stage)
        if active_only:
            stmt = stmt.where(GrowthTemplateModel.is_active == True)

        rows = (await session.execute(stmt)).scalars().all()
        return [
            GrowthTemplate(
                id=row.id,
                name=row.name,
                artifact_type=row.artifact_type,
                stage=row.stage,
                version=row.version,
                source_pdf=row.source_pdf,
                sections_keys_ordered=row.sections_keys_ordered or [],
                template_body=row.template_body,
                is_active=row.is_active,
                is_default=row.is_default,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]


async def create_growth_template(payload: Dict[str, Any]) -> GrowthTemplate:
    template = GrowthTemplateModel(
        id=str(uuid4()),
        name=payload["name"],
        artifact_type=payload.get("artifact_type", "business_plan"),
        stage=payload["stage"],
        version=payload["version"],
        source_pdf=payload.get("source_pdf"),
        sections_keys_ordered=payload.get("sections_keys_ordered") or [],
        template_body=payload["template_body"],
        is_active=bool(payload.get("is_active", False)),
        is_default=bool(payload.get("is_default", False)),
    )
    if template.is_default:
        template.is_active = True

    async with AsyncSessionLocal() as session:
        session.add(template)
        await session.commit()
        return GrowthTemplate(
            id=template.id,
            name=template.name,
            artifact_type=template.artifact_type,
            stage=template.stage,
            version=template.version,
            source_pdf=template.source_pdf,
            sections_keys_ordered=template.sections_keys_ordered or [],
            template_body=template.template_body,
            is_active=template.is_active,
            is_default=template.is_default,
            created_at=template.created_at,
            updated_at=template.updated_at,
        )


async def patch_growth_template(template_id: str, payload: Dict[str, Any]) -> GrowthTemplate:
    async with AsyncSessionLocal() as session:
        row = (await session.execute(
            select(GrowthTemplateModel).where(GrowthTemplateModel.id == template_id)
        )).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Template not found")

        for key, value in payload.items():
            if value is None:
                continue
            setattr(row, key, value)

        await session.commit()
        return GrowthTemplate(
            id=row.id,
            name=row.name,
            artifact_type=row.artifact_type,
            stage=row.stage,
            version=row.version,
            source_pdf=row.source_pdf,
            sections_keys_ordered=row.sections_keys_ordered or [],
            template_body=row.template_body,
            is_active=row.is_active,
            is_default=row.is_default,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


async def delete_growth_template(template_id: str) -> None:
    async with AsyncSessionLocal() as session:
        row = (await session.execute(select(GrowthTemplateModel).where(GrowthTemplateModel.id == template_id))).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Template not found")
        await session.delete(row)
        await session.commit()


async def activate_template(template_id: str) -> GrowthTemplate:
    async with AsyncSessionLocal() as session:
        row = (await session.execute(select(GrowthTemplateModel).where(GrowthTemplateModel.id == template_id))).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Template not found")
        stmt = (
            select(GrowthTemplateModel)
            .where(GrowthTemplateModel.artifact_type == row.artifact_type, GrowthTemplateModel.stage == row.stage)
        )
        existing = (await session.execute(stmt)).scalars().all()
        for item in existing:
            item.is_active = False
        row.is_active = True
        await session.commit()

        return GrowthTemplate(
            id=row.id,
            name=row.name,
            artifact_type=row.artifact_type,
            stage=row.stage,
            version=row.version,
            source_pdf=row.source_pdf,
            sections_keys_ordered=row.sections_keys_ordered or [],
            template_body=row.template_body,
            is_active=row.is_active,
            is_default=row.is_default,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


async def deactivate_template(template_id: str) -> GrowthTemplate:
    async with AsyncSessionLocal() as session:
        row = (await session.execute(select(GrowthTemplateModel).where(GrowthTemplateModel.id == template_id))).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Template not found")
        row.is_active = False
        await session.commit()
        return GrowthTemplate(
            id=row.id,
            name=row.name,
            artifact_type=row.artifact_type,
            stage=row.stage,
            version=row.version,
            source_pdf=row.source_pdf,
            sections_keys_ordered=row.sections_keys_ordered or [],
            template_body=row.template_body,
            is_active=row.is_active,
            is_default=row.is_default,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


async def set_project_active_template(
    project_id: str,
    template_id: str,
    thread_id: str | None = None,
) -> Dict[str, Any]:
    async with AsyncSessionLocal() as session:
        row = (await session.execute(select(GrowthTemplateModel).where(GrowthTemplateModel.id == template_id))).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Template not found")

        if thread_id:
            state = (await session.execute(
                select(ConversationStateThreadModel).where(
                    ConversationStateThreadModel.project_id == project_id,
                    ConversationStateThreadModel.thread_id == thread_id,
                )
            )).scalar_one_or_none()
            if not state:
                state = ConversationStateThreadModel(
                    project_id=project_id,
                    thread_id=thread_id,
                    policy_version=POLICY_VERSION_V1,
                    consultation_mode=row.stage,
                    profile_stage=row.stage,
                )
                session.add(state)
        else:
            state = (await session.execute(
                select(ConversationStateModel).where(ConversationStateModel.project_id == project_id)
            )).scalar_one_or_none()
            if not state:
                state = ConversationStateModel(
                    project_id=project_id,
                    policy_version=POLICY_VERSION_V1,
                    consultation_mode=row.stage,
                    profile_stage=row.stage,
                )
                session.add(state)
        state.active_template_id = row.id
        await session.commit()

        if row.artifact_type == "business_plan":
            await update_approval_step(
                project_id,
                row.artifact_type,
                "template_selected",
                True,
                thread_id=thread_id,
            )

        return {
            "project_id": project_id,
            "template_id": row.id,
            "template": row.name,
            "artifact_type": row.artifact_type,
            "stage": row.stage,
            "version": row.version,
        }


async def get_selected_template(
    project_id: str,
    artifact_type: str,
    fallback_stage: str,
    thread_id: str | None = None,
) -> Optional[GrowthTemplateModel]:
    async with AsyncSessionLocal() as session:
        state = None
        if thread_id:
            state = (await session.execute(
                select(ConversationStateThreadModel).where(
                    ConversationStateThreadModel.project_id == project_id,
                    ConversationStateThreadModel.thread_id == thread_id,
                )
            )).scalar_one_or_none()
            if not state:
                state = (await session.execute(
                    select(ConversationStateModel).where(ConversationStateModel.project_id == project_id)
                )).scalar_one_or_none()
        else:
            state = (await session.execute(
                select(ConversationStateModel).where(ConversationStateModel.project_id == project_id)
            )).scalar_one_or_none()
        if state and state.active_template_id:
            selected = (await session.execute(
                select(GrowthTemplateModel).where(GrowthTemplateModel.id == state.active_template_id)
            )).scalar_one_or_none()
            if selected and selected.artifact_type == artifact_type:
                # 수동 선택 템플릿 우선. 단, 단계 불일치 시에는 공통 템플릿만 예외 허용.
                if selected.stage == fallback_stage or selected.stage == "공통":
                    return selected

        stmt = select(GrowthTemplateModel).where(
            GrowthTemplateModel.artifact_type == artifact_type,
            GrowthTemplateModel.stage == fallback_stage,
            GrowthTemplateModel.is_active.is_(True),
        )
        active = (await session.execute(stmt)).scalar_one_or_none()
        if active:
            return active

        stmt = select(GrowthTemplateModel).where(
            GrowthTemplateModel.artifact_type == artifact_type,
            GrowthTemplateModel.stage == fallback_stage,
            GrowthTemplateModel.is_default.is_(True),
        )
        default_tpl = (await session.execute(stmt)).scalar_one_or_none()
        return default_tpl


def _to_html(text: str) -> str:
    source = str(text or "")
    if _py_markdown is not None:
        rendered = _py_markdown.markdown(
            source,
            extensions=["extra", "tables", "fenced_code", "sane_lists", "nl2br"],
            output_format="html5",
        )
    else:
        # Fallback: keep previous behavior if markdown package is unavailable.
        rendered = escape(source).replace("\n", "<br/>")

    return (
        "<html><head><meta charset='utf-8' />"
        "<style>"
        "body{font-family:Arial,sans-serif;padding:24px;line-height:1.6;color:#111827;}"
        "table{border-collapse:collapse;width:100%;margin:12px 0;}"
        "th,td{border:1px solid #d1d5db;padding:8px;text-align:left;vertical-align:top;}"
        "th{background:#f3f4f6;font-weight:600;}"
        "blockquote{border-left:4px solid #d1d5db;margin:12px 0;padding:4px 12px;color:#374151;}"
        "code{background:#f3f4f6;padding:2px 4px;border-radius:4px;}"
        "pre code{display:block;padding:12px;overflow:auto;}"
        "</style></head><body>"
        f"{rendered}"
        "</body></html>"
    )


def _resolve_template_sections_row(template: GrowthTemplateModel) -> list[str]:
    if template and template.sections_keys_ordered:
        return template.sections_keys_ordered
    return SECTION_SCHEMA_V1.get(template.artifact_type if template else "business_plan", {}).get(template.stage if template else "", [])


def _ensure_dict_sections(
    sections: Any,
    required_keys: list[str],
    source: str,
) -> dict[str, str]:
    if not isinstance(sections, dict):
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": "POLICY_VALIDATION_FAILED",
                "message": "섹션이 dict 형태로 전달되어야 합니다.",
                "violations": [f"{source}: sections_type_invalid"],
            },
        )

    prepared: dict[str, str] = {}
    missing: list[str] = []
    for key in required_keys:
        value = sections.get(key)
        if value is None:
            missing.append(key)
            continue
        prepared[key] = str(value)

    if missing:
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": "POLICY_VALIDATION_FAILED",
                "message": "필수 템플릿 섹션 누락",
                "violations": [f"missing_sections:{key}" for key in missing],
                "required_sections": required_keys,
                "missing_sections": missing,
            },
        )

    return prepared


def _build_template_context(plan: Dict[str, Any], template: Optional[GrowthTemplateModel]) -> Dict[str, Any]:
    required_sections = _resolve_template_sections_row(template) if template else []
    sections_markdown = _ensure_dict_sections(plan.get("sections_markdown", {}), required_sections, "sections_markdown")
    sections_html = _ensure_dict_sections(plan.get("sections_html", {}), required_sections, "sections_html")

    analysis = plan.get("analysis", {})
    if isinstance(analysis, dict):
        analysis_text = json.dumps(analysis, ensure_ascii=False)
    else:
        analysis_text = str(analysis)

    return {
        "title": plan.get("title", "사업계획서"),
        "company_type": plan.get("company_type", ""),
        "growth_stage": plan.get("growth_stage", ""),
        "company_name": (plan.get("company_name", "") or plan.get("company", "")),
        "sections_markdown": sections_markdown,
        "sections_html": sections_html,
        "analysis": analysis_text,
    }


def _to_markdown_cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return text.replace("|", "\\|").replace("\r\n", "\n").replace("\n", "<br/>")


def _to_html_cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return escape(text).replace("\r\n", "\n").replace("\n", "<br/>")


def _normalize_multiline_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\r\n", "\n").strip()


def _get_section_markdown(plan: Dict[str, Any], key: str) -> str:
    sections_markdown = plan.get("sections_markdown")
    if not isinstance(sections_markdown, dict):
        return ""
    return _normalize_multiline_text(sections_markdown.get(key))


def _section_or_placeholder(plan: Dict[str, Any], key: str) -> str:
    text = _get_section_markdown(plan, key)
    return text if text else "&nbsp;"


def _get_form_fields(plan: Dict[str, Any]) -> Dict[str, str]:
    return normalize_form_fields(plan.get("form_fields", {}))


def _form_value(plan: Dict[str, Any], *keys: str, default: str = "") -> str:
    fields = _get_form_fields(plan)
    for key in keys:
        value = fields.get(key)
        if value:
            return _to_markdown_cell(value)
    return _to_markdown_cell(default) if default else ""


def _field_status_payload(
    artifact_type: str,
    stage: str,
    plan: Dict[str, Any],
) -> Dict[str, Any]:
    template_code = resolve_template_code(artifact_type, stage)
    form_fields = _get_form_fields(plan)
    missing_keys, missing_guides = compute_missing_field_guides(template_code, form_fields)
    required_count = len(TEMPLATE_REQUIRED_FIELDS.get(template_code, []))
    return {
        "missing_field_keys": missing_keys,
        "missing_field_guides": missing_guides,
        "filled_field_count": max(0, required_count - len(missing_keys)),
        "required_field_count": required_count,
    }


def _render_business_plan_pre_startup_2025_markdown_form(plan: Dict[str, Any]) -> str:
    item_name = _form_value(plan, "item_name")
    representative_name = _form_value(plan, "representative_name")
    contact_phone = _form_value(plan, "contact_phone")
    email = _form_value(plan, "email")
    technology_field = _form_value(plan, "technology_field")
    government_fund = _form_value(plan, "government_fund")
    matching_fund = _form_value(plan, "matching_fund")
    total_budget = _form_value(plan, "total_budget")
    pain_point = _form_value(plan, "pain_point")
    implementation_plan = _form_value(plan, "implementation_plan")
    funding_plan = _form_value(plan, "funding_plan")
    founder_experience = _form_value(plan, "founder_experience")
    external_partnership = _form_value(plan, "external_partnership")
    summary_overview = _section_or_placeholder(plan, "summary_overview")
    general_status = _section_or_placeholder(plan, "general_status")
    problem_market = _section_or_placeholder(plan, "problem_1_market_status_and_issues")
    problem_need = _section_or_placeholder(plan, "problem_2_need_for_development")
    solution_plan = _section_or_placeholder(plan, "solution_1_development_plan")
    solution_diff = _section_or_placeholder(plan, "solution_2_differentiation_competitiveness")
    solution_stage1 = _section_or_placeholder(plan, "solution_3_gov_fund_execution_plan_stage1")
    solution_stage2 = _section_or_placeholder(plan, "solution_4_gov_fund_execution_plan_stage2")
    solution_schedule = _section_or_placeholder(plan, "solution_5_schedule_within_agreement")
    scaleup_competition = _section_or_placeholder(plan, "scaleup_1_competitor_analysis_and_entry_strategy")
    scaleup_bm = _section_or_placeholder(plan, "scaleup_2_business_model_revenue")
    scaleup_funding = _section_or_placeholder(plan, "scaleup_3_funding_investment_strategy")
    scaleup_social_value = _section_or_placeholder(plan, "scaleup_4_roadmap_and_social_value_plan")
    scaleup_roadmap = _section_or_placeholder(plan, "scaleup_5_schedule_full_phases")
    team_founder = _section_or_placeholder(plan, "team_1_founder_capability")
    team_hiring = _section_or_placeholder(plan, "team_2_team_members_and_hiring_plan")
    team_external = _section_or_placeholder(plan, "team_3_assets_facilities_and_partners")

    return (
        "# 예비창업패키지 예비창업자 사업계획서 (2025)\n\n"
        "> **작성 유의사항**\n"
        "> - 글자 색상은 검정색으로 작성하세요.\n"
        "> - 파란색 안내 문구는 삭제 후 작성하세요.\n"
        "> - 생년월일, 성별 등 민감 개인정보는 마스킹 처리 후 제출하세요.\n"
        "> - 목차 페이지는 제출 시 삭제하세요.\n"
        "> - 사업계획서는 10페이지 이내(목차 제외)로 작성하세요.\n\n"
        "---\n\n"
        "## 목차\n\n"
        "1. [문제 인식 (Problem)](#1-문제-인식-problem)\n"
        "2. [실현 가능성 (Solution)](#2-실현-가능성-solution)\n"
        "3. [성장 전략 (Scale-up)](#3-성장-전략-scale-up)\n"
        "4. [팀 구성 (Team)](#4-팀-구성-team)\n\n"
        "---\n\n"
        "## 일반 현황\n\n"
        "| 항목 | 내용 |\n"
        "|------|------|\n"
        f"| 창업아이템명 | {item_name} |\n\n"
        "**신청인 정보**\n\n"
        "| 성명 | 연락처 | 이메일 |\n"
        "|------|--------|--------|\n"
        f"| {representative_name} | {contact_phone} | {email} |\n\n"
        "| 생년월일 | 성별 |\n"
        "|---------|------|\n"
        "| (마스킹 처리) | (마스킹 처리) |\n\n"
        "**창업 단계**\n\n"
        "- ☐ 아이디어 단계\n"
        "- ☐ 시제품 제작 단계\n"
        "- ☐ 시장 검증 단계\n"
        "- ☐ 사업화 단계\n\n"
        f"{technology_field}\n\n"
        "**팀 구성 현황**\n\n"
        "| 성명 | 직위 | 역할 | 보유 역량 |\n"
        "|------|------|------|----------|\n"
        "| | | | |\n"
        "| | | | |\n"
        "| | | | |\n\n"
        f"{general_status}\n\n"
        "---\n\n"
        "## 사업계획서 요약\n\n"
        "**창업아이템 개요**\n\n"
        "> (창업 아이템의 핵심 내용을 간략히 서술하세요.)\n\n"
        f"{summary_overview}\n\n"
        "| 항목 | 내용 |\n"
        "|------|------|\n"
        "| 문제 인식 (Problem) | |\n"
        "| 실현 가능성 (Solution) | |\n"
        "| 성장 전략 (Scale-up) | |\n"
        "| 팀 구성 (Team) | |\n\n"
        "> 📷 *제품/서비스 이미지 또는 다이어그램 삽입*\n\n"
        "---\n\n"
        "## 1. 문제 인식 (Problem)\n\n"
        "### 1-1. 창업 아이템의 필요성\n\n"
        "> (해결하고자 하는 문제/불편함, 시장의 Pain Point를 서술하세요.)\n\n"
        f"{pain_point}\n\n"
        f"{problem_need}\n\n"
        "**시장 분석**\n\n"
        "> (목표 시장 규모, 고객 분석 등을 서술하세요.)\n\n"
        f"{problem_market}\n\n"
        "---\n\n"
        "## 2. 실현 가능성 (Solution)\n\n"
        "### 2-1. 사업화 및 구체화 계획\n\n"
        "> (제품/서비스의 구체적인 구현 방안과 차별성을 서술하세요.)\n\n"
        f"{implementation_plan}\n\n"
        f"{solution_plan}\n\n"
        f"{solution_diff}\n\n"
        "**추진 일정**\n\n"
        "| 구분 | 내용 | 기간 | 세부 사항 |\n"
        "|------|------|------|----------|\n"
        "| | | | |\n"
        "| | | | |\n"
        "| | | | |\n"
        "| | | | |\n\n"
        f"{solution_schedule}\n\n"
        "### 2-2. 정부지원사업비 집행 계획\n\n"
        "**예산 구성**\n\n"
        "| 구분 | 정부지원금 | 자부담 | 합계 |\n"
        "|------|-----------|--------|------|\n"
        f"| 금액 (원) | {government_fund} | {matching_fund} | {total_budget} |\n"
        "| 비율 (%) | | | |\n\n"
        "**1단계 지출 계획**\n\n"
        "| 비목 | 세목 | 산출근거 | 금액 (원) |\n"
        "|------|------|---------|----------|\n"
        "| 재료비 | | | |\n"
        "| 외주용역비 | | | |\n"
        "| 수수료 | | | |\n"
        "| 기타 | | | |\n"
        "| **합계** | | | |\n\n"
        f"{solution_stage1}\n\n"
        "**2단계 지출 계획**\n\n"
        "| 비목 | 세목 | 산출근거 | 금액 (원) |\n"
        "|------|------|---------|----------|\n"
        "| 재료비 | | | |\n"
        "| 외주용역비 | | | |\n"
        "| 수수료 | | | |\n"
        "| 기타 | | | |\n"
        "| **합계** | | | |\n\n"
        f"{solution_stage2}\n\n"
        "---\n\n"
        "## 3. 성장 전략 (Scale-up)\n\n"
        "### 3-1. 경쟁사 분석 및 시장 진입 전략\n\n"
        "> (경쟁사 현황 분석 및 자사의 차별화 전략을 서술하세요.)\n\n"
        f"{scaleup_competition}\n\n"
        "### 3-2. BM 및 자금 조달\n\n"
        "> (비즈니스 모델 및 향후 자금 조달 계획을 서술하세요.)\n\n"
        f"{funding_plan}\n\n"
        f"{scaleup_bm}\n\n"
        f"{scaleup_funding}\n\n"
        "**사업 로드맵**\n\n"
        "| 단계 | 기간 | 목표 | 주요 활동 |\n"
        "|------|------|------|----------|\n"
        "| 단기 (1년) | | | |\n"
        "| 중기 (3년) | | | |\n"
        "| 장기 (5년) | | | |\n\n"
        f"{scaleup_roadmap}\n\n"
        "### 3-3. 사회적 가치\n\n"
        "> (창업 아이템이 창출하는 사회적 가치를 서술하세요.)\n\n"
        f"{scaleup_social_value}\n\n"
        "---\n\n"
        "## 4. 팀 구성 (Team)\n\n"
        "### 4-1. 대표자 역량\n\n"
        "> (대표자의 관련 경력, 교육, 전문성 등을 서술하세요.)\n\n"
        f"{founder_experience}\n\n"
        f"{team_founder}\n\n"
        "### 4-2. 추가 채용 계획\n\n"
        "| 직무 | 인원 | 채용 시기 | 요구 역량 |\n"
        "|------|------|----------|----------|\n"
        "| | | | |\n"
        "| | | | |\n\n"
        f"{team_hiring}\n\n"
        "### 4-3. 외부 협력 계획\n\n"
        "> (멘토, 협력기관, 투자사 등 외부 협력 네트워크를 서술하세요.)\n\n"
        f"{external_partnership}\n\n"
        f"{team_external}\n"
    )


def _render_business_plan_early_startup_2023_markdown_form(plan: Dict[str, Any]) -> str:
    company_name = _form_value(plan, "company_name", default=str(plan.get("company_name") or plan.get("company") or ""))
    representative_name = _form_value(plan, "representative_name")
    established_date = _form_value(plan, "established_date")
    address = _form_value(plan, "address")
    employee_count = _form_value(plan, "employee_count")
    recent_revenue = _form_value(plan, "recent_revenue", default=str(plan.get("recent_revenue") or plan.get("annual_revenue") or ""))
    technology_field = _form_value(plan, "technology_field")
    government_fund = _form_value(plan, "government_fund")
    matching_fund = _form_value(plan, "matching_fund")
    total_budget = _form_value(plan, "total_budget")
    item_name = _form_value(plan, "item_name")
    core_advantage_1 = _form_value(plan, "core_advantage_1")
    core_advantage_2 = _form_value(plan, "core_advantage_2")
    core_advantage_3 = _form_value(plan, "core_advantage_3")
    target_market = _form_value(plan, "target_market")
    general_status = _section_or_placeholder(plan, "general_status")
    summary_overview = _section_or_placeholder(plan, "summary_overview")
    problem_1 = _section_or_placeholder(plan, "problem_1_background_and_necessity")
    problem_2 = _section_or_placeholder(plan, "problem_2_target_market_and_requirements")
    solution_1 = _section_or_placeholder(plan, "solution_1_preparation_status")
    solution_2 = _section_or_placeholder(plan, "solution_2_realization_and_detail_plan")
    scaleup_1 = _section_or_placeholder(plan, "scaleup_1_business_model_and_results")
    scaleup_2 = _section_or_placeholder(plan, "scaleup_2_market_entry_and_strategy")
    scaleup_3 = _section_or_placeholder(plan, "scaleup_3_schedule_and_fund_plan_roadmap")
    scaleup_5 = _section_or_placeholder(plan, "scaleup_5_budget_execution_plan")
    team_1 = _section_or_placeholder(plan, "team_1_org_and_capabilities")
    team_2 = _section_or_placeholder(plan, "team_2_current_hires_and_hiring_plan")
    team_3 = _section_or_placeholder(plan, "team_3_external_partners")

    return (
        "# 창업사업화 지원사업 사업계획서 [초기단계] (2023)\n\n"
        "---\n\n"
        "## 신청서 및 일반 현황\n\n"
        "**사업분야 (해당 항목 체크)**\n\n"
        "| 분야 | | 분야 | | 분야 | |\n"
        "|------|---|------|---|------|---|\n"
        "| ☐ 제조업 | | ☐ 에너지 | | ☐ ICT | |\n"
        "| ☐ 바이오 | | ☐ 문화·콘텐츠 | | ☐ 농식품 | |\n"
        "| ☐ 환경 | | ☐ 소재·부품 | | ☐ 기타 | |\n\n"
        "**기술 분야**\n\n"
        "> (주력 기술 분야를 기재하세요.)\n\n"
        f"{technology_field}\n\n"
        f"{general_status}\n\n"
        "**프로젝트 예산**\n\n"
        "| 구분 | 정부지원금 | 대응자금 | 합계 |\n"
        "|------|-----------|----------|------|\n"
        f"| 금액 (원) | {government_fund} | {matching_fund} | {total_budget} |\n"
        "| 비율 (%) | | | |\n\n"
        "**기업 기본 정보**\n\n"
        "| 항목 | 내용 | 항목 | 내용 |\n"
        "|------|------|------|------|\n"
        f"| 기업명 | {company_name} | 대표자 | {representative_name} |\n"
        f"| 설립일 | {established_date} | 주소 | {address} |\n"
        f"| 종사자 수 | {employee_count} | 최근 매출액 | {recent_revenue} |\n\n"
        "---\n\n"
        "## 사업계획서 요약\n\n"
        "**아이템명**\n\n"
        "> OOO 기술이 적용된 OOO (구체적으로 작성)\n\n"
        f"{item_name}\n\n"
        f"{summary_overview}\n\n"
        "**핵심 특장점**\n\n"
        "| 특장점 | 내용 |\n"
        "|--------|------|\n"
        f"| 1. | {core_advantage_1} |\n"
        f"| 2. | {core_advantage_2} |\n"
        f"| 3. | {core_advantage_3} |\n\n"
        "**사업 요약**\n\n"
        "| 항목 | 내용 |\n"
        "|------|------|\n"
        "| 문제 인식 | |\n"
        "| 솔루션 | |\n"
        "| 성장 전략 | |\n"
        "| 팀 역량 | |\n\n"
        "---\n\n"
        "## 1. 문제 인식 (Problem)\n\n"
        "### 1-1. 배경 및 필요성\n\n"
        "> (해결하고자 하는 문제와 시장 현황을 서술하세요.)\n\n"
        f"{problem_1}\n\n"
        "### 1-2. 목표 시장 및 고객 분석\n\n"
        "> (타겟 시장 규모, 고객 세분화, 주요 고객 불편사항을 서술하세요.)\n\n"
        f"{target_market}\n\n"
        f"{problem_2}\n\n"
        "---\n\n"
        "## 2. 실현 가능성 (Solution)\n\n"
        "### 2-1. 개발 현황\n\n"
        "| 항목 | 현황 |\n"
        "|------|------|\n"
        "| 진행 현황 | |\n"
        "| 기술 현황 | |\n"
        "| 인프라 현황 | |\n\n"
        "> (기술 개발 진행 상황을 상세히 서술하세요.)\n\n"
        f"{solution_1}\n\n"
        "### 2-2. 구체화 계획 및 경쟁력\n\n"
        "> (구현 계획 및 경쟁사 대비 차별점을 서술하세요.)\n\n"
        f"{solution_2}\n\n"
        "**경쟁사 비교**\n\n"
        "| 구분 | 자사 | 경쟁사A | 경쟁사B |\n"
        "|------|------|--------|--------|\n"
        "| 강점 | | | |\n"
        "| 약점 | | | |\n"
        "| 차별화 포인트 | | | |\n\n"
        "---\n\n"
        "## 3. 성장 전략 (Scale-up)\n\n"
        "### 3-1. BM 및 성과 목표\n\n"
        "**달성 목표**\n\n"
        "| 구분 | 특허 | 매출 | 고용 | 투자 |\n"
        "|------|------|------|------|------|\n"
        "| 1년차 | | | | |\n"
        "| 2년차 | | | | |\n"
        "| 3년차 | | | | |\n\n"
        f"{scaleup_1}\n\n"
        "### 3-2. 시장 전략 및 로드맵\n\n"
        "**국내 진출 계획**\n\n"
        "> (국내 시장 진출 전략을 서술하세요.)\n\n"
        f"{scaleup_2}\n\n"
        "**글로벌 진출 계획**\n\n"
        "> (해외 시장 진출 전략을 서술하세요.)\n\n"
        f"{scaleup_2}\n\n"
        "**사업 로드맵**\n\n"
        "| 단계 | 기간 | 주요 목표 | 세부 활동 |\n"
        "|------|------|----------|----------|\n"
        "| 단기 | | | |\n"
        "| 중기 | | | |\n"
        "| 장기 | | | |\n\n"
        f"{scaleup_3}\n\n"
        "### 3-3. 추진 일정 및 예산\n\n"
        "**월별 추진 일정**\n\n"
        "| 업무 구분 | 1월 | 2월 | 3월 | 4월 | 5월 | 6월 | 7월 | 8월 | 9월 | 10월 | 11월 | 12월 |\n"
        "|----------|-----|-----|-----|-----|-----|-----|-----|-----|-----|------|------|------|\n"
        "| | | | | | | | | | | | | |\n"
        "| | | | | | | | | | | | | |\n\n"
        "**자금 사용 계획**\n\n"
        "| 비목 | 세목 | 산출근거 | 금액 (원) |\n"
        "|------|------|---------|----------|\n"
        "| | | | |\n"
        "| | | | |\n"
        "| **합계** | | | |\n\n"
        f"{scaleup_5}\n\n"
        "---\n\n"
        "## 4. 팀 구성 (Team)\n\n"
        "### 4-1. 기업 역량\n\n"
        "**대표자 역량**\n\n"
        "| 항목 | 내용 |\n"
        "|------|------|\n"
        "| 이름 | |\n"
        "| 주요 경력 | |\n"
        "| 관련 학력/자격 | |\n\n"
        f"{team_1}\n\n"
        "**팀원 역량**\n\n"
        "| 이름 | 직위 | 주요 경력 | 담당 역할 |\n"
        "|------|------|----------|----------|\n"
        "| | | | |\n"
        "| | | | |\n\n"
        f"{team_2}\n\n"
        "### 4-2. 협력 네트워크\n\n"
        "| 구분 | 기관명 | 협력 내용 | 기여도 |\n"
        "|------|--------|----------|--------|\n"
        "| 멘토 | | | |\n"
        "| 협력기관 | | | |\n"
        "| 투자사 | | | |\n\n"
        f"{team_3}\n\n"
        "---\n\n"
        "## 가점 및 면제 기준\n\n"
        "### 가점 (추가 점수)\n\n"
        "| 구분 | 조건 | 가점 | 해당 여부 |\n"
        "|------|------|------|---------|\n"
        "| 투자 유치 | 1억원 이상 투자 유치 | | ☐ |\n"
        "| 수상 이력 | 정부 주관 창업경진대회 입상 | | ☐ |\n\n"
        "### 서류 평가 면제\n\n"
        "> ※ 다음 해당자는 서류 평가를 면제받을 수 있습니다.\n"
        "> - K-Startup 그랜드챌린지 최종 선발자\n"
        "> - 기타 요건 충족자 (공고문 확인)\n\n"
        "- ☐ 서류평가 면제 대상 해당 없음\n"
        "- ☐ 서류평가 면제 대상 해당 (근거: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; )\n\n"
        "---\n\n"
        "## 첨부 서류\n\n"
        "- ☐ 개인정보 수집·이용 동의서\n"
        "- ☐ 사업자 확인서\n"
        "- ☐ 기타 증빙 서류\n"
    )


def _render_business_plan_scaleup_package_markdown_form(plan: Dict[str, Any]) -> str:
    company_name = _form_value(plan, "company_name", default=str(plan.get("company_name") or plan.get("company") or ""))
    representative_name = _form_value(plan, "representative_name")
    business_registration_no = _form_value(plan, "business_registration_no")
    established_date = _form_value(plan, "established_date")
    address = _form_value(plan, "address")
    contact_phone = _form_value(plan, "contact_phone")
    current_employment = _form_value(plan, "current_employment")
    current_sales = _form_value(plan, "current_sales")
    current_export = _form_value(plan, "current_export")
    current_investment = _form_value(plan, "current_investment")
    target_employment = _form_value(plan, "target_employment")
    target_sales = _form_value(plan, "target_sales")
    target_export = _form_value(plan, "target_export")
    target_investment = _form_value(plan, "target_investment")
    government_fund = _form_value(plan, "government_fund")
    matching_fund = _form_value(plan, "matching_fund")
    total_budget = _form_value(plan, "total_budget")
    technology_field = _form_value(plan, "technology_field")
    item_name = _form_value(plan, "item_name")
    differentiation = _form_value(plan, "differentiation")
    target_market = _form_value(plan, "target_market")
    exit_strategy = _form_value(plan, "exit_strategy")
    general_status = _section_or_placeholder(plan, "general_status")
    product_service_summary = _section_or_placeholder(plan, "product_service_summary")
    problem_1 = _section_or_placeholder(plan, "problem_1_tasks_to_solve")
    problem_2 = _section_or_placeholder(plan, "problem_2_competitor_gap_tasks")
    problem_3 = _section_or_placeholder(plan, "problem_3_customer_needs_tasks")
    solution_1 = _section_or_placeholder(plan, "solution_1_dev_improve_plan_and_schedule")
    solution_2 = _section_or_placeholder(plan, "solution_2_customer_requirements_response")
    solution_3 = _section_or_placeholder(plan, "solution_3_competitiveness_strengthening")
    scaleup_1 = _section_or_placeholder(plan, "scaleup_1_fund_need_and_financing")
    scaleup_2 = _section_or_placeholder(plan, "scaleup_2_market_entry_and_results_domestic")
    scaleup_3 = _section_or_placeholder(plan, "scaleup_3_market_entry_and_results_global")
    scaleup_4 = _section_or_placeholder(plan, "scaleup_4_exit_strategy_investment_ma_ipo_gov")
    team_1 = _section_or_placeholder(plan, "team_1_founder_and_staff_capabilities_and_hiring")
    team_2 = _section_or_placeholder(plan, "team_2_partners_and_collaboration")
    team_3 = _section_or_placeholder(plan, "team_3_rnd_capability_and_security")
    team_4 = _section_or_placeholder(plan, "team_4_social_value_and_performance_sharing")

    return (
        "# 창업도약패키지 사업화지원 사업계획서\n\n"
        "> **작성 유의사항**\n"
        "> - 본 서식은 창업도약패키지 사업화지원 신청을 위한 공식 양식입니다.\n"
        "> - 파란색 안내 문구는 삭제 후 내용을 작성하세요.\n"
        "> - 예산 항목은 별첨 예산 계산 기준(부록 1, 2)을 참고하세요.\n\n"
        "---\n\n"
        "## 일반 현황\n\n"
        "**기술 분야 (해당 항목 체크)**\n\n"
        "| 분야 | | 분야 | | 분야 | |\n"
        "|------|---|------|---|------|---|\n"
        "| ☐ 제조 | | ☐ ICT·SW | | ☐ 바이오·헬스 | |\n"
        "| ☐ 에너지·환경 | | ☐ 농식품 | | ☐ 문화·콘텐츠 | |\n"
        "| ☐ 기계·소재 | | ☐ 소비재 | | ☐ 기타 | |\n\n"
        "**대표자 정보**\n\n"
        "| 항목 | 내용 | 항목 | 내용 |\n"
        "|------|------|------|------|\n"
        f"| 기업명 | {company_name} | 대표자 | {representative_name} |\n"
        f"| 사업자번호 | {business_registration_no} | 설립일 | {established_date} |\n"
        f"| 주소 | {address} | 연락처 | {contact_phone} |\n\n"
        f"{technology_field}\n\n"
        "**주요 성과 현황**\n\n"
        "| 구분 | 고용 (명) | 매출 (백만원) | 수출 (천달러) | 투자 (백만원) |\n"
        "|------|----------|-------------|-------------|-------------|\n"
        f"| 현재 | {current_employment} | {current_sales} | {current_export} | {current_investment} |\n"
        f"| 목표 | {target_employment} | {target_sales} | {target_export} | {target_investment} |\n\n"
        "**예산 계획**\n\n"
        "| 구분 | 정부지원금 | 자부담 | 합계 |\n"
        "|------|-----------|--------|------|\n"
        f"| 금액 (원) | {government_fund} | {matching_fund} | {total_budget} |\n"
        "| 비율 (%) | | | |\n\n"
        "**팀 구성 현황**\n\n"
        "| 성명 | 직위 | 역할 | 주요 역량 |\n"
        "|------|------|------|----------|\n"
        "| | | | |\n"
        "| | | | |\n"
        "| | | | |\n\n"
        f"{general_status}\n\n"
        "---\n\n"
        "## 제품·서비스 개요\n\n"
        "| 항목 | 내용 |\n"
        "|------|------|\n"
        f"| 제품/서비스명 | {item_name} |\n"
        "| 제품/서비스 소개 | |\n"
        "| 차별화 포인트 | |\n"
        "| 개발 진행 단계 | |\n"
        "| 목표 시장 | |\n\n"
        "> 📷 *제품/서비스 이미지 삽입*\n\n"
        f"{product_service_summary}\n\n"
        "---\n\n"
        "## 1. 문제 인식 (Problem)\n\n"
        "### 1-1. 해결할 과제 및 Pain Point\n\n"
        "> (시장 문제점, 기존 솔루션의 한계, 고객이 겪는 불편함을 서술하세요.)\n\n"
        f"{problem_1}\n\n"
        "### 1-2. 경쟁사 대비 개선점\n\n"
        "> (기존 제품/서비스와 비교하여 본 아이템이 갖는 개선점을 서술하세요.)\n\n"
        f"{differentiation}\n\n"
        "| 구분 | 기존 제품/서비스 | 본 아이템 |\n"
        "|------|----------------|----------|\n"
        "| 핵심 차이점 | | |\n"
        "| 기술적 우위 | | |\n"
        "| 고객 혜택 | | |\n\n"
        f"{problem_2}\n\n"
        "### 1-3. 고객 니즈 충족 방안\n\n"
        "> (목표 고객의 핵심 니즈와 충족 전략을 서술하세요.)\n\n"
        f"{target_market}\n\n"
        f"{problem_3}\n\n"
        "---\n\n"
        "## 2. 실현 가능성 (Solution)\n\n"
        "### 2-1. 개발 계획 및 일정\n\n"
        "**추진 일정**\n\n"
        "| 구분 | 세부 내용 | 기간 | 산출물 |\n"
        "|------|----------|------|--------|\n"
        "| 1단계 | | | |\n"
        "| 2단계 | | | |\n"
        "| 3단계 | | | |\n\n"
        "> (개발 계획의 구체적인 내용을 서술하세요.)\n\n"
        f"{solution_1}\n\n"
        "### 2-2. 고객 요구사항 대응\n\n"
        "> (고객 피드백 수집 방법 및 제품 개선 계획을 서술하세요.)\n\n"
        f"{solution_2}\n\n"
        "### 2-3. 시장 경쟁력 강화 방안\n\n"
        "> (기술, 특허, 파트너십 등을 통한 경쟁력 강화 전략을 서술하세요.)\n\n"
        f"{solution_3}\n\n"
        "---\n\n"
        "## 3. 성장 전략 (Scale-up)\n\n"
        "### 3-1. 자금 조달 및 집행 계획\n\n"
        "**예산 집행 계획**\n\n"
        "| 비목 | 세목 | 산출근거 | 금액 (원) |\n"
        "|------|------|---------|----------|\n"
        "| 재료비 | | | |\n"
        "| 외주용역비 | | | |\n"
        "| 인건비 | | | |\n"
        "| 기타 | | | |\n"
        "| **합계** | | | |\n\n"
        "*(부록 1, 2의 예산 계산 기준 참고)*\n\n"
        f"{scaleup_1}\n\n"
        "### 3-2. 시장 진입 및 성과 창출\n\n"
        "**국내 시장 전략**\n\n"
        "> (국내 타겟 시장 및 진입 전략을 서술하세요.)\n\n"
        f"{scaleup_2}\n\n"
        "**글로벌 시장 전략**\n\n"
        "> (해외 진출 목표 국가 및 전략을 서술하세요.)\n\n"
        f"{scaleup_3}\n\n"
        "**성과 목표**\n\n"
        "| 구분 | 1년차 | 2년차 | 3년차 |\n"
        "|------|------|------|------|\n"
        "| 매출액 (백만원) | | | |\n"
        "| 수출액 (천달러) | | | |\n"
        "| 고용 (명) | | | |\n"
        "| 투자 유치 (백만원) | | | |\n\n"
        "### 3-3. EXIT 전략\n\n"
        "> ※ 해당 항목에 체크하세요.\n"
        "- ☐ **M&A** — 전략적 인수합병을 통한 Exit 계획\n"
        "- ☐ **IPO** — 기업공개(상장)를 통한 Exit 계획\n"
        "- ☐ **기타** — (구체적 방안 서술)\n\n"
        "> (구체적인 EXIT 전략을 서술하세요.)\n\n"
        f"{exit_strategy}\n\n"
        f"{scaleup_4}\n\n"
        "---\n\n"
        "## 4. 팀 구성 (Team)\n\n"
        "### 4-1. 대표자 및 핵심 인력 전문성\n\n"
        "**대표자 역량**\n\n"
        "| 항목 | 내용 |\n"
        "|------|------|\n"
        "| 이름 | |\n"
        "| 학력 | |\n"
        "| 주요 경력 (최근 순) | |\n"
        "| 관련 성과 | |\n\n"
        f"{team_1}\n\n"
        "**핵심 인력 역량**\n\n"
        "| 이름 | 직위 | 전문 분야 | 주요 경력 |\n"
        "|------|------|----------|----------|\n"
        "| | | | |\n"
        "| | | | |\n\n"
        f"{team_2}\n\n"
        "### 4-2. 기술 개발 및 보호 역량\n\n"
        "> (기술 개발 역량, 특허 보유 현황 및 기술 보호 전략을 서술하세요.)\n\n"
        "| 항목 | 내용 |\n"
        "|------|------|\n"
        "| 보유 특허 | |\n"
        "| 출원 중 특허 | |\n"
        "| 기술 보호 전략 | |\n\n"
        f"{team_3}\n\n"
        "### 4-3. 사회적 가치 실천 계획\n\n"
        "> (기업의 사회적 책임(CSR) 및 ESG 실천 계획을 서술하세요.)\n\n"
        f"{team_4}\n\n"
        "---\n\n"
        "## 가점 및 패스트트랙 체크리스트\n\n"
        "**가점 해당 여부**\n\n"
        "| 항목 | 조건 | 해당 여부 |\n"
        "|------|------|---------|\n"
        "| 투자 유치 | 1억원 이상 | ☐ 해당 / ☐ 미해당 |\n"
        "| 정부 포상 | 장관급 이상 수상 | ☐ 해당 / ☐ 미해당 |\n"
        "| 기타 | (구체적 기재) | ☐ 해당 / ☐ 미해당 |\n\n"
        "**패스트트랙 자격 해당 여부**\n\n"
        "- ☐ 해당 없음\n"
        "- ☐ 해당 (근거 서류 첨부: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; )\n\n"
        "---\n\n"
        "## 부록\n\n"
        "### 부록 1. 예산 계산 기준 (인건비)\n\n"
        "> (인건비 산출 기준 및 방식을 기재하세요.)\n\n"
        "&nbsp;\n\n"
        "### 부록 2. 예산 계산 기준 (경비)\n\n"
        "> (기타 경비 산출 기준 및 방식을 기재하세요.)\n\n"
        "&nbsp;\n"
    )


def _render_business_plan_social_pre_cert_markdown_form(plan: Dict[str, Any]) -> str:
    company_name = _form_value(plan, "company_name", default=str(plan.get("company_name") or plan.get("company") or ""))
    representative_name = _form_value(plan, "representative_name")
    address = _form_value(plan, "address")
    established_date = _form_value(plan, "established_date")
    main_business = _form_value(plan, "main_business")
    contact_phone = _form_value(plan, "contact_phone")
    social_service_type = _form_value(plan, "social_service_type")
    governance_structure = _form_value(plan, "governance_structure")
    vulnerable_employment_plan = _form_value(plan, "vulnerable_employment_plan")
    roadmap_after_designation = _form_value(plan, "roadmap_after_designation")
    social_mission = _form_value(plan, "social_mission")
    company_overview = _section_or_placeholder(plan, "company_overview")
    cert_type = _section_or_placeholder(plan, "cert_eligibility_1_social_purpose_type")
    cert_org = _section_or_placeholder(plan, "cert_eligibility_2_org_form_and_governance")
    cert_employment = _section_or_placeholder(plan, "cert_eligibility_3_employment_plan")
    cert_rules = _section_or_placeholder(plan, "cert_eligibility_4_articles_and_rules")
    plan_purpose = _section_or_placeholder(plan, "plan_1_business_purpose")
    plan_content = _section_or_placeholder(plan, "plan_2_business_content_and_revenue")
    plan_capability = _section_or_placeholder(plan, "plan_3_business_capability")
    plan_goals = _section_or_placeholder(plan, "plan_4_business_goals")
    year1 = _section_or_placeholder(plan, "post_designation_1_year_plan")
    year2 = _section_or_placeholder(plan, "post_designation_2_year_plan")
    year3 = _section_or_placeholder(plan, "post_designation_3_year_plan")
    other_plan = _section_or_placeholder(plan, "other_execution_plan")

    return (
        "# 예비사회적기업 사업계획서\n\n"
        "---\n\n"
        "## 기업 개요\n\n"
        "| 항목 | 내용 |\n"
        "|------|------|\n"
        f"| 기업명 | {company_name} |\n"
        f"| 대표자 | {representative_name} |\n"
        f"| 소재지 | {address} |\n"
        f"| 설립일 | {established_date} |\n"
        f"| 주요사업 | {main_business} |\n"
        f"| 연락처 | {contact_phone} |\n\n"
        f"{company_overview}\n\n"
        "---\n\n"
        "## 인증요건 충족 계획\n\n"
        "### 사회적 목적 유형 선택\n\n"
        "> ※ 해당하는 유형에 체크(☑)하세요.\n\n"
        "- ☐ **사회서비스 제공형** — 취약계층에게 사회서비스 또는 일자리를 제공\n"
        "- ☐ **일자리 제공형** — 취약계층에게 일자리를 제공하는 것을 주된 목적으로 하는 조직\n"
        "- ☐ **지역사회 공헌형** — 지역사회에 공헌하는 것을 주된 목적으로 하는 조직\n"
        "- ☐ **혼합형** — 취약계층 일자리 제공과 사회서비스 제공이 혼합된 유형\n"
        "- ☐ **기타형** — 사회적 목적의 실현 여부를 위의 항목에 포함되지 않는 경우\n\n"
        f"{social_service_type}\n\n"
        f"{cert_type}\n\n"
        "### 조직 형태\n\n"
        "| 항목 | 내용 |\n"
        "|------|------|\n"
        "| 현재 조직 형태 | |\n"
        "| 법적 실체 여부 | ☐ 있음 &nbsp; ☐ 없음 |\n"
        "| 정관/규약 보유 여부 | ☐ 있음 &nbsp; ☐ 없음 |\n\n"
        f"{governance_structure}\n\n"
        f"{cert_org}\n\n"
        "### 고용 계획\n\n"
        "| 구분 | 현재 | 6개월 후 | 1년 후 |\n"
        "|------|------|---------|--------|\n"
        "| 전체 고용인원 (명) | | | |\n"
        "| 취약계층 고용인원 (명) | | | |\n"
        "| 취약계층 비율 (%) | | | |\n\n"
        f"{vulnerable_employment_plan}\n\n"
        f"{cert_employment}\n\n"
        "### 의사결정 구조\n\n"
        "> (민주적 의사결정 구조 및 이해관계자 참여 방식을 서술하세요.)\n\n"
        f"{governance_structure}\n\n"
        f"{cert_org}\n\n"
        "### 정관 변경 계획\n\n"
        "> (인증 요건을 충족하기 위한 정관 변경 계획이 있는 경우 서술하세요.)\n\n"
        f"{cert_rules}\n\n"
        "---\n\n"
        "## 사회적 목적 실현을 위한 사업계획\n\n"
        "### 1. 사업 목적\n\n"
        "> (사회적 미션 및 해결하고자 하는 구체적 사회문제를 서술하세요.)\n\n"
        f"{social_mission}\n\n"
        f"{plan_purpose}\n\n"
        "### 2. 사업 내용\n\n"
        "| 항목 | 내용 |\n"
        "|------|------|\n"
        "| 주요 제품/서비스 | |\n"
        "| 수익 창출 모델 | |\n"
        "| 주요 고객(수혜자) | |\n\n"
        "> (제품/서비스 내용 및 수익 모델을 상세히 서술하세요.)\n\n"
        f"{plan_content}\n\n"
        "### 3. 사업 역량\n\n"
        "| 항목 | 내용 |\n"
        "|------|------|\n"
        "| 대표자 배경 및 관심계기 | |\n"
        "| 인력 전문성 | |\n"
        "| 자원 확보 방안 | |\n\n"
        f"{plan_capability}\n\n"
        "### 4. 사업 목표\n\n"
        "| 구분 | 1년차 | 2년차 | 3년차 |\n"
        "|------|------|------|------|\n"
        "| 매출액 (만원) | | | |\n"
        "| 고용인원 (명) | | | |\n"
        "| 사회적 목적 지표 | | | |\n\n"
        f"{plan_goals}\n\n"
        "---\n\n"
        "## 지정 이후 단계별 세부 추진 계획\n\n"
        "| 단계 | 기간 | 추진 내용 | 비고 |\n"
        "|------|------|----------|------|\n"
        "| 1단계 | | | |\n"
        "| 2단계 | | | |\n"
        "| 3단계 | | | |\n\n"
        "> (시설, 마케팅, 투자 등 구체적인 로드맵을 서술하세요.)\n\n"
        f"{roadmap_after_designation}\n\n"
        f"{year1}\n\n"
        f"{year2}\n\n"
        f"{year3}\n\n"
        f"{other_plan}\n"
    )


def _render_bm_diagnosis_markdown_form(plan: Dict[str, Any]) -> str:
    company_name = _form_value(plan, "company_name", default=str(plan.get("company_name") or plan.get("company") or ""))
    representative_name = _form_value(plan, "representative_name")
    business_registration_no = _form_value(plan, "business_registration_no")
    established_date = _form_value(plan, "established_date")
    address = _form_value(plan, "address")
    contact_phone = _form_value(plan, "contact_phone")
    main_business = _form_value(plan, "main_business")
    employee_count = _form_value(plan, "employee_count")
    recent_revenue = _form_value(plan, "recent_revenue", default=str(plan.get("recent_revenue") or plan.get("annual_revenue") or ""))
    main_revenue_source = _form_value(plan, "main_revenue_source")
    certification_status = _form_value(plan, "certification_status")
    ip_status = _form_value(plan, "ip_status")
    rnd_status = _form_value(plan, "rnd_status")
    investment_status = _form_value(plan, "investment_status")
    company_type = _to_markdown_cell(plan.get("company_type"))

    return (
        "# BM 진단서\n\n"
        "---\n\n"
        "## 기업 기본 현황\n\n"
        "| 항목 | 내용 | 항목 | 내용 | 항목 | 내용 |\n"
        "|------|------|------|------|------|------|\n"
        f"| 기업명 | {company_name} | 대표자명 | {representative_name} | 사업자등록번호 | {business_registration_no} |\n"
        f"| 설립년도 | {established_date} | 소재지 | {address} | 연락처 | {contact_phone} |\n"
        f"| 홈페이지 |  | 법인형태 |  | 기업유형 | {company_type} |\n"
        f"| 주요업종 |  | 주력사업내용 | {main_business} | 종사자수 | {employee_count} |\n"
        f"| 고용형태 |  | 최근매출 | {recent_revenue} | 주요수익원 | {main_revenue_source} |\n"
        f"| 정부사업 참여이력 |  | 기업인증보유현황 | {certification_status} | 지재권보유현황 | {ip_status} |\n"
        f"| 연구개발전담부서 |  | 투자현황 | {investment_status} | R&D 현황 | {rnd_status} |\n\n"
        "---\n\n"
        "## 지원 항목\n\n"
        "> 각 항목별 우선순위/등급(★)을 체크하세요.\n\n"
        "| 번호 | 항목 | 등급 | 해당여부 |\n"
        "|------|------|------|---------|\n"
        "| 1 | 중소기업 | ★ | ☐ |\n"
        "| 2 | 여성기업 | ★ | ☐ |\n"
        "| 3 | 장애인기업 | ★ | ☐ |\n"
        "| 4 | 협동조합 | ★ | ☐ |\n"
        "| 5 | 예비사회적기업 | ★★ | ☐ |\n"
        "| 6 | 소셜벤처 | ★★ | ☐ |\n"
        "| 7 | 창업기업 | ★★ | ☐ |\n"
        "| 8 | 성과공유기업 | ★★ | ☐ |\n"
        "| 9 | 벤처기업 | ★★★ | ☐ |\n"
        "| 10 | 이노비즈(기술) | ★★ | ☐ |\n"
        "| 11 | 메인비즈(경영) | ★★ | ☐ |\n"
        "| 12 | 녹색기업 | ★★★ | ☐ |\n"
        "| 13 | 사회적기업 | ★★★★ | ☐ |\n"
        "| 14 | R&D | ★★★★ | ☐ |\n"
        "| 15 | ESG관련인증 | ★★★ | ☐ |\n"
        "| 16 | 우수사회적기업 | ★★★★ | ☐ |\n"
        "| 17 | 혁신기업 | ★★★★ | ☐ |\n"
        "| 18 | 공공우수제품지정 | ★★★★★ | ☐ |\n"
        "| 19 | 강소기업 | ★★★★★ | ☐ |\n"
        "| 20 | 글로벌강소기업 | ★★★★★ | ☐ |\n\n"
        "---\n\n"
        "## 의견\n\n"
        "> (BM 진단 결과 및 종합 의견을 작성하세요.)\n\n"
        "&nbsp;\n\n"
        "&nbsp;\n\n"
        "&nbsp;\n\n"
        "&nbsp;\n\n"
        "&nbsp;\n\n"
        "&nbsp;\n\n"
        "&nbsp;\n\n"
        "---\n\n"
        "- 작성일: 2024년 &nbsp;&nbsp;&nbsp; 월 &nbsp;&nbsp;&nbsp; 일\n"
        "- 작성자: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; (인 또는 서명)\n\n"
        "---\n\n"
        "## BM 분석 (별첨)\n\n"
        "> (비즈니스 모델 분석 내용을 작성하세요.)\n\n"
        "&nbsp;\n\n"
        "&nbsp;\n\n"
        "&nbsp;\n\n"
        "&nbsp;\n"
    )


def _render_bm_diagnosis_html_form(plan: Dict[str, Any]) -> str:
    fields = _get_form_fields(plan)
    company_name = _to_html_cell(fields.get("company_name") or plan.get("company_name") or plan.get("company"))
    representative_name = _to_html_cell(fields.get("representative_name"))
    business_registration_no = _to_html_cell(fields.get("business_registration_no"))
    established_date = _to_html_cell(fields.get("established_date"))
    address = _to_html_cell(fields.get("address"))
    contact_phone = _to_html_cell(fields.get("contact_phone"))
    main_business = _to_html_cell(fields.get("main_business"))
    employee_count = _to_html_cell(fields.get("employee_count"))
    main_revenue_source = _to_html_cell(fields.get("main_revenue_source"))
    certification_status = _to_html_cell(fields.get("certification_status"))
    ip_status = _to_html_cell(fields.get("ip_status"))
    rnd_status = _to_html_cell(fields.get("rnd_status"))
    investment_status = _to_html_cell(fields.get("investment_status"))
    company_type = _to_html_cell(plan.get("company_type"))
    recent_revenue = _to_html_cell(fields.get("recent_revenue") or plan.get("recent_revenue") or plan.get("annual_revenue"))

    return (
        "<html><body style='font-family: Arial, sans-serif; padding: 24px; line-height: 1.5'>"
        "<h1>BM 진단서</h1>"
        "<hr/>"
        "<h2>기업 기본 현황</h2>"
        "<table style='width:100%; border-collapse:collapse; margin-bottom:16px' border='1' cellpadding='6'>"
        "<tr><th>항목</th><th>내용</th><th>항목</th><th>내용</th><th>항목</th><th>내용</th></tr>"
        f"<tr><td>기업명</td><td>{company_name}</td><td>대표자명</td><td>{representative_name}</td><td>사업자등록번호</td><td>{business_registration_no}</td></tr>"
        f"<tr><td>설립년도</td><td>{established_date}</td><td>소재지</td><td>{address}</td><td>연락처</td><td>{contact_phone}</td></tr>"
        f"<tr><td>홈페이지</td><td></td><td>법인형태</td><td></td><td>기업유형</td><td>{company_type}</td></tr>"
        f"<tr><td>주요업종</td><td></td><td>주력사업내용</td><td>{main_business}</td><td>종사자수</td><td>{employee_count}</td></tr>"
        f"<tr><td>고용형태</td><td></td><td>최근매출</td><td>{recent_revenue}</td><td>주요수익원</td><td>{main_revenue_source}</td></tr>"
        f"<tr><td>정부사업 참여이력</td><td></td><td>기업인증보유현황</td><td>{certification_status}</td><td>지재권보유현황</td><td>{ip_status}</td></tr>"
        f"<tr><td>연구개발전담부서</td><td></td><td>투자현황</td><td>{investment_status}</td><td>R&amp;D 현황</td><td>{rnd_status}</td></tr>"
        "</table>"
        "<hr/>"
        "<h2>지원 항목</h2>"
        "<p>각 항목별 우선순위/등급(★)을 체크하세요.</p>"
        "<table style='width:100%; border-collapse:collapse; margin-bottom:16px' border='1' cellpadding='6'>"
        "<tr><th>번호</th><th>항목</th><th>등급</th><th>해당여부</th></tr>"
        "<tr><td>1</td><td>중소기업</td><td>★</td><td>☐</td></tr>"
        "<tr><td>2</td><td>여성기업</td><td>★</td><td>☐</td></tr>"
        "<tr><td>3</td><td>장애인기업</td><td>★</td><td>☐</td></tr>"
        "<tr><td>4</td><td>협동조합</td><td>★</td><td>☐</td></tr>"
        "<tr><td>5</td><td>예비사회적기업</td><td>★★</td><td>☐</td></tr>"
        "<tr><td>6</td><td>소셜벤처</td><td>★★</td><td>☐</td></tr>"
        "<tr><td>7</td><td>창업기업</td><td>★★</td><td>☐</td></tr>"
        "<tr><td>8</td><td>성과공유기업</td><td>★★</td><td>☐</td></tr>"
        "<tr><td>9</td><td>벤처기업</td><td>★★★</td><td>☐</td></tr>"
        "<tr><td>10</td><td>이노비즈(기술)</td><td>★★</td><td>☐</td></tr>"
        "<tr><td>11</td><td>메인비즈(경영)</td><td>★★</td><td>☐</td></tr>"
        "<tr><td>12</td><td>녹색기업</td><td>★★★</td><td>☐</td></tr>"
        "<tr><td>13</td><td>사회적기업</td><td>★★★★</td><td>☐</td></tr>"
        "<tr><td>14</td><td>R&amp;D</td><td>★★★★</td><td>☐</td></tr>"
        "<tr><td>15</td><td>ESG관련인증</td><td>★★★</td><td>☐</td></tr>"
        "<tr><td>16</td><td>우수사회적기업</td><td>★★★★</td><td>☐</td></tr>"
        "<tr><td>17</td><td>혁신기업</td><td>★★★★</td><td>☐</td></tr>"
        "<tr><td>18</td><td>공공우수제품지정</td><td>★★★★★</td><td>☐</td></tr>"
        "<tr><td>19</td><td>강소기업</td><td>★★★★★</td><td>☐</td></tr>"
        "<tr><td>20</td><td>글로벌강소기업</td><td>★★★★★</td><td>☐</td></tr>"
        "</table>"
        "<hr/>"
        "<h2>의견</h2>"
        "<p>(BM 진단 결과 및 종합 의견을 작성하세요.)</p>"
        "<p>&nbsp;</p><p>&nbsp;</p><p>&nbsp;</p><p>&nbsp;</p><p>&nbsp;</p><p>&nbsp;</p><p>&nbsp;</p>"
        "<hr/>"
        "<p>- 작성일: 2024년 &nbsp;&nbsp;&nbsp; 월 &nbsp;&nbsp;&nbsp; 일</p>"
        "<p>- 작성자: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; (인 또는 서명)</p>"
        "<hr/>"
        "<h2>BM 분석 (별첨)</h2>"
        "<p>(비즈니스 모델 분석 내용을 작성하세요.)</p>"
        "<p>&nbsp;</p><p>&nbsp;</p><p>&nbsp;</p><p>&nbsp;</p>"
        "</body></html>"
    )


def _use_common_business_plan_template(plan: Dict[str, Any]) -> bool:
    keys = ("사회적기업", "사회적 기업", "사회적가치", "취약계층", "예비사회적")
    parts: list[str] = []

    for section_map_key in ("sections_markdown", "sections_html"):
        section_map = plan.get(section_map_key, {})
        if isinstance(section_map, dict):
            parts.extend(str(v) for v in section_map.values() if v is not None)

    analysis = plan.get("analysis")
    if analysis is not None:
        parts.append(json.dumps(analysis, ensure_ascii=False) if isinstance(analysis, dict) else str(analysis))
    for k in ("title", "company_type", "growth_stage"):
        v = plan.get(k)
        if v is not None:
            parts.append(str(v))

    merged = "\n".join(parts)
    return any(keyword in merged for keyword in keys)


async def render_business_plan_with_template(
    project_id: str,
    plan: Dict[str, Any],
    artifact_type: str = "business_plan",
    thread_id: str | None = None,
) -> Dict[str, Any]:
    policy_version = await get_project_policy_version(project_id, thread_id=thread_id)
    policy_state = await get_project_policy_state(project_id, thread_id=thread_id)
    state = (
        await get_or_create_conversation_state(project_id, POLICY_VERSION_V1, thread_id=thread_id)
        if policy_state is None and policy_version == POLICY_VERSION_V1
        else policy_state
    )
    if state is None:
        state = await get_or_create_conversation_state(project_id, POLICY_VERSION_LEGACY, thread_id=thread_id)
    stage_from_plan = str(plan.get("growth_stage", "") or "").strip()
    fallback_stage = stage_from_plan if stage_from_plan in {"예비", "초기", "성장", "공통"} else state.consultation_mode
    if artifact_type == "bm_diagnosis":
        fallback_stage = "공통"
    elif artifact_type == "business_plan" and _use_common_business_plan_template(plan):
        fallback_stage = "공통"
    elif fallback_stage not in {"예비", "초기", "성장", "공통"}:
        fallback_stage = "예비"

    if artifact_type == "bm_diagnosis":
        markdown = _render_bm_diagnosis_markdown_form(plan)
        field_status = _field_status_payload("bm_diagnosis", "공통", plan)
        return {
            "html": _render_bm_diagnosis_html_form(plan),
            "markdown": markdown,
            "template_id": None,
            **field_status,
        }

    template = await get_selected_template(
        project_id=project_id,
        artifact_type=artifact_type,
        fallback_stage=fallback_stage,
        thread_id=thread_id,
    )

    if template:
        if artifact_type == "business_plan" and fallback_stage == "예비":
            markdown = _render_business_plan_pre_startup_2025_markdown_form(plan)
            field_status = _field_status_payload("business_plan", "예비", plan)
            return {
                "html": _to_html(markdown),
                "markdown": markdown,
                "template_id": template.id,
                **field_status,
            }

        if artifact_type == "business_plan" and fallback_stage == "초기":
            markdown = _render_business_plan_early_startup_2023_markdown_form(plan)
            field_status = _field_status_payload("business_plan", "초기", plan)
            return {
                "html": _to_html(markdown),
                "markdown": markdown,
                "template_id": template.id,
                **field_status,
            }

        if artifact_type == "business_plan" and fallback_stage == "성장":
            markdown = _render_business_plan_scaleup_package_markdown_form(plan)
            field_status = _field_status_payload("business_plan", "성장", plan)
            return {
                "html": _to_html(markdown),
                "markdown": markdown,
                "template_id": template.id,
                **field_status,
            }

        if artifact_type == "business_plan" and fallback_stage == "공통":
            markdown = _render_business_plan_social_pre_cert_markdown_form(plan)
            field_status = _field_status_payload("business_plan", "공통", plan)
            return {
                "html": _to_html(markdown),
                "markdown": markdown,
                "template_id": template.id,
                **field_status,
            }

        required_sections = _resolve_template_sections_row(template)
        if not required_sections:
            raise HTTPException(
                status_code=422,
                detail={
                    "error_code": "POLICY_VALIDATION_FAILED",
                    "message": "템플릿 섹션 스키마 미정의",
                    "violations": [f"template_sections_schema_missing:{template.id}"],
                },
            )

        try:
            ctx = _build_template_context(plan, template)
            rendered = _V1_TEMPLATE_RENDERER.from_string(template.template_body).render(**ctx)
        except UndefinedError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "error_code": "POLICY_VALIDATION_FAILED",
                    "message": "템플릿 렌더링에 필요한 키가 누락되었습니다.",
                    "violations": [str(exc)],
                    "required_sections": required_sections,
                },
            )
        if "<" in rendered and "</" in rendered:
            html = rendered
        else:
            html = _to_html(rendered)
        markdown = rendered
    else:
        if artifact_type in {"business_plan", "bm_diagnosis"}:
            raise HTTPException(
                status_code=422,
                detail={
                    "error_code": "POLICY_VALIDATION_FAILED",
                    "message": "활성 템플릿을 찾을 수 없어 산출물을 렌더링할 수 없습니다.",
                    "violations": [f"template_not_found:{artifact_type}:{fallback_stage}"],
                },
            )
        markdown = "\n\n".join(
            f"## {s.get('title', 'Section')}\n{s.get('content', '')}" for s in plan.get("sections", [])
        )
        html = render_business_plan_html(plan)

    return {
        "html": html,
        "markdown": markdown,
        "template_id": template.id if template else None,
        **_field_status_payload(artifact_type, fallback_stage, plan),
    }


def validate_growth_mode_policy(
    growth_mode: str,
    roadmap: Dict[str, Any],
    matching: Dict[str, Any],
) -> None:
    if growth_mode != CONSULTATION_MODE_GROWTH:
        return

    if not isinstance(roadmap, dict):
        return
    yearly_plan = roadmap.get("yearly_plan", [])
    violations = []

    def _check_text_block(text: str, tag: str):
        for keyword in GROWTH_MODE_POLICY_RULES:
            if keyword in text:
                violations.append(f"{tag}:{keyword}")
                break

    for idx, year in enumerate(yearly_plan, start=1):
        for action in year.get("actions", []):
            _check_text_block(str(action), f"roadmap.y{idx}.action")
        for goal in year.get("goals", []):
            _check_text_block(str(goal), f"roadmap.y{idx}.goal")
        if not year.get("kpis") and not year.get("KPIs"):
            violations.append(f"roadmap.y{idx}: KPI 미정의")

    for item in matching.get("items", []) if isinstance(matching, dict) else []:
        _check_text_block(str(item.get("name", "")), "matching.item")

    if violations:
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": "POLICY_VALIDATION_FAILED",
                "message": "성장 모드 전략 심화 출력 정책 위반",
                "violations": sorted(set(violations)),
            },
        )
