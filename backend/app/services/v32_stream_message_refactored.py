# -*- coding: utf-8 -*-
"""
v3.2 stream_message - Refactored
오케스트레이션만 수행 (실제 로직은 전부 위임)
"""
import uuid
import re
import json
import asyncio
from typing import AsyncGenerator, List, Dict, Any, Optional

from app.models.stream_context import StreamContext
from app.models.master import ChatMessage, ConversationMode, MasterIntent # [v4.0]
from app.models.company import CompanyProfile, CompanyType
from app.core.database import (
    save_message_to_rdb,
    resolve_thread_id_for_project,
    get_messages_from_rdb,
)
from app.services.knowledge_service import knowledge_queue # [v4.2] Knowledge Ingestion
from app.services.growth_v1_controls import set_project_active_mode
from app.services.growth_v1_controls import (
    get_approval_state_dict,
    get_plan_profile_slots,
    update_approval_step,
)
from app.services.intent_router import _derive_plan_active_mode
from app.services.growth_support_service import growth_support_service

# Step 함수들 import
from app.services.intent_router import (
    parse_user_input,
    classify_intent,
    resolve_plan_execution_flow,
    reserve_plan_question_slot,
    PLAN_ROUTING_LEGACY,
    PLAN_ROUTING_QUESTION_FLOW,
    PLAN_ROUTING_DRAFT_SECTIONS,
    PLAN_ROUTING_FREEFLOW,
    _derive_plan_active_mode,
)
from app.services.shadow_mining import extract_shadow_draft
from app.services.mes_sync import load_current_mes_and_state, sync_mes_if_needed, compute_mes_hash
from app.services.response_builder import handle_function_read, handle_function_write_gate, response_builder


from app.services.debug_service import debug_service  # [v4.2]
from app.schemas.debug import DebugInfo, RetrievalChunk, RetrievalDebug  # [v4.2]


PLAN_SEED_KEYWORDS = [
    "사업계획서",
    "창업",
    "창업준비",
    "템플릿",
    "양식",
    "지원사업",
    "정부지원",
    "사회적기업",
    "예비",
    "초기",
    "성장",
    "써",
    "작성",
    "도와",
    "쓸",
]


def _is_plan_seed_message(text: str) -> bool:
    if not text:
        return False
    norm = text.replace(" ", "")
    return any(keyword in norm for keyword in PLAN_SEED_KEYWORDS)


def _build_plan_master_prompt(ctx: StreamContext, relevant_context: str) -> str:
    is_summary = ctx.plan_intent == "PLAN_SUMMARY"
    context_block = f"\n[Context Identified]\n[관련 지식/대화]\n{relevant_context}\n" if relevant_context else ""
    summary_extra = (
        "지금은 '요약 요청' 단계입니다.\n"
        "사용자에게 질문하지 말고, 지금까지 수집된 내용만 근거로 3~5문장 내로 핵심을 정리하세요.\n"
        "포함 항목: 회사/제품, 타깃 고객, 해결 과제, 매출·자금 상태, 남은 수집 항목.\n"
        "요약 후 바로 확인 질문을 덧붙여주세요.\n"
        "예: '이 요약이 맞으면 [맞아요/이대로 진행]으로 답해 주세요. 수정 필요하면 [수정]이라고 말해 주세요.'\n"
        if is_summary
        else ""
    )
    return (
        "당신은 AIBizPlan의 사업계획서 상담 AI입니다.\n"
        "오늘 목표는 사용자가 작성할 수 있도록 질문을 통해 필요한 정보를 수집하고,\n"
        "5개 템플릿(사업계획서 4종 + BM진단)로 갈 수 있게 흐름을 고정하는 것입니다.\n\n"
        f"{summary_extra}"
        "응답 규칙:\n"
        "1) 바로 초안·요약·최종본을 쓰지 말고, 1개 질문으로만 다음 단계를 진행하세요.\n"
        " - 예외: 요약 요청 단계일 땐 질문 없이 한 번에 정리해서 응답합니다.\n"
        "2) 질문은 사용자 답변이 짧아도 수집 가능한 항목으로 분리해서 안내하세요.\n"
        "3) 템플릿 명칭·작성/검토 결과를 강요하지 말고, 먼저 회사/사업 핵심 정보(회사명, 업종, 고객, 매출/자금)부터 묻습니다.\n"
        "4) 모르는 항목은 '잘 모르겠어요'로 답할 수 있다고 알려주세요.\n"
        "5) START TASK, READY_TO_START, MISSION READINESS 같은 시스템 메시지는 출력하지 마세요.\n"
        "6) 필요시 사용자 입력을 반영해 다음 질문 1개만 제시하세요.\n"
        f"{context_block}"
    )


def _infer_company_name_from_text(text: str) -> str:
    patterns = [
        r"회사명(?:은|:)?\s*([A-Za-z0-9가-힣_\-]+)",
        r"기업명(?:은|:)?\s*([A-Za-z0-9가-힣_\-]+)",
        r"^([A-Za-z0-9가-힣_\-]+)\s*(?:,|$)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.MULTILINE)
        if m and m.group(1):
            candidate = m.group(1).strip()
            if candidate and candidate not in {"업종", "팀", "창업자", "대표", "회사"}:
                return candidate
    return "회사명 미입력"


def _extract_company_profile_facts(text: str) -> dict:
    raw = (text or "").strip()
    if not raw:
        return {}
    facts: dict[str, str] = {}
    norm = raw.replace("\n", " ")

    company_name = _infer_company_name_from_text(norm)
    if company_name and company_name != "회사명 미입력":
        facts["company_name"] = company_name

    industry_patterns = [
        r"업종(?:은|:)?\s*([A-Za-z0-9가-힣\s/_-]+?)(?:,|\.|$)",
        r"산업(?:은|:)?\s*([A-Za-z0-9가-힣\s/_-]+?)(?:,|\.|$)",
    ]
    for pattern in industry_patterns:
        m = re.search(pattern, norm)
        if m and m.group(1):
            facts["industry"] = m.group(1).strip()
            break

    team_patterns = [
        r"팀(?:은|:)?\s*([A-Za-z0-9가-힣\s,_-]+?)(?:,|\.|$)",
        r"(창업자\s*\d+명(?:\s*개발자\s*\d+명)?)",
    ]
    for pattern in team_patterns:
        m = re.search(pattern, norm)
        if m and m.group(1):
            facts["team"] = m.group(1).strip()
            break

    return facts


def _infer_item_description_from_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in reversed(lines):
        if len(line) >= 5 and "사업계획서" not in line:
            return line[:500]
    return "사업 아이템 상세 정보 확인 필요"


def _mode_to_profile_defaults(mode: Optional[str]) -> tuple[CompanyType, float]:
    if mode == "성장":
        return (CompanyType.GROWTH_STAGE, 2_000_000_000.0)
    if mode == "초기":
        return (CompanyType.EARLY_STAGE, 200_000_000.0)
    return (CompanyType.PRE_ENTREPRENEUR, 0.0)


def _detect_target_artifact(user_text_norm: str) -> str:
    if any(token in user_text_norm for token in ["bm진단", "bm 진단", "진단양식", "비엠진단"]):
        return "bm_diagnosis"
    return "business_plan"


def _progress_event_json(step_message: str) -> str:
    return json.dumps(
        {
            "type": "PIPELINE_PROGRESS",
            "message": step_message,
        },
        ensure_ascii=False,
    )


def _build_profile_from_context(ctx: StreamContext, current_message: str) -> CompanyProfile:
    user_texts: List[str] = []
    for msg in ctx.history or []:
        role = getattr(msg, "role", None)
        if role == "user":
            user_texts.append(getattr(msg, "content", "") or "")
    user_texts.append(current_message or "")
    merged = "\n".join(user_texts)
    company_name = _infer_company_name_from_text(merged)
    item_description = _infer_item_description_from_text(merged)
    company_type, annual_revenue = _mode_to_profile_defaults(ctx.consultation_mode)

    return CompanyProfile(
        company_name=company_name,
        item_description=item_description,
        annual_revenue=annual_revenue,
        classified_type=company_type,
    )


async def _auto_generate_draft_artifact(ctx: StreamContext, message: str) -> AsyncGenerator[str, None]:
    slots = await get_plan_profile_slots(ctx.project_id, thread_id=ctx.thread_id)
    company_profile = (slots.get("company_profile") or "").strip()
    target_problem = (slots.get("target_problem") or "").strip()
    revenue_funding = (slots.get("revenue_funding") or "").strip()
    financing_topic = (slots.get("financing_topic") or "").strip()

    base_profile = _build_profile_from_context(ctx, message)
    profile_facts = _extract_company_profile_facts(company_profile)
    company_name = profile_facts.get("company_name") or (
        _infer_company_name_from_text(company_profile) if company_profile else base_profile.company_name
    )
    item_parts = [p for p in [target_problem, revenue_funding, financing_topic] if p]
    item_description = " / ".join(item_parts)[:2000] if item_parts else base_profile.item_description
    profile = CompanyProfile(
        company_name=company_name or base_profile.company_name,
        item_description=item_description or base_profile.item_description,
        annual_revenue=base_profile.annual_revenue,
        classified_type=base_profile.classified_type,
        metadata={
            "industry": profile_facts.get("industry", ""),
            "team": profile_facts.get("team", ""),
            "company_profile_raw": company_profile,
            "target_problem_raw": target_problem,
            "revenue_funding_raw": revenue_funding,
            "financing_topic_raw": financing_topic,
        },
    )
    artifact_type = _detect_target_artifact((ctx.user_input_norm or "").replace(" ", ""))
    pipeline_input_text = "\n".join(
        [f"회사/팀: {company_profile}", f"고객/문제: {target_problem}", f"매출/자금: {revenue_funding}", f"보강주제: {financing_topic}"]
    ).strip()
    progress_queue: asyncio.Queue[str] = asyncio.Queue()

    async def _on_progress(step_message: str) -> None:
        await progress_queue.put(_progress_event_json(step_message))

    pipeline_task = asyncio.create_task(
        growth_support_service.run_pipeline(
            project_id=ctx.project_id,
            profile=profile,
            input_text=pipeline_input_text or message,
            progress_callback=_on_progress,
        )
    )

    while True:
        if pipeline_task.done() and progress_queue.empty():
            break
        try:
            progress_event = await asyncio.wait_for(progress_queue.get(), timeout=0.1)
            yield f"{progress_event}\n"
        except asyncio.TimeoutError:
            continue

    result = await pipeline_task
    artifact = (result.get("artifacts") or {}).get(artifact_type) or {}
    template_id = artifact.get("template_id")
    missing_field_guides = artifact.get("missing_field_guides", []) or []
    missing_field_keys = artifact.get("missing_field_keys", []) or []
    thread_query = f"&threadId={ctx.thread_id}" if ctx.thread_id else ""
    html_path = f"/api/v1/projects/{ctx.project_id}/artifacts/{artifact_type}?format=html{thread_query}"
    pdf_path = f"/api/v1/projects/{ctx.project_id}/artifacts/{artifact_type}?format=pdf{thread_query}"
    approval_path = f"/api/v1/projects/{ctx.project_id}/artifacts/{artifact_type}/approval"
    if ctx.thread_id:
        approval_path = f"{approval_path}?threadId={ctx.thread_id}"

    completed_steps = 0
    total_steps = 4 if artifact_type == "business_plan" else 0
    missing_steps: list[str] = []
    missing_step_guides: list[str] = []
    if artifact_type == "business_plan":
        try:
            if template_id:
                await update_approval_step(
                    project_id=ctx.project_id,
                    artifact_type="business_plan",
                    step="template_selected",
                    approved=True,
                    thread_id=ctx.thread_id,
                )
        except Exception:
            pass
        try:
            approval_state = await get_approval_state_dict(
                ctx.project_id,
                "business_plan",
                thread_id=ctx.thread_id,
            )
            approval_steps = (
                "key_figures_approved",
                "certification_path_approved",
                "template_selected",
                "summary_confirmed",
            )
            completed_steps = sum(
                1 for step in approval_steps
                if approval_state.get(step)
            )
            missing_steps = [step for step in approval_steps if not approval_state.get(step)]
            missing_step_guides = approval_state.get("missing_step_guides", []) or []
        except Exception:
            completed_steps = 0
            missing_steps = ["key_figures_approved", "certification_path_approved", "template_selected", "summary_confirmed"]
            missing_step_guides = []

    message_lines = [
        "초안 생성이 완료되었습니다.",
        "아래 버튼에서 결과를 확인해 주세요.",
    ]
    if artifact_type == "business_plan":
        message_lines.append("PDF는 승인 4단계를 완료하면 다운로드할 수 있습니다.")
        message_lines.append(f"현재 승인 진행: {completed_steps}/4")
        if missing_steps:
            message_lines.append("남은 단계는 아래 안내를 따라 순서대로 완료해 주세요.")
    if missing_field_guides:
        message_lines.append("양식 자동입력에 필요한 추가 정보가 있습니다. 아래 가이드를 확인해 주세요.")

    signal_payload = {
        "type": "ARTIFACT_ACTIONS",
        "artifact_type": artifact_type,
        "html_url": html_path,
        "pdf_url": pdf_path if artifact_type == "business_plan" else None,
        "approval_url": approval_path if artifact_type == "business_plan" else None,
        "completed_steps": completed_steps,
        "total_steps": total_steps,
        "missing_steps": missing_steps,
        "missing_step_guides": missing_step_guides,
        "missing_field_keys": missing_field_keys,
        "missing_field_guides": missing_field_guides,
    }
    ctx.routing_message = "\n".join(message_lines) + "\n" + json.dumps(signal_payload, ensure_ascii=False)


async def stream_message_v32(
    message: str,
    history: List[ChatMessage],
    project_id: str = None,
    thread_id: str = None,
    user: Any = None,
    worker_status: Dict[str, Any] = None,
    request_id: str = "",  # [v4.2]
    is_admin: bool = False,  # [v4.2]
    mode: ConversationMode = ConversationMode.NATURAL, # [v4.0]
    mode_change_origin: str = "auto",
) -> AsyncGenerator[str, None]:
    """
    [v3.2 Refactored] stream_message 오케스트레이션 (<= 200줄)
    """
    # ===== 초기화 =====
    resolved_thread_id = await resolve_thread_id_for_project(
        project_id=project_id,
        requested_thread_id=thread_id,
        create_if_missing=True,
        owner_user_id=(getattr(user, "id", None) if getattr(user, "role", None) == "standard_user" else None),
    )
    messages_for_thread: List[Dict[str, Any]] = []
    if resolved_thread_id:
        messages = await get_messages_from_rdb(
            project_id=project_id,
            thread_id=resolved_thread_id,
            limit=200,
        )
        messages_for_thread = [
            {
                "role": msg.sender_role,
                "content": msg.content,
                "timestamp": msg.timestamp.isoformat() if getattr(msg, "timestamp", None) else None,
                "request_id": getattr(msg, "request_id", None),
            }
            for msg in messages
            if msg and msg.content
        ]
        # 핵심: 스레드 최초 진입 시(메시지 없음) 이전 방 히스토리가 섞이지 않도록 항상 현재 스레드 기준으로 초기화.
        history = [ChatMessage(**h) for h in messages_for_thread] if messages_for_thread else []

    session_id = resolved_thread_id or str(uuid.uuid4())
    user_id = user.id if user else "system"
    thread_messages = await get_messages_from_rdb(
        project_id=project_id,
        thread_id=resolved_thread_id,
        limit=1,
    ) if resolved_thread_id else []
    is_thread_new = not bool(thread_messages)

    # 요청 토글 값(프론트에서 보낸 모드)과 persist 모드(프로젝트/스레드 기준) 분리 관리
    requested_mode = mode

    # Resume latest persisted runtime mode for this project (thread-bound continuity)
    resolved_mode = mode
    if project_id:
        from app.services.growth_v1_controls import get_project_policy_state

        state = await get_project_policy_state(project_id, thread_id=resolved_thread_id)
        active = getattr(state, "active_mode", None) if state else None
        if active in {"NATURAL", "REQUIREMENT", "FUNCTION"}:
            resolved_mode = ConversationMode(active)

    requested_origin = (mode_change_origin or "auto").strip().lower()
    if requested_origin not in {"auto", "user"}:
        requested_origin = "auto"

    requested_mode_in_scope = resolved_mode
    try:
        requested_value = requested_mode.value if isinstance(requested_mode, ConversationMode) else str(requested_mode)
        if requested_value in {mode.value for mode in ConversationMode}:
            requested_mode_in_scope = ConversationMode(requested_value)
    except Exception:
        requested_mode_in_scope = resolved_mode

    # 프론트가 modeChangeOrigin=user를 상시 전송해도
    # 실제 토글 변경이 없으면 자동 전환으로 간주해 안내문 오출력을 막는다.
    is_explicit_user_toggle = (
        requested_origin == "user"
        and requested_mode_in_scope != resolved_mode
    )
    if not is_explicit_user_toggle:
        requested_origin = "auto"
    
    ctx = StreamContext(
        session_id=session_id,
        project_id=project_id or "system-master",
        thread_id=resolved_thread_id,
        user_id=user_id,
        user_input_raw=message,
        history=history,
        is_first_login_entry=(not bool(history) and is_thread_new),
        is_new_room_first_entry=(not bool(history) and bool(resolved_thread_id) and is_thread_new),
        request_id=request_id,  # [v4.2]
        is_admin=is_admin,      # [v4.2]
        mode=(requested_mode_in_scope if requested_origin == "user" else resolved_mode),     # [v4.0]
        # 사용자 토글(또는 클라이언트 기본값)이 실제 어떤 값인지 추적
        # 초기 판단에서는 resolved_mode를 SSOT로 사용하고, 새 모드 산출 시 user/auto 원인 분기
        mode_switch_origin=requested_origin,
    )
    
    ctx.add_log("stream_message", f"=== v3.2 stream_message started (Mode: {mode}) ===")
    
    # ===== Step 1: 입력 정규화 =====
    ctx = parse_user_input(ctx)
    
    # ===== Step 2: Intent 분류 (LLM 기반 맥락 판단) =====
    ctx = await classify_intent(ctx)

    # ===== Step 2b: v1.0 Plan Routing Guard (state-first) =====
    await resolve_plan_execution_flow(ctx)
    if ctx.routing_state == PLAN_ROUTING_QUESTION_FLOW:
        # 서버 슬롯 할당은 프런트 힌트 이전, 이 단계에서 먼저 반영
        await reserve_plan_question_slot(ctx)
        # reserve 단계에서 질문 상태가 초안 단계로 전이될 수 있으므로
        # 같은 턴에서 즉시 초안 생성을 이어서 수행한다.
        if ctx.routing_state == PLAN_ROUTING_DRAFT_SECTIONS:
            try:
                async for progress_event in _auto_generate_draft_artifact(ctx, message):
                    yield progress_event
            except Exception as exc:
                ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
                ctx.routing_error = {
                    "error_code": "POLICY_VALIDATION_FAILED",
                    "message": "초안 생성 중 오류가 발생했습니다. 입력 정보를 보강한 뒤 다시 시도해 주세요.",
                    "violations": [str(exc)],
                }
                ctx.routing_message = "핵심 정보(회사명/업종/고객/매출)를 조금 더 알려주시면 다시 생성할 수 있어요."
    elif ctx.routing_state == PLAN_ROUTING_DRAFT_SECTIONS:
        try:
            async for progress_event in _auto_generate_draft_artifact(ctx, message):
                yield progress_event
        except Exception as exc:
            ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
            ctx.routing_error = {
                "error_code": "POLICY_VALIDATION_FAILED",
                "message": "초안 생성 중 오류가 발생했습니다. 입력 정보를 보강한 뒤 다시 시도해 주세요.",
                "violations": [str(exc)],
            }
            ctx.routing_message = "핵심 정보(회사명/업종/고객/매출)를 조금 더 알려주시면 다시 생성할 수 있어요."

    skip_planner_llm = ctx.routing_state != PLAN_ROUTING_LEGACY

    # ===== [v4.0] Auto Mode Switch (Routing-first, fallback to intent) =====
    new_mode = ConversationMode.NATURAL
    if ctx.routing_state == PLAN_ROUTING_LEGACY:
        if ctx.primary_intent == MasterIntent.REQUIREMENT:
            new_mode = ConversationMode.REQUIREMENT
        elif ctx.primary_intent in [MasterIntent.FUNCTION_WRITE, MasterIntent.FUNCTION_READ]:
            new_mode = ConversationMode.FUNCTION
    else:
        new_mode = ConversationMode(_derive_plan_active_mode(ctx.routing_state))

    # If mode changed, update context and flag it
    if new_mode != ctx.mode:
        ctx.add_log("mode_switch", f"Auto-switching mode: {ctx.mode} -> {new_mode}")
        ctx.mode = new_mode
        ctx.mode_switched = True

        # origin은 요청에서 명시적으로 전달된 경우에만 user로 인정한다.
        # (자동 라우팅 결과와 우연히 일치하는 경우를 user로 오판하지 않음)
        ctx.mode_switch_origin = "user" if requested_origin == "user" else "auto"

        # 자동 전환은 UI 가이드 억제를 위해 모드 변경만 반영하고, SSOT 동기화는 사용자 전환 시점에만 저장.
        if ctx.mode_switch_origin == "user":
            try:
                await set_project_active_mode(
                    ctx.project_id,
                    new_mode.value,
                    thread_id=resolved_thread_id,
                )
            except Exception as exc:
                ctx.add_log("mode_switch", f"project active mode persist failed: {exc}")

        # Yield Mode Switch Signal immediately
        import json

        signal = json.dumps({
            "type": "MODE_SWITCH",
            "mode": new_mode.value,
            "origin": ctx.mode_switch_origin,
            "reason": f"Routing state: {ctx.routing_state}",
        })
        yield f"{signal}\n"
    
    # ===== Step 3: MES 및 Verification 상태 로드 =====
    ctx = await load_current_mes_and_state(ctx)
    
    # ===== Step 4: Shadow Draft 추출 (조건부) =====
    # v1.0 plan routing 진행 시 미확정 단계는 shadow draft 추출만 수행해 대화 메모리 보조
    if ctx.routing_state == PLAN_ROUTING_LEGACY and (ctx.primary_intent == "NATURAL" or "HAS_BRAINSTORM_SIGNAL" in ctx.flags):
        ctx = await extract_shadow_draft(ctx)
    
    # ===== Step 5: MES 동기화 (조건부) =====
    if ctx.routing_state == PLAN_ROUTING_LEGACY and ctx.primary_intent == "REQUIREMENT":
        ctx = await sync_mes_if_needed(ctx)
    
    # ===== Step 6: MES Hash 재계산 =====
    if ctx.mes:
        ctx.mes_hash = compute_mes_hash(ctx.mes)
    
    # ===== Step 7: FUNCTION_READ 처리 (조건부) =====
    if ctx.routing_state == PLAN_ROUTING_LEGACY and ctx.primary_intent == "FUNCTION_READ":
        ctx = await handle_function_read(ctx)
    
    # ===== Step 8: FUNCTION_WRITE Gate 평가 (조건부) =====
    if ctx.routing_state == PLAN_ROUTING_LEGACY and ctx.primary_intent == "FUNCTION_WRITE":
        ctx = await handle_function_write_gate(ctx)
    
    # ===== Step 9: Response Builder =====
    ctx = response_builder(ctx)
    
    # ===== [v3.2.1 FIX] NATURAL, TOPIC_SHIFT, REQUIREMENT intent일 때는 LLM 호출 (OPENROUTER 통일) =====
    if not skip_planner_llm and ctx.primary_intent in ["NATURAL", "TOPIC_SHIFT", "REQUIREMENT"]:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
        from app.core.config import settings
        from app.services.embedding_service import embedding_service
        from app.core.vector_store import PineconeClient
        from app.core.neo4j_client import neo4j_client
        
        try:
            # [중요] OPENROUTER로 통일 (Provider 분기 금지)
            llm = ChatOpenAI(
                model="google/gemini-2.0-flash-001",  # Flash급 모델 사용
                api_key=settings.OPENROUTER_API_KEY,
                base_url="https://openrouter.ai/api/v1",
                temperature=0.7,
            )
            
            # [신규] Vector DB 검색 (의미 기반 맥락)
            relevant_context = ""
            try:
                # 사용자 질문 임베딩 생성
                query_embedding = await embedding_service.generate_embedding(message)
                
                # Vector DB 검색 (대화 청크 + 지식 그래프)
                vector_client = PineconeClient()
                
                # 1. 지식 검색 (Priority)
                knowledge_results = await vector_client.query_vectors(
                    tenant_id=ctx.project_id,
                    vector=query_embedding,
                    top_k=3,
                    namespace="knowledge"
                )
                
                # 2. 대화 이력 검색 (Secondary)
                conversation_results = await vector_client.query_vectors(
                    tenant_id=ctx.project_id,
                    vector=query_embedding,
                    top_k=2,
                    filter_metadata={"source": "conversation"},
                    namespace="conversation"
                )
                
                vector_results = knowledge_results + conversation_results
                
                # [v4.2] Vector 결과를 DebugInfo에 저장
                if ctx.is_admin and vector_results:
                    debug_chunks = []
                    for idx, res in enumerate(vector_results):
                        meta = res.get("metadata", {})
                        
                        title_val = meta.get("title")
                        text_val = meta.get("text") or meta.get("summary", "(원문 없음)")
                        
                        # [v4.2 FIX] Fallback title from text if missing
                        if not title_val or title_val == "Untitled":
                            title_val = (text_val[:30] + "...") if text_val and len(text_val) > 1 else "No Title"

                        chunk = RetrievalChunk(
                            rank=idx + 1,
                            score=res.get("score", 0.0),
                            title=title_val,
                            text=text_val,
                            source_message_id=meta.get("source_message_id"),
                            node_id=meta.get("node_id"),  # [v5.0 Critical] Neo4j ID for tab navigation
                            type=meta.get("type", "Concept"),  # [v5.0] Node type for UI
                            metadata=meta
                        )
                        debug_chunks.append(chunk)
                    
                    ctx.debug_info.retrieval.chunks = debug_chunks
                
                # [v5.0 Critical Fix] Admin Debug Info 즉시 저장 (404 방지)
                if ctx.is_admin and ctx.request_id:
                    try:
                        await debug_service.save_debug_info(ctx.request_id, ctx.debug_info)
                        ctx.add_log("debug_cache", f"Debug info cached immediately for request {ctx.request_id}")
                    except Exception as e:
                        ctx.add_log("debug_cache", f"Failed to cache debug info: {e}")
                
                # 맥락 구성 (지식 우선)
                relevant_context = ""
                context_parts = []
                
                if knowledge_results:
                    context_parts.append("=== [지식 베이스] ===")
                    for i, res in enumerate(knowledge_results):
                        meta = res.get("metadata", {})
                        text = meta.get("text") or meta.get("summary", "")
                        context_parts.append(f"[{i+1}] (유사도: {res['score']:.2f}) {text}")
                        
                if conversation_results:
                    context_parts.append("\n=== [과거 대화] ===")
                    for i, res in enumerate(conversation_results):
                        meta = res.get("metadata", {})
                        text = meta.get("text") or meta.get("summary", "")
                        context_parts.append(f"[{i+1}] (유사도: {res['score']:.2f}) {text}")

                if context_parts:
                    relevant_context = "\n".join(context_parts)
                    ctx.add_log("vector_search", f"Found {len(knowledge_results)} knowledge chunks, {len(conversation_results)} chat chunks")
                    
                    # [Test Log] Proof of Knowledge Persistence (Requested by User)
                    print(f"DEBUG: [Knowledge Persistence] Project {ctx.project_id} - Loaded {len(knowledge_results)} Graph/Vector nodes for New Chat context.")
            except Exception as e:
                ctx.add_log("vector_search", f"Vector search failed: {e}")
                # Vector 검색 실패는 무시하고 계속 진행
            
            # [v3.2.1 FIX] 직전 대화 이력 로드 (최근 10개)
            recent_messages = await get_messages_from_rdb(
                project_id=ctx.project_id,
                thread_id=ctx.thread_id,
                limit=10
            )
            
            ctx.add_log("llm_context", f"Loaded {len(recent_messages)} recent messages for context")
            
            # 시스템 프롬프트 (intent별 차별화)
            if ctx.primary_intent == "REQUIREMENT":
                # 사업계획서 시드 문구가 들어오면 PLAN 마스터 프롬프트로 고정
                if _is_plan_seed_message(message) or _is_plan_seed_message(ctx.user_input_norm):
                    system_prompt = _build_plan_master_prompt(ctx, relevant_context)
                else:
                    # REQUIREMENT: MES 정보 포함
                    agents_count = len(ctx.mes.get("agents", []))
                    mes_info = (
                        f"현재 프로젝트에는 {agents_count}개의 에이전트가 등록되어 있습니다."
                        if agents_count > 0
                        else "아직 에이전트가 등록되지 않았습니다."
                    )
                    
                    # Vector Context 추가
                    vector_context_str = ""
                    if relevant_context:
                        vector_context_str = (
                            f"\n[Context Identified]\n[관련 지식/대화]\n{relevant_context}\n"
                        )
                        print(
                            f"DEBUG: [RAG Injection] Injected {len(relevant_context)} chars of context into System Prompt."
                        )
                    
                    system_prompt = f"""당신은 프로젝트 관리를 돕는 AI 어시스턴트입니다.

[현재 프로젝트 상태]
{mes_info}
{vector_context_str}

사용자의 요구사항을 이해하고 다음 단계를 안내하세요:
- 프로젝트를 만들고 싶다면: 프로젝트 생성 절차 안내
- 에이전트를 추가하고 싶다면: 에이전트 추가 방법 안내
- 설정을 변경하고 싶다면: 설정 변경 방법 안내

호칭은 '사용자님'을 사용하세요.
자연스럽고 도움이 되는 답변을 제공하세요.
START TASK, READY_TO_START 같은 시스템 메시지는 출력하지 마세요."""
            else:
                # NATURAL/TOPIC_SHIFT: 일반 대화
                vector_context_str = ""
                if relevant_context:
                    vector_context_str = f"\n[Context Identified]\n[관련 지식/대화]\n{relevant_context}\n위 관련 정보를 참고하되, 최신 대화 맥락을 우선하세요.\n"
                    print(f"DEBUG: [RAG Injection] Injected {len(relevant_context)} chars of context into System Prompt.")
                if _is_plan_seed_message(message) or _is_plan_seed_message(ctx.user_input_norm):
                    system_prompt = _build_plan_master_prompt(ctx, relevant_context)
                else:
                    system_prompt = f"""당신은 친절한 AI 어시스턴트입니다.
{vector_context_str}
사용자와 자연스럽게 대화하세요.
이전 대화 맥락을 기억하고 연속된 대화를 이어가세요.
호칭은 '사용자님'을 사용하세요.
짧고 간결하게 답변하세요.
운영 메뉴나 명령어 안내는 하지 마세요.
사용자가 사업계획서 작성을 명시적으로 요청하기 전에는 회사명/업종/팀 등 사업계획 수집 질문을 먼저 시작하지 마세요.
START TASK, READY_TO_START, MISSION READINESS 같은 시스템 메시지는 절대 출력하지 마세요."""
            
            # LLM 메시지 구성 (이전 대화 포함)
            messages = [SystemMessage(content=system_prompt)]
            
            # 최근 대화 이력 추가 (최대 10개)
            for msg in recent_messages[-10:]:
                if msg.sender_role == "user":
                    messages.append(HumanMessage(content=msg.content))
                elif msg.sender_role == "assistant":
                    messages.append(AIMessage(content=msg.content))
            
            # 현재 사용자 메시지 추가
            messages.append(HumanMessage(content=message))
            
            ctx.add_log("llm_context", f"Sending {len(messages)} messages to LLM (including {len(recent_messages)} history)")
            
            response = await llm.ainvoke(messages)
            ctx.final_response = response.content
            
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            ctx.add_log("stream_message", f"LLM error: {e}\n{error_trace}")
            print(f"CRITICAL LLM ERROR: {e}\n{error_trace}") # 콘솔에도 강제 출력
            ctx.final_response = "죄송합니다. 시스템 오류가 발생하여 요청을 처리할 수 없습니다. 관리자에게 문의해주세요."
    
    # ===== Step 10: 최종 응답 후처리 (모든 intent에 대해 1회만) =====
    ctx.final_response = _clean_response_final(ctx.final_response, ctx.primary_intent, ctx.write_gate_open)
    
    # ===== Step 11: 상태 저장 (persist_state) =====
    # [TODO] MES/Hash/Draft/verification_state를 Redis/DB에 저장
    # 지금은 메시지만 저장
    # [v4.2 Update] 사용자 메시지 저장 및 Knowledge Queue 등록
    user_msg_metadata = {
        "user_id": user_id,
        "routing_state": ctx.routing_state,
        "routing_message": ctx.routing_message,
        "plan_intent": ctx.plan_intent,
        "plan_confidence": ctx.plan_confidence,
        "consultation_mode": ctx.consultation_mode,
        "policy_version": ctx.policy_version,
        "active_template_id": ctx.active_template_id,
        "allocated_question_type": ctx.allocated_question_type,
        "question_counters_snapshot": ctx.question_counters_snapshot,
        "plan_data_version": ctx.plan_data_version,
        "summary_revision": ctx.summary_revision,
        "request_id": ctx.request_id,
    }
    user_msg_id, saved_thread_id = await save_message_to_rdb(
        "user",
        message,
        project_id,
        ctx.thread_id,
        metadata=user_msg_metadata,
    )
    
    # [KNOW-001] Knowledge Ingestion Trigger
    # 사용자의 메시지를 지식 큐에 등록하여 비동기로 처리 (중요도 필터링은 worker가 수행)
    try:
        if user_msg_id:
            knowledge_queue.put_nowait(user_msg_id)
            ctx.add_log("knowledge_ingestion", f"Message {user_msg_id} queued for knowledge processing")
    except Exception as e:
        ctx.add_log("knowledge_ingestion", f"Failed to queue message: {e}")

    assistant_msg_metadata = {
        "request_id": ctx.request_id,
        "primary_intent": ctx.primary_intent,
        "routing_state": ctx.routing_state,
        "plan_intent": ctx.plan_intent,
        "plan_confidence": ctx.plan_confidence,
        "mode": ctx.mode.value,
        "consultation_mode": ctx.consultation_mode,
        "policy_version": ctx.policy_version,
        "active_template_id": ctx.active_template_id,
        "question_counters_snapshot": ctx.question_counters_snapshot,
        "plan_data_version": ctx.plan_data_version,
        "summary_revision": ctx.summary_revision,
    }
    asst_msg_id, _ = await save_message_to_rdb(
        "assistant",
        ctx.final_response,
        project_id, 
        ctx.thread_id,
        metadata=assistant_msg_metadata,
    )
    
    # [v4.0] Auto-Ingestion for Requirement Mode (Assistant Response)
    if ctx.mode == ConversationMode.REQUIREMENT and asst_msg_id:
        try:
            knowledge_queue.put_nowait(asst_msg_id)
            ctx.add_log("knowledge_ingestion", f"Auto-ingesting Assistant Response {asst_msg_id} (Requirement Mode)")
        except Exception as e:
            ctx.add_log("knowledge_ingestion", f"Failed to auto-ingest assistant response: {e}")
    


    # [v4.2] Admin인 경우 Debug Info 캐싱 (TTL 10분)
    if ctx.is_admin and ctx.request_id:
        await debug_service.save_debug_info(ctx.request_id, ctx.debug_info)
    
    ctx.add_log("stream_message", "=== v3.2 stream_message completed ===")
    
    # ===== 최종 응답 스트리밍 =====
    
    # [v5.0] Admin 출처 호출 (Source Auditing)
    # 메시지 끝에 구분자와 함께 request_id를 메타데이터 형태로 전달하지 않고
    # 프론트엔드에서는 이미 응답 헤더의 X-Request-Id 또는 저장된 메시지의 metadata_json을 통해 확인하고 있습니다.
    # 하지만 사용자가 "출처 라인 호출"을 명시적으로 요청했으므로, 
    # 어드민인 경우 응답 끝에 보이지 않는 메타데이터나 특정 시그널을 추가할 수 있습니다.
    # 현재 프론트엔드(ChatInterface.tsx)는 msg.request_id가 있으면 자동으로 출처 바를 렌더링합니다.
    # 따라서 여기서 별도의 텍스트를 추가할 필요는 없지만, 확실한 동작을 위해 로그만 남깁니다.
    
    yield ctx.final_response
    yield "\n" # Ensure clean end


def _clean_response_final(content: str, intent: str, gate_open: bool) -> str:
    """
    [v3.2] 최종 응답 후처리 (모든 intent에 대해 1회만)
    
    제거 대상:
    - MISSION READINESS REPORT
    - READY_TO_START JSON (FUNCTION_WRITE + Gate Open이 아닌 경우)
    - 설정 오류 블록
    """
    import re
    
    # [Guardrail] FUNCTION_WRITE + Gate Open이 아니면 READY_TO_START 제거
    if intent != "FUNCTION_WRITE" or not gate_open:
        patterns = [
            # MISSION READINESS REPORT
            r"---\s*MISSION READINESS REPORT\s*---[\s\S]*?(?=\n\n|\Z)",
            r"\[준비 상태 점검 완료\][\s\S]*?(?=\n\n|\Z)",
            
            # READY_TO_START JSON
            r'```json\s*\{\s*"status"\s*:\s*"READY_TO_START"[\s\S]*?```',
            r'\{\s*"status"\s*:\s*"READY_TO_START"[\s\S]*?\}',
            
            # 조치 방법 가이드
            r"## 조치 방법 가이드[\s\S]*?(?=\n\n|\Z)",
            r"\*\*권장 조치:\*\*[\s\S]*?(?=\n\n|\Z)",
        ]
        
        for pattern in patterns:
            content = re.sub(pattern, "", content, flags=re.MULTILINE | re.DOTALL)
    
    # 연속 빈 줄 제거
    content = re.sub(r"\n{3,}", "\n\n", content)
    
    return content.strip()
