# -*- coding: utf-8 -*-
"""
Intent Router - v3.2
Intent 분류 전담 (Primary Intent 1개 + Secondary Flags)
"""
import re
import json
from typing import Tuple, List

from app.models.stream_context import StreamContext
from app.models.master import ChatMessage
from app.models.master import ConversationMode
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from fastapi import HTTPException
from app.core.database import get_messages_from_rdb

from app.services.growth_v1_controls import (
    POLICY_VERSION_LEGACY,
    POLICY_VERSION_V1,
    get_selected_template,
    get_project_policy_state,
    get_or_create_approval_state,
    get_plan_profile_slots,
    merge_plan_profile_slots,
    replace_plan_profile_slots,
    set_last_asked_slot,
    set_plan_suspended,
    set_project_active_mode,
    set_project_consultation_mode,
    set_project_policy_version,
    update_approval_step,
    update_question_counters,
)
from app.services.growth_v1_controls import CONSULTATION_MODE_PRELIMINARY, CONSULTATION_MODE_EARLY, CONSULTATION_MODE_GROWTH

# [v3.2 Guardrail] 명시적 confirm_token만 인정
CONFIRM_TOKENS = ["실행 확정", "변경 확정", "START TASK 실행"]

PLAN_INTENT_PROFILE_CAPTURE = "PLAN_PROFILE_CAPTURE"  # 초보자 정보 수집
PLAN_INTENT_TEMPLATE_SELECT = "PLAN_TEMPLATE_SELECT"  # 템플릿 추천/선택
PLAN_INTENT_DRAFT_SECTIONS = "PLAN_DRAFT_SECTIONS"  # 섹션 초안/작성
PLAN_INTENT_POLICY_CHECK = "PLAN_POLICY_CHECK"  # 요건/자격/지원 정책 체크
PLAN_INTENT_CORRECTION = "PLAN_CORRECTION"  # 텍스트 보정/수정 요청
PLAN_INTENT_SUMMARY = "PLAN_SUMMARY"  # 지금까지 내용 정리/요약
PLAN_INTENT_SUMMARY_CONFIRM = "PLAN_SUMMARY_CONFIRM"  # 요약 동의
PLAN_INTENT_SUMMARY_REVISE = "PLAN_SUMMARY_REVISE"  # 요약 반려/수정요청
PLAN_INTENT_UNKNOWN = "PLAN_UNKNOWN"

PLAN_UNKNOWN_FALLBACK_OPTIONS = [
    "회사/아이템 정보부터 정리",
    "지금까지 내용 요약/정리",
]

PLAN_KEYWORDS = {
    PLAN_INTENT_PROFILE_CAPTURE: [
        "회사명", "기업명", "아이템", "사업", "아이디어", "무엇부터", "어떻게", "처음", "잘 모르겠", "지원사업", "정부",
        "매출", "연매출", "매출액", "목표", "고객", "업력", "직원", "비즈니스", "무슨", "어떤", "도와줘",
        "도와주세요", "기초", "정보", "회사 소개", "업종", "산업", "자문", "상담",
    ],
    PLAN_INTENT_TEMPLATE_SELECT: [
        "템플릿", "양식", "사업계획서", "어떤 템플릿", "문서", "서식", "표준", "추천", "포맷", "형식", "체크",
        "예비창업", "초기창업", "도약", "성장", "사회적", "작성 방식"
    ],
    PLAN_INTENT_DRAFT_SECTIONS: [
        "초안", "작성", "섹션", "섹션별", "문단", "보고서", "자동 생성", "기록", "생성", "작성해", "문서화",
        "작성해줘", "짧게 정리", "사업계획", "요약", "draft", "내용 반영", "항목", "섹션은"
    ],
    PLAN_INTENT_POLICY_CHECK: [
        "요건", "요건이", "자격", "조건", "지원", "공고", "정부지원", "인증", "심사", "지침", "정책", "사회적기업", "지원사업"
    ],
    PLAN_INTENT_CORRECTION: [
        "수정", "고쳐", "다듬", "교정", "문장", "표현", "바꿔", "재작성", "오탈자", "틀린", "부족"
    ],
    PLAN_INTENT_SUMMARY: [
        "요약", "정리", "한번 정리", "지금까지", "요약해줘", "정리해줘", "요약해", "내용 다시", "이전"
    ],
}

PLAN_SEED_HINTS = [
    "사업계획서",
    "예비창업",
    "초기창업",
    "창업도약",
    "창업",
    "지원사업",
    "정부지원사업",
]

PLAN_DISAMBIGUATE_OPTIONS = {
    1: PLAN_INTENT_PROFILE_CAPTURE,
    2: PLAN_INTENT_TEMPLATE_SELECT,
    3: PLAN_INTENT_SUMMARY,
}

PLAN_DISAMBIGUATE_KEYWORDS = {
    PLAN_INTENT_PROFILE_CAPTURE: [
        "회사/아이템 정보부터 정리",
        "회사부터 정리",
        "회사 정보부터",
        "정보부터 정리",
    ],
    PLAN_INTENT_SUMMARY: [
        "지금까지 내용 요약",
        "지금까지 요약",
        "내용 요약",
        "요약/정리",
        "요약해줘",
    ],
}

PLAN_DISAMBIGUATE_FOLLOWUP_HINTS = [
    "아래 버튼 중 원하시는 흐름 하나를 골라주세요",
    "버튼으로 동작하지 않으면 채팅으로",
    "버튼이 안 눌리거나 원하는 방식이면 채팅으로도",
    "아래 선택지 중 하나를 골라주세요",
    "어떤 흐름으로 진행할까요",
    "원하시는 흐름 하나를 골라주세요",
]

PLAN_AFFIRMATIVE_TOKENS = [
    "네",
    "응",
    "좋아",
    "그래",
    "넹",
    "ㅇㅋ",
    "ok",
    "좋습니다",
]
PLAN_SUMMARY_CONFIRM_TOKENS = [
    "맞아요",
    "맞습니다",
    "동의",
    "동의해요",
    "네 맞아요",
    "네 맞습니다",
    "좋아요",
    "좋습니다",
    "그럼 진행",
    "진행해줘",
    "진행해",
    "이대로",
    "이제 진행",
    "계속",
]
PLAN_SUMMARY_REVISE_TOKENS = [
    "아니요",
    "아니오",
    "아님",
    "수정",
    "틀려",
    "다르게",
    "누락",
    "부족",
    "다시 말",
    "재확인",
]
PLAN_YES_TOKENS = ["예", "네", "응", "맞아", "예요", "좋아", "ㅇㅇ", "yes", "y"]
PLAN_NO_TOKENS = ["아니오", "아니요", "아니", "no", "n", "싫어", "아냐"]

PLAN_TOPIC_SWITCH_CONFIRM_MESSAGE = (
    "사업계획서를 작성중이였어요, 지금말씀하신 내용은 다른주제로 분류됩니다. 다른주제로 대화할까요?(예/아니오)"
)
PLAN_RESUME_CONFIRM_MESSAGE = (
    "이전 요건이 존재합니다. 이어서 진행할까요? (예/아니오)"
)
PLAN_SUMMARY_CONFIRM_RETRY_MESSAGE = (
    "요약 확인 단계예요. 내용이 맞으면 '맞아요/이대로 진행', 수정이 필요하면 '수정'이라고 답해 주세요."
)
TRANSITION_KEY = "__transition_state"
TRANSITION_CONTEXT_LAST_SLOT = "__transition_last_slot"
TRANSITION_AWAIT_TOPIC_SWITCH = "await_topic_switch_confirm"
TRANSITION_AWAIT_RESUME = "await_resume_confirm"
TRANSITION_AWAIT_SUMMARY_CONFIRM = "await_summary_confirm"
TRANSITION_AWAIT_CLASSIFICATION_CONFIRM = "await_classification_confirm"
CLASSIFICATION_CONFIRM_PROMPT = "이 분류로 초안 생성할까요? (예/수정)"

PLAN_ROUTING_LEGACY = "PLAN_FREE_CHAT"
PLAN_ROUTING_QUESTION_FLOW = "PLAN_QUESTION_FLOW"
PLAN_ROUTING_TEMPLATE_SELECT = "PLAN_TEMPLATE_SELECT"
PLAN_ROUTING_DRAFT_SECTIONS = "PLAN_DRAFT_SECTIONS"
PLAN_ROUTING_DISAMBIGUATE = "PLAN_DISAMBIGUATION"
PLAN_ROUTING_FREEFLOW = "PLAN_FREEFLOW"
PLAN_ROUTING_POLICY_BLOCK = "PLAN_POLICY_BLOCK"

CONFIDENCE_TIE_DELTA = 0.08
CONFIDENCE_FORCE_MIN = 0.55


PLAN_CASUAL_BYPASS_KEYWORDS = [
    "하이",
    "ㅎㅇ",
    "hello",
    "hi",
    "날씨",
    "어떻게",
    "어때",
    "오늘",
    "지금",
    "내일",
    "비",
    "맑",
    "덥",
    "춥",
    "어느",
    "어디",
    "고마워",
    "감사",
    "안녕",
    "반가워",
]

PLAN_REQUIRED_SLOT_ORDER = (
    "company_profile",
    "target_problem",
    "revenue_funding",
)
PLAN_REQUIRED_SLOT_QUESTIONS = {
    "company_profile": "먼저 회사명, 업종, 팀 구성(예: 창업자 1명, 개발 1명)은 어떻게 되나요?",
    "target_problem": "타깃 고객은 누구이고, 어떤 문제를 해결하려는지 한 줄로 알려주세요.",
    "revenue_funding": "현재 매출·비용·자금 상태 중 아는 항목만 간단히 알려주세요.",
}


def _is_plan_non_match_natural_signal(text: str) -> bool:
    """PLAN 라우팅을 유발하지 않아야 할 자유형 자연어(잡담/간단질문) 판별."""
    if not text:
        return False
    norm = text.replace(" ", "")
    return any(token in norm for token in PLAN_CASUAL_BYPASS_KEYWORDS)


def _derive_plan_active_mode(routing_state: str) -> str:
    """Plan routing state to runtime mode mapping (UI + DB 저장용)."""
    if routing_state in (
        PLAN_ROUTING_DISAMBIGUATE,
        PLAN_ROUTING_QUESTION_FLOW,
        PLAN_ROUTING_TEMPLATE_SELECT,
        PLAN_ROUTING_POLICY_BLOCK,
    ):
        return "REQUIREMENT"
    if routing_state in (
        PLAN_ROUTING_DRAFT_SECTIONS,
        PLAN_ROUTING_FREEFLOW,
    ):
        return "FUNCTION"
    return "NATURAL"


async def _sync_active_mode(
    project_id: str,
    routing_state: str,
    thread_id: str | None = None,
) -> str:
    mode = _derive_plan_active_mode(routing_state)
    try:
        await set_project_active_mode(project_id, mode, thread_id=thread_id)
    except Exception:
        # DB 동기화 실패 시에도 흐름 중단 대신 fallback 모드 반환.
        return mode
    return mode

PLAN_READY_MESSAGES = {
    "first_login": (
        "안녕하세요, AIBizPlan에 오신 걸 환영해요. 😊\n"
        "첫 상담이라도 전혀 부담 가지지 마세요.\n"
        "AI가 대신 판단하지 않고, 지금은 '질문 중심'으로 차분하게 도와드리는 방식이에요.\n\n"
        "처음 오신 분을 위해 가장 안전한 순서를 먼저 안내드릴게요.\n"
        "1) 회사/사업의 핵심 정보부터 정리 (회사명, 업종, 제품/서비스, 고객)\n"
        "2) 매출/운영 현황, 지원 필요사항을 단계적으로 질문\n"
        "3) 지금까지의 답변으로 적절한 사업계획서 템플릿은 자동으로 고정\n"
        "4) 초안 작성 → 점검/보완 → 최종 승인 후 PDF 생성\n\n"
        "지금은 긴 문장을 몰라도 괜찮아요. 한 문장씩 가볍게 입력해도 충분합니다.\n"
        "예: “회사명은 OO입니다”, “아직 잘 몰라서 천천히 설명해줘요”처럼 말씀해 주세요.\n"
        "기본적으로 저와의 대화는 저장되고 다음 단계로 자연스럽게 이어집니다."
    ),
    "new_room": (
        "이 방은 “새로운 상담방”으로 시작된 현재 프로젝트 전용 작업공간입니다.\n"
        "방 안에서 주고받은 내용은 해당 상담 내용으로만 이어져서 반영돼요.\n\n"
        "처음엔 복잡한 항목을 한 번에 채우지 않아도 괜찮습니다.\n"
        "알고 있는 내용부터 짧게 말해 주세요.\n"
        "예: “회사명은 …”, “우리 제품은 …”, “고객은 …”, “매출은 …” 처럼 말해주세요.\n\n"
        "제가 필요할 때 필요한 질문만 드릴게요.\n"
        "중간에 “지금까지 내용 요약해줘”라고 하면 한 번에 정리해드릴 수 있어요."
    ),
    "consult_mode": (
        "현재는 상담 정보 수집 단계예요.\n"
        "한 번에 한 가지 항목만 물어볼게요.\n"
        "아는 만큼만 짧게 답해 주시면 충분해요."
    ),
    "requirement_mode": (
        "지금은 [요건수집 모드]예요.\n"
        "지원사업 신청 전 실수 방지를 위해 꼭 확인해야 하는 조건을 정리하는 단계입니다.\n"
        "지원 대상 요건, 증빙 자료, 심사 기준을 항목별로 같이 확인해요.\n"
        "한 번에 많이 말하면 오히려 빠지기 쉬우니, 항목별로 천천히 진행해도 괜찮습니다.\n"
        "예: “지원사업 대상인지 체크해줘”, “이 조건 충족 가능한지 같이 봐줘”라고 말해 주세요."
    ),
    "assistant_mode": (
        "현재는 세부 보완/수정 요청을 받는 단계예요.\n"
        "핵심 질문 수집이 끝난 뒤 요약 확인을 거쳐 바로 반영할 수 있습니다.\n"
        "원하면 “지금까지 내용 요약해줘”, “수정”, “모양 다듬어줘”처럼 간단히 말해 주세요."
    ),
    "summary_prepare": (
        "지금까지 입력하신 내용을 한 번에 정리해드릴게요.\n"
        "정리가 끝나면 맞는지 확인만 해주시면 초안 작성 단계로 바로 이동합니다."
    ),
    "summary_request": (
        "사업계획서 초안은 요약 확인 후에만 진행돼요.\n"
        "아래 요약 내용이 맞으면 “맞아요/이대로 진행”으로 답해 주세요.\n"
        "틀리거나 누락된 부분이 있으면 “수정”이라고 말해 주세요."
    ),
    "summary_confirmed": (
        "요약 내용이 확인되어 초안 작성 단계로 이동합니다.\n"
        "수집된 정보는 템플릿 섹션 작성으로 이어집니다."
    ),
    "collect_more_before_assist": (
        "아직 초안 작성 단계로 넘기기 전입니다.\n"
        "현재는 기본 정보 수집을 우선 진행해야 하므로, "
        "회사명·고객·매출·자금 정보부터 차근차근 채워주세요."
    ),
    "summary_need_capture": (
        "지금은 초안 단계로 넘어가기 전입니다.\n"
        "먼저 대화 내용을 기반으로 요약을 먼저 보고 진행할 수 있어요. “지금까지 내용 요약해줘”라고 말해 주세요."
    ),
    "disambiguate": (
        "의도 충돌이 있어 판단을 안전하게 보정해요.\n"
        "일단 회사/사업 정보 수집을 계속 진행해서, 정보가 모이면 다음 단계로 바로 이동합니다."
    ),
    "policy_block": (
        "상담 정책 상태 동기화에 실패해 v1.0 플로우를 시작할 수 없습니다.\n"
        "잠시 후 다시 시도해 주세요. 문제가 계속되면 운영자에게 문의해 주세요."
    ),
}


PROFILE_REQUIRED_QUESTIONS = [
    "먼저 회사명, 업종, 팀 구성(예: 창업자 1명, 개발 1명)은 어떻게 되나요?",
    "타깃 고객은 누구이고, 어떤 문제를 해결하려는지 한 줄로 알려주세요.",
    "현재 매출·비용·자금 상태 중 아는 항목만 간단히 알려주세요.",
]


PROFILE_OPTIONAL_QUESTIONS = [
    "문장·수치·근거자료가 있다면 함께 주시면 정확도가 올라갑니다.",
    "문서 기반 정보가 있으면 핵심 수치만 먼저 주시면 됩니다.",
]


PROFILE_SPECIAL_QUESTIONS = [
    "원하시면 추가로 정리하고 싶은 내용이 있으면 말씀해 주세요.",
]


def _build_profile_capture_prompt(
    slot: str,
    required_count: int,
    optional_count: int,
    special_count: int,
    consultation_mode: str,
) -> str:
    """상태 기반 1개 질문을 강제 생성한다."""
    slot = slot or "required"
    if slot == "required":
        # required 슬롯은 응답이 들어올 때마다 카운터가 증가한다.
        # 전달되는 카운터는 방금 증가한 값이므로 다음 질문은 0-based 보정으로 출력한다.
        next_required_index = max(0, required_count - 1)
        if next_required_index < len(PROFILE_REQUIRED_QUESTIONS):
            question = PROFILE_REQUIRED_QUESTIONS[next_required_index]
        elif optional_count < len(PROFILE_OPTIONAL_QUESTIONS):
            question = PROFILE_OPTIONAL_QUESTIONS[optional_count]
        elif special_count < len(PROFILE_SPECIAL_QUESTIONS):
            question = PROFILE_SPECIAL_QUESTIONS[special_count]
        else:
            question = (
                "추가로 확인이 필요한 항목이 있으면 알려주세요. "
                "없으면 '없음'이라고 답해 주세요."
            )
    elif slot == "optional":
        # optional은 기본 보강 문항 2개를 순환 사용한다.
        question = PROFILE_OPTIONAL_QUESTIONS[max(0, optional_count - 1) % len(PROFILE_OPTIONAL_QUESTIONS)]
    elif slot == "special":
        # special은 기본 보강 문항 1개를 고정 사용한다.
        question = PROFILE_SPECIAL_QUESTIONS[0]
    else:
        question = PROFILE_REQUIRED_QUESTIONS[max(0, required_count - 1)]

    return (
        f"{question}\n"
        "정답은 짧은 문장으로도 충분해요. 모르면 ‘잘 모르겠어요’라고 답해 주세요."
    )


async def detect_topic_shift_with_context(ctx: StreamContext) -> bool:
    """
    이전 대화 맥락을 보고 주제 변경 여부 판단 (LLM 활용)
    
    Returns:
        True: 완전히 다른 주제로 변경됨
        False: 연속된 대화 또는 판단 불가
    """
    try:
        from app.core.database import get_messages_from_rdb
        from app.core.config import settings
        
        # 1. 이전 3~5개 대화 가져오기
        recent_messages = await get_messages_from_rdb(
            ctx.project_id, 
            ctx.thread_id, 
            limit=5
        )
        
        if len(recent_messages) < 2:
            ctx.add_log("topic_shift", "대화 시작 단계 - 주제 변경 아님")
            return False  # 대화 시작이면 주제 변경 아님
        
        # 2. LLM에게 맥락 판단 요청
        llm = ChatOpenAI(
            model="google/gemini-2.0-flash-001",  # 빠르고 저렴한 모델
            api_key=settings.OPENROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1",
            temperature=0.1,
        )
        
        # 최근 3개 메시지만 사용 (너무 길면 노이즈)
        history = "\n".join([
            f"{m.sender_role}: {m.content[:100]}..."  # MessageModel 객체 접근
            for m in recent_messages[-3:]
        ])
        
        prompt = f"""이전 대화와 현재 입력의 연관성을 판단하세요.

이전 대화:
{history}

현재 입력: {ctx.user_input_raw}

질문: 현재 입력이 이전 대화와 **완전히 다른 주제**인가요?

판단 기준:
- YES: 완전히 다른 주제 (예: GPT 성능 얘기하다가 → "오늘 날씨 어때?")
- NO: 연속된 대화 (예: GPT 성능 얘기 중 → "다른 모델은 어때?", "그럼 어떻게 해결해?")

**답변은 "YES" 또는 "NO" 한 단어만 출력하세요.**

답변:"""
        
        response = await llm.ainvoke(prompt)
        result = "YES" in response.content.upper()
        
        ctx.add_log("topic_shift", f"LLM 판단: {'주제 변경' if result else '연속 대화'}")
        return result
        
    except Exception as e:
        ctx.add_log("topic_shift", f"LLM 판단 실패: {e} - False로 처리")
        return False  # 실패 시 안전하게 연속 대화로 처리


def parse_user_input(ctx: StreamContext) -> StreamContext:
    """
    Step 1: 입력 정규화 (<= 150줄)
    
    역할:
    - 공백/줄바꿈 정리
    - confirm_token 감지 (명시 토큰만)
    """
    # 입력 정규화
    raw = ctx.user_input_raw
    norm = raw.strip()
    
    # 연속 공백 축약
    norm = re.sub(r'\s+', ' ', norm)
    
    ctx.user_input_norm = norm
    ctx.add_log("parse_user_input", f"Normalized: '{norm}'")
    
    # [Guardrail] confirm_token 감지 (명시 토큰만)
    for token in CONFIRM_TOKENS:
        if token in norm:
            ctx.confirm_token = token
            ctx.confirm_token_detected = True
            ctx.add_log("parse_user_input", f"Confirm token detected: '{token}'")
            break
    
    # [Guardrail] "응", "예", "좋아"는 confirm_token으로 인정하지 않음
    # (아무 처리도 하지 않음, False 고정)
    
    return ctx


def _contains_any(text: str, keywords: List[str]) -> int:
    return sum(1 for keyword in keywords if keyword in text)


def _is_plan_seed_signal(text: str) -> bool:
    norm = text.replace(" ", "")
    return any(keyword in norm for keyword in PLAN_SEED_HINTS)


def _infer_consultation_mode_from_seed(text: str) -> str | None:
    """seed 입력에서 단계 키워드가 있으면 상담 모드 힌트를 추론."""
    norm = text.replace(" ", "")
    if any(k in norm for k in ["성장", "도약", "도약기업", "성장기업", "scale"]):
        return CONSULTATION_MODE_GROWTH
    if any(k in norm for k in ["초기", "초기창업"]):
        return CONSULTATION_MODE_EARLY
    if any(k in norm for k in ["창업준비", "예비", "예비창업", "준비중", "startup", "pre"]):
        return CONSULTATION_MODE_PRELIMINARY
    return None


def _normalize_consultation_mode(mode: str | None) -> str | None:
    if not mode:
        return None
    normalized = str(mode).strip()
    if normalized in {CONSULTATION_MODE_PRELIMINARY, CONSULTATION_MODE_EARLY, CONSULTATION_MODE_GROWTH}:
        return normalized
    return None


def _is_business_data_sufficient_for_draft(state) -> bool:
    """
    초안 작성 단계 진입 조건은 질문상한 충족 + 최소 데이터 확보가 선행되어야 한다.
    """
    return (
        _required_fields_ready_for_draft(state)
        and state.question_required_count > 0
    )


def _extract_disambiguate_intent(text: str) -> str | None:
    """
    사용자 입력이 아래 3개 흐름 중 선택인지 해석한다.
    번호 입력/텍스트 키워드 모두 지원.
    """
    if not text:
        return None
    norm = text.replace(" ", "")

    option_1 = {
        "1", "1번", "1번이요", "1번입니다", "1번으로", "첫", "첫번째", "옵션1", "선택1", "번호1", "1)", "1번으로",
    }
    option_2 = {
        "2", "2번", "2번이요", "2번입니다", "2번으로", "두", "두번째", "옵션2", "선택2", "번호2", "2)",
    }
    option_3 = {
        "3", "3번", "3번이요", "3번입니다", "3번으로", "세", "세번째", "옵션3", "선택3", "번호3", "3)",
    }

    if norm in option_1:
        return PLAN_INTENT_PROFILE_CAPTURE
    if norm in option_2:
        return PLAN_INTENT_TEMPLATE_SELECT
    if norm in option_3:
        return PLAN_INTENT_SUMMARY

    for intent, kws in PLAN_DISAMBIGUATE_KEYWORDS.items():
        if any(kw in text for kw in kws):
            return intent

    return None


def _recent_disambiguation_prompt_in_history(history: List[ChatMessage]) -> bool:
    """
    최근 어시스턴트 답변에 디스앰비규레이션 문구가 있었는지 확인한다.
    버튼 선택 실패/미클릭 보완에서 only-affirmative로 진행할 수 있게 사용.
    """
    for msg in reversed(history[-6:]):
        if msg.role != "assistant":
            continue
        content = (msg.content or "").replace("\n", " ")
        if any(hint in content for hint in PLAN_DISAMBIGUATE_FOLLOWUP_HINTS):
            return True
    return False


def _normalize_token_set(text: str) -> str:
    return (text or "").replace(" ", "").replace("\n", "").strip()


def _contains_any_token(text: str, tokens: list[str]) -> bool:
    normalized = _normalize_token_set(text)
    return any(_normalize_token_set(token) in normalized for token in tokens)


def _contains_summary_confirmation_prompt(history: List[ChatMessage]) -> bool:
    """
    최근 어시스턴트 메시지에 '요약 확인' 요청이 있었는지 판별.
    """
    if not history:
        return False
    markers = [
        "지금까지 정리한 내용",
        "요약해드릴게요",
        "요약 내용을",
        "요약 내용",
        "요약해 드릴게요",
        "요약을 바탕",
        "요약이 맞",
    ]
    for msg in reversed(history[-8:]):
        if msg.role != "assistant":
            continue
        content = msg.content or ""
        if any(marker in content for marker in markers):
            return True
    return False


async def _is_disambiguation_followup_affirmation(ctx: StreamContext) -> bool:
    """
    직전 안내 흐름 후 이어지는 짧은 '그래/네' 응답을 안전하게 상담 진행 의도로 해석.
    """
    if not _is_affirmative_brief_signal(ctx.user_input_norm):
        return False

    if _recent_disambiguation_prompt_in_history(ctx.history):
        return True

    # 최근 DB 이력을 fallback으로 점검: 클라이언트가 최신 assistant 메시지를 안 보냈을 때도 안정 동작
    try:
        from app.core.database import get_messages_from_rdb

        db_messages = await get_messages_from_rdb(
            ctx.project_id,
            ctx.thread_id,
            limit=8
        )
        if db_messages:
            recent_db_history = [
                ChatMessage(
                    role="assistant" if m.sender_role == "assistant" else m.sender_role,
                    content=m.content or "",
                )
                for m in reversed(db_messages[-8:])
            ]
            if _recent_disambiguation_prompt_in_history(recent_db_history):
                return True
    except Exception:
        # 정책 판단 실패는 fail-open(확인 대기) 대신 fail-safe 방지로 false 처리
        return False

    return False


def _is_affirmative_brief_signal(text: str) -> bool:
    if not text:
        return False
    norm = text.replace(" ", "")
    return norm in {tok.replace(" ", "") for tok in PLAN_AFFIRMATIVE_TOKENS}


def _is_summary_confirm_reply(text: str) -> bool:
    if not text:
        return False
    return _contains_any_token(text, PLAN_SUMMARY_CONFIRM_TOKENS)


def _is_summary_revise_reply(text: str) -> bool:
    if not text:
        return False
    return _contains_any_token(text, PLAN_SUMMARY_REVISE_TOKENS)


def _extract_plan_slot_updates(text: str, last_asked_slot: str | None = None) -> dict[str, str]:
    raw = (text or "").strip()
    if not raw:
        return {}
    updates: dict[str, str] = {}
    lowered = raw.lower()
    if "?" in raw or lowered.endswith("요") and any(k in lowered for k in ["무엇", "어떻게", "뭐", "궁금"]):
        return {}
    norm = raw.replace(" ", "")
    # 진행 지시형 문구는 슬롯 답변으로 저장하지 않는다.
    flow_control_tokens = ("이어서", "계속", "진행", "다음질문", "다음 질문", "resume", "retry")
    if any(token in norm.lower() for token in flow_control_tokens):
        return {}

    # 우선순위: 서버가 마지막으로 질문한 슬롯 기준으로만 저장(오분류 방지)
    if last_asked_slot in PLAN_REQUIRED_SLOT_ORDER:
        updates[last_asked_slot] = raw[:500]
        return updates

    has_company_profile_hint = any(token in norm for token in ["회사명", "기업명", "업종", "산업", "팀", "팀원", "창업자", "대표"])
    has_target_problem_hint = any(token in norm for token in ["고객", "타깃", "타겟", "문제", "해결", "제품", "서비스", "아이템"])
    has_revenue_funding_hint = any(token in norm for token in ["매출", "비용", "자금", "투자", "자본", "없어", "없음", "0원", "무매출"])

    if has_company_profile_hint:
        updates["company_profile"] = raw[:500]
    if has_target_problem_hint:
        updates["target_problem"] = raw[:500]
    if has_revenue_funding_hint:
        # "자금조달계획" 같은 작성 요청은 현황 슬롯이 아니라 주제 메모로 저장
        if any(token in norm for token in ["조달계획", "자금조달계획", "투자유치전략", "계획수립"]):
            updates["financing_topic"] = raw[:500]
        else:
            updates["revenue_funding"] = raw[:500]

    return updates


def _is_unknown_or_skip_reply(text: str) -> bool:
    norm = (text or "").replace(" ", "").strip()
    if not norm:
        return False
    tokens = ("모르겠", "잘모르", "없음", "없어요", "없어", "스킵", "skip")
    return any(token in norm for token in tokens)


def _find_missing_required_slots(slots: dict[str, str]) -> list[str]:
    missing: list[str] = []
    for key in PLAN_REQUIRED_SLOT_ORDER:
        value = (slots.get(key) or "").strip()
        if not value:
            missing.append(key)
    return missing


def _is_yes_reply(text: str) -> bool:
    norm = (text or "").replace(" ", "").strip().lower()
    return norm in {tok.replace(" ", "").lower() for tok in PLAN_YES_TOKENS}


def _is_no_reply(text: str) -> bool:
    norm = (text or "").replace(" ", "").strip().lower()
    return norm in {tok.replace(" ", "").lower() for tok in PLAN_NO_TOKENS}


def _is_classification_confirm_reply(text: str) -> bool:
    return _is_yes_reply(text)


def _is_classification_revise_reply(text: str) -> bool:
    norm = (text or "").replace(" ", "").strip().lower()
    if not norm:
        return False
    return "수정" in norm or _is_no_reply(text)


def _has_plan_progress(state, slots: dict[str, str]) -> bool:
    if slots and any((slots.get(k) or "").strip() for k in PLAN_REQUIRED_SLOT_ORDER):
        return True
    if not state:
        return False
    return int(getattr(state, "question_total_count", 0) or 0) > 0


def _build_plan_summary_confirmation(slots: dict[str, str], consultation_mode: str | None) -> str:
    company = slots.get("company_profile", "미입력")
    target = slots.get("target_problem", "미입력")
    revenue = slots.get("revenue_funding", "미입력")
    stage = consultation_mode or "미확정"
    return (
        "지금까지 수집된 내용을 요약했어요.\n"
        f"1) 기업/팀: {company}\n"
        f"2) 고객/문제: {target}\n"
        f"3) 매출/자금: {revenue}\n"
        f"4) 현재 단계 판단: {stage}\n\n"
        "이 요약이 맞으면 '맞아요' 또는 '이대로 진행'이라고 답해 주세요.\n"
        "수정이 필요하면 '수정'이라고 답해 주세요."
    )


def _consultation_stage_label(mode: str | None) -> str:
    if mode == CONSULTATION_MODE_PRELIMINARY:
        return "예비"
    if mode == CONSULTATION_MODE_EARLY:
        return "초기"
    if mode == CONSULTATION_MODE_GROWTH:
        return "성장"
    return "미확정"


def _build_classification_confirmation_card(slots: dict[str, str], consultation_mode: str | None) -> str:
    stage_label = _consultation_stage_label(consultation_mode)
    company = (slots.get("company_profile") or "미입력").strip() or "미입력"
    target = (slots.get("target_problem") or "미입력").strip() or "미입력"
    revenue = (slots.get("revenue_funding") or "미입력").strip() or "미입력"
    return (
        "[분류 결과]\n"
        f"단계: {stage_label}\n"
        f"근거 1) 기업/팀 정보: {company}\n"
        f"근거 2) 고객/문제: {target}\n"
        f"근거 3) 매출/자금: {revenue}\n\n"
        f"{CLASSIFICATION_CONFIRM_PROMPT}"
    )


def _extract_json_object(text: str) -> dict | None:
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


async def _classify_summary_reply_with_llm(user_text: str) -> tuple[str, float]:
    """
    요약확인 답변을 LLM 보조로 판정.
    반환: (confirm|revise|unknown, confidence)
    """
    if not user_text:
        return ("unknown", 0.0)

    try:
        from app.core.config import settings

        llm = ChatOpenAI(
            model="google/gemini-2.0-flash-001",
            api_key=settings.OPENROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1",
            temperature=0.0,
        )
        prompt = (
            "사용자 답변이 사업계획서 요약 확인에 대해 '확정/수정/불명확' 중 무엇인지 분류하세요.\n"
            "반드시 JSON만 출력: {\"decision\":\"confirm|revise|unknown\",\"confidence\":0.0}\n"
            f"사용자 답변: {user_text}"
        )
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        parsed = _extract_json_object(getattr(response, "content", "") or "")
        if not parsed:
            return ("unknown", 0.0)
        decision = str(parsed.get("decision", "unknown")).strip().lower()
        confidence = float(parsed.get("confidence", 0.0) or 0.0)
        if decision not in {"confirm", "revise", "unknown"}:
            decision = "unknown"
        return (decision, confidence)
    except Exception:
        return ("unknown", 0.0)


async def _is_summary_confirmed(
    project_id: str,
    artifact_type: str = "business_plan",
    thread_id: str | None = None,
) -> bool:
    try:
        approval_state = await get_or_create_approval_state(
            project_id,
            artifact_type,
            thread_id=thread_id,
        )
        if not bool(getattr(approval_state, "summary_confirmed", False)):
            return False
        conv_state = await get_project_policy_state(project_id, thread_id=thread_id)
        if not conv_state:
            return False
        summary_revision = int(getattr(approval_state, "summary_revision", 0) or 0)
        plan_data_version = int(getattr(conv_state, "plan_data_version", 0) or 0)
        return summary_revision >= plan_data_version and plan_data_version > 0
    except Exception:
        return False


async def _is_summary_confirmation_intent(ctx: StreamContext) -> str | None:
    """
    최근에 요약 승인 요청이 있었던 경우 사용자의 답변을 동의/반려로 해석한다.
    """
    if not _contains_summary_confirmation_prompt(ctx.history):
        return None

    if _is_summary_confirm_reply(ctx.user_input_norm):
        return PLAN_INTENT_SUMMARY_CONFIRM
    if _is_summary_revise_reply(ctx.user_input_norm):
        return PLAN_INTENT_SUMMARY_REVISE
    return None


def _score_plan_intents(msg: str) -> Tuple[str, float, dict, float | None]:
    """
    Rule 기반 PLAN intent 점수 계산 (LLM 보조 분류의 하드게이트 목적).
    
    반환:
    - top intent, top score, 상세 점수 map, 2차 intent와의 점수 차이
    """
    text = msg.lower()
    scores: dict[str, float] = {}
    for intent, keywords in PLAN_KEYWORDS.items():
        hit = _contains_any(text, [kw.lower() for kw in keywords])
        if not keywords:
            scores[intent] = 0.0
            continue
        score = hit / len(keywords)
        # 길이/강세 보정: 6개 이상 매치되면 상한치 보완
        score = min(0.99, score + (0.05 if hit >= 2 else 0.0))
        scores[intent] = round(score, 4)

    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    top_intent, top_score = ordered[0]
    second_score = ordered[1][1] if len(ordered) > 1 else 0.0
    tie_delta = round(top_score - second_score, 4)

    # 히트가 없으면 UNKNOWN
    if top_score < 0.05:
        return PLAN_INTENT_UNKNOWN, 0.0, scores, None
    return top_intent, top_score, scores, tie_delta


def _plan_intent_to_question_slot(plan_intent: str) -> str | None:
    if plan_intent in (PLAN_INTENT_PROFILE_CAPTURE, PLAN_INTENT_TEMPLATE_SELECT, PLAN_INTENT_DRAFT_SECTIONS):
        return "required"
    if plan_intent in (PLAN_INTENT_POLICY_CHECK, PLAN_INTENT_SUMMARY):
        return "optional"
    if plan_intent in (PLAN_INTENT_CORRECTION,):
        return "special"
    return "optional"


def _is_v1_state_ready(state) -> bool:
    if not state:
        return False
    return state.policy_version == POLICY_VERSION_V1 and state.profile_stage in (
        CONSULTATION_MODE_PRELIMINARY,
        CONSULTATION_MODE_EARLY,
        CONSULTATION_MODE_GROWTH,
    )


def _required_fields_ready(state, minimum_ratio: float = 0.20) -> bool:
    if not state:
        return False
    required_count = getattr(state, "question_required_count", 0) or 0
    required_limit = getattr(state, "question_required_limit", 0) or 0
    if required_limit <= 0:
        return required_count > 0
    return required_count >= max(1, int(required_limit * minimum_ratio))


def _template_recommendation_ready(state, minimum_ratio: float = 0.30) -> bool:
    """템플릿 추천 단계 진입을 위한 최소 수집량(기본값: required 상위 30%)."""
    if not state:
        return False
    required_count = getattr(state, "question_required_count", 0) or 0
    required_limit = getattr(state, "question_required_limit", 0) or 0
    if required_limit <= 0:
        return required_count > 0
    return required_count >= max(2, int(required_limit * minimum_ratio))


def _required_fields_ready_for_draft(state) -> bool:
    if not state:
        return False
    required_count = getattr(state, "question_required_count", 0) or 0
    required_limit = getattr(state, "question_required_limit", 0) or 0
    if required_limit <= 0:
        return False
    return required_count >= required_limit


async def resolve_plan_execution_flow(ctx: StreamContext) -> StreamContext:
    """
    v1.0 전용 라우팅/가드.
    - 상태 우선: 미확정 프로필/카운터 미충족 시 PLAN_QUESTION_FLOW로 강제
    - 동률/저신뢰 시 1회 확인 버튼 옵션 반환(텍스트 형태)
    """
    state = await get_project_policy_state(ctx.project_id, thread_id=ctx.thread_id)

    # v1.0 only: 정책 상태는 항상 v1_0으로 강제 보정한다.
    if (not state or state.policy_version != POLICY_VERSION_V1) and ctx.project_id != "system-master":
        previous_policy = state.policy_version if state else POLICY_VERSION_LEGACY
        current_mode = _normalize_consultation_mode(state.consultation_mode if state else None)
        if not current_mode:
            current_mode = CONSULTATION_MODE_PRELIMINARY

        try:
            state = await set_project_policy_version(
                ctx.project_id,
                policy_version=POLICY_VERSION_V1,
                consultation_mode=current_mode or CONSULTATION_MODE_PRELIMINARY,
                thread_id=ctx.thread_id,
            )
            state = await get_project_policy_state(ctx.project_id, thread_id=ctx.thread_id)
        except Exception as exc:
            recover_error = str(exc)
            ctx.routing_state = PLAN_ROUTING_POLICY_BLOCK
            ctx.routing_message = PLAN_READY_MESSAGES["policy_block"]
            ctx.routing_error = {
                "error_code": "POLICY_VALIDATION_FAILED",
                "message": "프로젝트 정책 동기화(v1.0) 실패",
                "violations": [f"policy_sync_failed:{recover_error}"],
            }
            ctx.plan_intent = PLAN_INTENT_PROFILE_CAPTURE
            ctx.plan_confidence = 0.0
            ctx.policy_version = POLICY_VERSION_V1
            ctx.add_log(
                "plan_flow",
                f"v1.0 policy sync failed: project={ctx.project_id}, previous={previous_policy}, err={recover_error}",
            )
            return ctx

        if not state or state.policy_version != POLICY_VERSION_V1:
            ctx.routing_state = PLAN_ROUTING_POLICY_BLOCK
            ctx.routing_message = PLAN_READY_MESSAGES["policy_block"]
            ctx.routing_error = {
                "error_code": "POLICY_VALIDATION_FAILED",
                "message": "프로젝트 상담 정책 정합화 실패(v1.0 고정)",
                "violations": [f"policy_version={previous_policy}", "target=v1_0", "state_not_upserted"],
            }
            ctx.plan_intent = PLAN_INTENT_PROFILE_CAPTURE
            ctx.plan_confidence = 0.0
            ctx.policy_version = POLICY_VERSION_V1
            ctx.add_log(
                "plan_flow",
                f"v1.0 policy not ready after sync: previous={previous_policy}, project={ctx.project_id}",
            )
            return ctx

        if previous_policy != POLICY_VERSION_V1:
            ctx.add_log(
                "plan_flow",
                f"v1.0 policy auto-healed: project={ctx.project_id}, previous={previous_policy}",
            )

    # 새 상담방(새 thread) 진입 시 기존 프로젝트 누적 카운터 공유로 인한 오탐지 방지
    # -> thread별 신규 시작에서 질문 카운터는 반드시 초기화해서 0부터 시작한다.
    if state and state.policy_version == POLICY_VERSION_V1:
        should_reset_for_thread = False

        if getattr(ctx, "is_new_room_first_entry", False):
            should_reset_for_thread = True
        elif ctx.thread_id:
            # 히스토리 전달 상태와 무관하게 thread 단위로 새방 여부를 재검증
            try:
                thread_messages = await get_messages_from_rdb(
                    project_id=ctx.project_id,
                    thread_id=ctx.thread_id,
                    limit=1,
                )
                should_reset_for_thread = not bool(thread_messages)
            except Exception:
                should_reset_for_thread = False

        if should_reset_for_thread and (
            state.question_total_count > 0
            or state.question_required_count > 0
            or state.question_optional_count > 0
            or state.question_special_count > 0
        ):
            mode_for_reset = _normalize_consultation_mode(state.consultation_mode) or CONSULTATION_MODE_PRELIMINARY
            state = await set_project_consultation_mode(
                ctx.project_id,
                mode_for_reset,
                thread_id=ctx.thread_id,
            )
            ctx.add_log(
                "plan_flow",
                f"새 상담방 최초 진입으로 카운터 초기화: project={ctx.project_id}, mode={mode_for_reset}"
            )

    ctx.policy_version = state.policy_version
    ctx.consultation_mode = state.consultation_mode
    ctx.active_template_id = state.active_template_id
    ctx.plan_data_version = int(getattr(state, "plan_data_version", 0) or 0)
    ctx.summary_revision = int(getattr(state, "summary_revision", 0) or 0)
    ctx.question_counters_snapshot = {
        "question_required_count": state.question_required_count,
        "question_optional_count": state.question_optional_count,
        "question_special_count": state.question_special_count,
        "question_required_limit": state.question_required_limit,
        "question_optional_limit": state.question_optional_limit,
        "question_special_limit": state.question_special_limit,
    }
    slots = await get_plan_profile_slots(ctx.project_id, thread_id=ctx.thread_id)
    transition_state = slots.get(TRANSITION_KEY)
    plan_suspended = bool(getattr(state, "plan_suspended", False))

    # 잡담/인사 입력은 언제든 자유대화로 즉시 우회한다.
    # 기존 진행 슬롯은 유지하되 plan_suspended=True로 고정하여 다음 턴 오분류 재진입을 막는다.
    if _is_plan_non_match_natural_signal(ctx.user_input_norm) and not _is_plan_seed_signal(ctx.user_input_norm):
        cleared = dict(slots)
        cleared.pop(TRANSITION_KEY, None)
        cleared.pop(TRANSITION_CONTEXT_LAST_SLOT, None)
        await replace_plan_profile_slots(
            ctx.project_id,
            cleared,
            thread_id=ctx.thread_id,
            touch_plan_data_version=False,
        )
        if _has_plan_progress(state, slots):
            await set_plan_suspended(ctx.project_id, True, thread_id=ctx.thread_id)
        ctx.routing_state = PLAN_ROUTING_LEGACY
        ctx.routing_message = None
        ctx.plan_intent = PLAN_INTENT_UNKNOWN
        ctx.plan_confidence = 0.0
        ctx.add_log("plan_flow", "Casual/non-seed input detected -> suspend plan and keep free chat")
        return ctx

    async def _route_classification_confirm_gate() -> None:
        slots_with_transition = dict(slots)
        slots_with_transition[TRANSITION_KEY] = TRANSITION_AWAIT_CLASSIFICATION_CONFIRM
        await replace_plan_profile_slots(
            ctx.project_id,
            slots_with_transition,
            thread_id=ctx.thread_id,
            touch_plan_data_version=False,
        )
        ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
        ctx.routing_message = _build_classification_confirmation_card(
            slots,
            state.consultation_mode if state else None,
        )

    # 1) 전환 확인 대기 상태 우선 처리
    if transition_state == TRANSITION_AWAIT_TOPIC_SWITCH:
        if _is_yes_reply(ctx.user_input_norm):
            cleared = dict(slots)
            cleared.pop(TRANSITION_KEY, None)
            cleared.pop(TRANSITION_CONTEXT_LAST_SLOT, None)
            await replace_plan_profile_slots(
                ctx.project_id,
                cleared,
                thread_id=ctx.thread_id,
                touch_plan_data_version=False,
            )
            await set_plan_suspended(ctx.project_id, True, thread_id=ctx.thread_id)
            ctx.routing_state = PLAN_ROUTING_LEGACY
            ctx.routing_message = None
            ctx.plan_intent = PLAN_INTENT_UNKNOWN
            ctx.add_log("plan_flow", "topic switch confirm=yes -> suspend plan and move free chat")
            return ctx
        if _is_no_reply(ctx.user_input_norm):
            previous_slot = slots.get(TRANSITION_CONTEXT_LAST_SLOT, "")
            cleared = dict(slots)
            cleared.pop(TRANSITION_KEY, None)
            cleared.pop(TRANSITION_CONTEXT_LAST_SLOT, None)
            await replace_plan_profile_slots(
                ctx.project_id,
                cleared,
                thread_id=ctx.thread_id,
                touch_plan_data_version=False,
            )
            await set_plan_suspended(ctx.project_id, False, thread_id=ctx.thread_id)
            if previous_slot in PLAN_REQUIRED_SLOT_ORDER:
                await set_last_asked_slot(ctx.project_id, previous_slot, thread_id=ctx.thread_id)
            ctx.plan_intent = PLAN_INTENT_PROFILE_CAPTURE
            ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
            ctx.routing_message = None
            ctx.add_log("plan_flow", "topic switch confirm=no -> continue plan flow")
            return ctx
        ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
        ctx.routing_message = PLAN_TOPIC_SWITCH_CONFIRM_MESSAGE
        return ctx

    if transition_state == TRANSITION_AWAIT_RESUME:
        if _is_yes_reply(ctx.user_input_norm):
            cleared = dict(slots)
            cleared.pop(TRANSITION_KEY, None)
            cleared.pop(TRANSITION_CONTEXT_LAST_SLOT, None)
            await replace_plan_profile_slots(
                ctx.project_id,
                cleared,
                thread_id=ctx.thread_id,
                touch_plan_data_version=False,
            )
            await set_plan_suspended(ctx.project_id, False, thread_id=ctx.thread_id)
            ctx.plan_intent = PLAN_INTENT_PROFILE_CAPTURE
            ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
            ctx.routing_message = None
            ctx.add_log("plan_flow", "resume confirm=yes -> continue previous plan")
            return ctx
        if _is_no_reply(ctx.user_input_norm):
            await replace_plan_profile_slots(
                ctx.project_id,
                {},
                thread_id=ctx.thread_id,
                touch_plan_data_version=True,
            )
            # 새 플랜 시작: 카운터/질문 위치 초기화
            await set_project_consultation_mode(
                ctx.project_id,
                _normalize_consultation_mode(state.consultation_mode) or CONSULTATION_MODE_PRELIMINARY,
                thread_id=ctx.thread_id,
            )
            await set_last_asked_slot(ctx.project_id, None, thread_id=ctx.thread_id)
            await set_plan_suspended(ctx.project_id, False, thread_id=ctx.thread_id)
            ctx.plan_intent = PLAN_INTENT_PROFILE_CAPTURE
            ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
            ctx.routing_message = None
            ctx.add_log("plan_flow", "resume confirm=no -> reset and start new plan")
            return ctx
        ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
        ctx.routing_message = PLAN_RESUME_CONFIRM_MESSAGE
        return ctx

    if transition_state in {TRANSITION_AWAIT_SUMMARY_CONFIRM, TRANSITION_AWAIT_CLASSIFICATION_CONFIRM}:
        is_classification_gate = transition_state == TRANSITION_AWAIT_CLASSIFICATION_CONFIRM

        if _is_classification_confirm_reply(ctx.user_input_norm) or (
            not is_classification_gate and _is_summary_confirm_reply(ctx.user_input_norm)
        ):
            cleared = dict(slots)
            cleared.pop(TRANSITION_KEY, None)
            await replace_plan_profile_slots(
                ctx.project_id,
                cleared,
                thread_id=ctx.thread_id,
                touch_plan_data_version=False,
            )
            await update_approval_step(
                project_id=ctx.project_id,
                artifact_type="business_plan",
                step="summary_confirmed",
                approved=True,
                thread_id=ctx.thread_id,
            )
            ctx.routing_state = PLAN_ROUTING_DRAFT_SECTIONS
            ctx.routing_message = PLAN_READY_MESSAGES["summary_confirmed"]
            ctx.plan_intent = PLAN_INTENT_SUMMARY_CONFIRM
            return ctx

        if _is_classification_revise_reply(ctx.user_input_norm) or (
            not is_classification_gate and _is_summary_revise_reply(ctx.user_input_norm)
        ):
            cleared = dict(slots)
            cleared.pop(TRANSITION_KEY, None)
            await replace_plan_profile_slots(
                ctx.project_id,
                cleared,
                thread_id=ctx.thread_id,
                touch_plan_data_version=False,
            )
            await update_approval_step(
                project_id=ctx.project_id,
                artifact_type="business_plan",
                step="summary_confirmed",
                approved=False,
                thread_id=ctx.thread_id,
            )
            ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
            ctx.plan_intent = PLAN_INTENT_SUMMARY_REVISE
            ctx.routing_message = "분류를 다시 판단할게요. 회사/팀, 고객/문제, 매출/자금 중 바뀐 내용을 알려주세요."
            return ctx

        if not is_classification_gate:
            decision, confidence = await _classify_summary_reply_with_llm(ctx.user_input_norm)
            if decision == "confirm" and confidence >= 0.65:
                cleared = dict(slots)
                cleared.pop(TRANSITION_KEY, None)
                await replace_plan_profile_slots(
                    ctx.project_id,
                    cleared,
                    thread_id=ctx.thread_id,
                    touch_plan_data_version=False,
                )
                await update_approval_step(
                    project_id=ctx.project_id,
                    artifact_type="business_plan",
                    step="summary_confirmed",
                    approved=True,
                    thread_id=ctx.thread_id,
                )
                ctx.routing_state = PLAN_ROUTING_DRAFT_SECTIONS
                ctx.routing_message = PLAN_READY_MESSAGES["summary_confirmed"]
                ctx.plan_intent = PLAN_INTENT_SUMMARY_CONFIRM
                return ctx

            if decision == "revise" and confidence >= 0.65:
                cleared = dict(slots)
                cleared.pop(TRANSITION_KEY, None)
                await replace_plan_profile_slots(
                    ctx.project_id,
                    cleared,
                    thread_id=ctx.thread_id,
                    touch_plan_data_version=False,
                )
                await update_approval_step(
                    project_id=ctx.project_id,
                    artifact_type="business_plan",
                    step="summary_confirmed",
                    approved=False,
                    thread_id=ctx.thread_id,
                )
                ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
                ctx.plan_intent = PLAN_INTENT_SUMMARY_REVISE
                ctx.routing_message = "분류를 다시 판단할게요. 회사/팀, 고객/문제, 매출/자금 중 바뀐 내용을 알려주세요."
                return ctx

        ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
        ctx.plan_intent = PLAN_INTENT_SUMMARY if not is_classification_gate else PLAN_INTENT_PROFILE_CAPTURE
        ctx.routing_message = (
            CLASSIFICATION_CONFIRM_PROMPT
            if is_classification_gate
            else PLAN_SUMMARY_CONFIRM_RETRY_MESSAGE
        )
        return ctx

    forced_plan_intent = await _is_summary_confirmation_intent(ctx)
    if not forced_plan_intent:
        forced_plan_intent = _extract_disambiguate_intent(ctx.user_input_norm)
    if not forced_plan_intent and await _is_disambiguation_followup_affirmation(ctx):
        forced_plan_intent = PLAN_INTENT_PROFILE_CAPTURE
        ctx.add_log(
            "plan_flow",
            "Disambiguation follow-up detected (history signal + affirmative): forcing PROFILE_CAPTURE"
        )
    if not forced_plan_intent and _is_affirmative_brief_signal(ctx.user_input_norm):
        forced_plan_intent = PLAN_INTENT_PROFILE_CAPTURE

    if forced_plan_intent:
        plan_intent = forced_plan_intent
        score = 0.6
        delta = None
        score_map = {intent: 0.0 for intent in PLAN_KEYWORDS.keys()}
        score_map.setdefault(PLAN_INTENT_SUMMARY_CONFIRM, 0.0)
        score_map.setdefault(PLAN_INTENT_SUMMARY_REVISE, 0.0)
        score_map[plan_intent] = score
        ctx.add_log("plan_flow", f"Plan routing forced by user selection/affirmation: {plan_intent}")
    else:
        # seed 문구는 먼저 핵심 정보 수집(상담)으로 유도한 뒤 템플릿 확정으로 이동
        if _is_plan_seed_signal(ctx.user_input_norm):
            plan_intent = PLAN_INTENT_PROFILE_CAPTURE
            score = 0.75
            score_map = {intent: 0.0 for intent in PLAN_KEYWORDS.keys()}
            score_map[plan_intent] = score
            delta = None
            seed_mode = _infer_consultation_mode_from_seed(ctx.user_input_norm)
            if (
                seed_mode
                and seed_mode != state.consultation_mode
                and int(getattr(state, "question_total_count", 0) or 0) == 0
            ):
                await set_project_consultation_mode(
                    ctx.project_id,
                    seed_mode,
                    thread_id=ctx.thread_id,
                )
            await set_plan_suspended(ctx.project_id, False, thread_id=ctx.thread_id)
            ctx.add_log(
                "plan_flow",
                f"Business-plan seed signal detected → force PROFILE_CAPTURE (mode_hint={seed_mode or 'none'})"
            )
        else:
            plan_intent, score, score_map, delta = _score_plan_intents(ctx.user_input_norm.lower())

    if plan_suspended and _is_plan_seed_signal(ctx.user_input_norm) and not forced_plan_intent:
        await merge_plan_profile_slots(
            ctx.project_id,
            {TRANSITION_KEY: TRANSITION_AWAIT_RESUME},
            thread_id=ctx.thread_id,
            touch_plan_data_version=False,
        )
        ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
        ctx.routing_message = PLAN_RESUME_CONFIRM_MESSAGE
        ctx.plan_intent = PLAN_INTENT_PROFILE_CAPTURE
        ctx.plan_confidence = 1.0
        ctx.add_log("plan_flow", "suspended plan + seed detected -> ask resume confirmation")
        return ctx

    ctx.plan_intent = plan_intent
    ctx.plan_confidence = score
    ctx.plan_tie_delta = delta
    ctx.routing_state = PLAN_ROUTING_LEGACY

    if plan_suspended and not _is_plan_seed_signal(ctx.user_input_norm) and not forced_plan_intent:
        ctx.routing_state = PLAN_ROUTING_LEGACY
        ctx.routing_message = None
        ctx.add_log("plan_flow", "Plan flow is suspended for this thread; stay in free chat.")
        return ctx

    # 사용자가 명시적으로 NATURAL 모드로 전환한 직후의 가벼운 입력(인사/잡담)은
    # 진행중 슬롯/질문 흐름과 무관하게 자유대화로 즉시 처리한다.
    current_mode_value = ctx.mode.value if hasattr(ctx.mode, "value") else str(ctx.mode)
    if (
        ctx.mode_switch_origin == "user"
        and current_mode_value == "NATURAL"
        and _is_plan_non_match_natural_signal(ctx.user_input_norm)
        and not _is_plan_seed_signal(ctx.user_input_norm)
    ):
        ctx.routing_state = PLAN_ROUTING_LEGACY
        ctx.routing_message = None
        ctx.add_log("plan_flow", "User-forced NATURAL + casual input -> bypass plan flow and keep free chat")
        return ctx

    # 자유 대화 모드에서는 사업계획서 intent가 명시되지 않은 단순 자연어를 질문 플로우로 강제하지 않음.
    # (UI 자유모드에서 날씨/잡담 등은 자연스러운 응답으로 처리)
    # 자유대화/잡담(날씨/인사/일반 질문)은 상태와 무관하게 PLAN 질문 슬롯을 소모하지 않음.
    # 다만 PLAN 시드(사업계획서/템플릿/요약 의도)가 들어오면 예외 처리한다.
    if _is_plan_non_match_natural_signal(ctx.user_input_norm) and not _is_plan_seed_signal(ctx.user_input_norm):
        if _has_plan_progress(state, slots):
            current_last_slot = getattr(state, "last_asked_slot", None)
            await merge_plan_profile_slots(
                ctx.project_id,
                {
                    TRANSITION_KEY: TRANSITION_AWAIT_TOPIC_SWITCH,
                    TRANSITION_CONTEXT_LAST_SLOT: current_last_slot or "",
                },
                thread_id=ctx.thread_id,
                touch_plan_data_version=False,
            )
            ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
            ctx.routing_message = PLAN_TOPIC_SWITCH_CONFIRM_MESSAGE
            ctx.add_log("plan_flow", "natural non-match detected during plan -> ask topic switch confirmation")
            return ctx

        ctx.routing_state = PLAN_ROUTING_LEGACY
        ctx.routing_message = None
        if not _contains_any_token(ctx.user_input_norm, ["요약", "정리", "요약해"]):
            ctx.mode_switch_origin = "auto"
        ctx.add_log(
            "plan_flow",
            "Natural bypass signal detected without plan progress; keep free-chat flow",
        )
        return ctx

    if plan_intent == PLAN_INTENT_UNKNOWN:
        if _has_plan_progress(state, slots):
            ctx.plan_intent = PLAN_INTENT_PROFILE_CAPTURE
            ctx.add_log("plan_flow", "Unknown plan intent + progress -> continue PROFILE_CAPTURE")
        else:
            ctx.routing_state = PLAN_ROUTING_LEGACY
            ctx.routing_message = None
            ctx.add_log("plan_flow", "Unknown plan intent without progress -> stay free-chat")
            return ctx

    if not _is_v1_state_ready(state):
        ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
        ctx.routing_message = PLAN_READY_MESSAGES["consult_mode"]
        ctx.need_disambiguation = False
        ctx.add_log("plan_flow", "State-first: profile_stage 미확정/상태 미준비 → PLAN_QUESTION_FLOW")
        return ctx

    async def _template_hint_payload():
        template = await get_selected_template(
            project_id=ctx.project_id,
            artifact_type="business_plan",
            fallback_stage=state.consultation_mode,
            thread_id=ctx.thread_id,
        )
        if not template:
            return None
        return {
            "template_id": template.id,
            "template_name": template.name,
            "template_stage": template.stage,
            "template_source_pdf": template.source_pdf,
            "active": bool(state and state.active_template_id == template.id),
        }

    summary_confirmed = await _is_summary_confirmed(
        ctx.project_id,
        artifact_type="business_plan",
        thread_id=ctx.thread_id,
    )

    # 동률/저신뢰 처리 (확인 단계 생략, 초보자 안전 경로로 강제)
    if delta is not None and delta < CONFIDENCE_TIE_DELTA and score < CONFIDENCE_FORCE_MIN:
        ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
        ctx.routing_message = PLAN_READY_MESSAGES["disambiguate"]
        ctx.need_disambiguation = False
        ctx.add_log(
            "plan_flow",
            f"Confidence tie/low: top={plan_intent}/{score}, second_gap={delta}"
        )
        return ctx

    # 템플릿 선택(초기 진입)
    if plan_intent == PLAN_INTENT_TEMPLATE_SELECT:
        template_hint = await _template_hint_payload()
        draft_ready = _is_business_data_sufficient_for_draft(state)
        if not template_hint or not _template_recommendation_ready(state):
            ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
            ctx.routing_message = (
                "좋아요. 우선 핵심 정보부터 차례대로 수집할게요.\n"
                "가장 먼저 회사명, 업종, 고객/문제, 매출·자금 정보부터 묻겠습니다."
            )
            return ctx
        if draft_ready and template_hint and not summary_confirmed:
            await _route_classification_confirm_gate()
            return ctx

        if draft_ready and template_hint and summary_confirmed:
            ctx.routing_state = PLAN_ROUTING_DRAFT_SECTIONS
            ctx.routing_message = (
                "좋아요. 기본 정보 수집이 충족되어 초안 작성 단계로 이동합니다.\n"
            )
            return ctx

        ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
        ctx.routing_message = (
            "좋아요. 산출물 구조는 단계 기준으로 자동 적용되어 있습니다.\n"
            "지금은 핵심 정보 수집이 더 필요하므로, 질문을 이어서 진행해요."
        )
        ctx.add_log("plan_flow", "PLAN_TEMPLATE_SELECT path auto mapping -> continue question flow")
        return ctx

    # 섹션/작성 가드
    if plan_intent == PLAN_INTENT_DRAFT_SECTIONS:
        selected_template = await get_selected_template(
            project_id=ctx.project_id,
            artifact_type="business_plan",
            fallback_stage=state.consultation_mode,
            thread_id=ctx.thread_id,
        )
        draft_ready = _is_business_data_sufficient_for_draft(state)
        if not selected_template or not draft_ready:
            ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
            ctx.routing_message = (
                "초안 작성 전에 템플릿 확정과 기본 질문 완료가 먼저 필요합니다.\n"
                "관련 정보를 한두 개 더 채운 뒤 다시 요청해 주세요."
            )
            ctx.add_log(
                "plan_flow",
                "PLAN_DRAFT_SECTIONS 실행 조건 미충족: selected_template or required_counter"
            )
            return ctx
        if not summary_confirmed:
            await _route_classification_confirm_gate()
            return ctx
        ctx.routing_state = PLAN_ROUTING_DRAFT_SECTIONS
        if not ctx.routing_message:
            ctx.routing_message = (
                "지금은 초안 작성 준비 단계예요. "
                "수집한 내용을 바탕으로 섹션 단위로 문서를 차근차근 만들어갈게요."
            )
        ctx.add_log("plan_flow", "PLAN_DRAFT_SECTIONS 실행 guard 통과")
        return ctx

    # 정책/요건 체크는 v1에서도 질문 보강 없이 질문수행으로 유도(전환 안전성)
    if plan_intent == PLAN_INTENT_POLICY_CHECK:
        ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
        ctx.routing_message = PLAN_READY_MESSAGES["requirement_mode"]
        ctx.add_log("plan_flow", "PLAN_POLICY_CHECK는 보완 질문으로 route")
        return ctx

    # 요약/보정/오탐은 핵심 질문 미완료 시 상담 플로우로 되돌리고, 완료 시에만 도우미 처리
    if plan_intent == PLAN_INTENT_SUMMARY:
        if not _is_business_data_sufficient_for_draft(state):
            ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
            ctx.routing_message = (
                "요약은 핵심 정보(회사명/업종/고객/수익/자금)가 조금 더 확보되어야 가능합니다.\n"
                "현재는 회사·고객·필요 정보부터 계속 수집하겠습니다."
            )
            return ctx

        ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
        if not ctx.routing_message:
            slots_with_transition = dict(slots)
            slots_with_transition[TRANSITION_KEY] = TRANSITION_AWAIT_CLASSIFICATION_CONFIRM
            await replace_plan_profile_slots(
                ctx.project_id,
                slots_with_transition,
                thread_id=ctx.thread_id,
                touch_plan_data_version=False,
            )
            ctx.routing_message = _build_classification_confirmation_card(
                slots,
                state.consultation_mode if state else None,
            )
        ctx.add_log(
            "plan_flow",
            "PLAN_SUMMARY => classification confirmation flow"
        )
        return ctx

    if plan_intent == PLAN_INTENT_SUMMARY_CONFIRM:
        try:
            await update_approval_step(
                project_id=ctx.project_id,
                artifact_type="business_plan",
                step="summary_confirmed",
                approved=True,
                thread_id=ctx.thread_id,
            )
            ctx.routing_state = PLAN_ROUTING_DRAFT_SECTIONS
            ctx.routing_message = PLAN_READY_MESSAGES["summary_confirmed"]
            ctx.add_log("plan_flow", "PLAN_SUMMARY_CONFIRM => summary_confirmed=True, draft route")
            return ctx
        except HTTPException as exc:
            ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
            detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            ctx.routing_error = {
                "error_code": detail.get("error_code", "POLICY_VALIDATION_FAILED"),
                "message": detail.get("message", str(exc)),
                "violations": detail.get("violations", []),
            }
            return ctx

    if plan_intent == PLAN_INTENT_SUMMARY_REVISE:
        try:
            await update_approval_step(
                project_id=ctx.project_id,
                artifact_type="business_plan",
                step="summary_confirmed",
                approved=False,
                thread_id=ctx.thread_id,
            )
        except HTTPException:
            pass
        ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
        ctx.routing_message = (
            "네, 반영할 내용을 알려주세요.\n"
            "아래처럼 한 번에 말해주시면 바로 반영해드릴게요.\n"
            "- 빠진 항목\n"
            "- 잘못된 내용\n"
            "- 추가하고 싶은 내용\n"
            "말씀해주시면 추가 질문으로 이어서 수집해요."
        )
        ctx.add_log("plan_flow", "PLAN_SUMMARY_REVISE => summary_confirmed=False, back to question flow")
        return ctx

    if plan_intent == PLAN_INTENT_CORRECTION:
        if not _is_business_data_sufficient_for_draft(state):
            ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
            ctx.routing_message = PLAN_READY_MESSAGES["collect_more_before_assist"]
            ctx.add_log(
                "plan_flow",
                "보정 요청은 미완료 상태에서 상담 플로우(질문)로 강제 전환"
            )
            return ctx

        if not summary_confirmed:
            await _route_classification_confirm_gate()
            return ctx
        ctx.routing_state = PLAN_ROUTING_FREEFLOW
        if not ctx.routing_message:
            ctx.routing_message = PLAN_READY_MESSAGES["assistant_mode"]
        ctx.add_log("plan_flow", "PLAN_CORRECTION => PLAN_FREEFLOW")
        return ctx

    if plan_intent == PLAN_INTENT_UNKNOWN:
        if not _required_fields_ready_for_draft(state):
            ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
            ctx.routing_message = PLAN_READY_MESSAGES["consult_mode"]
            ctx.add_log(
                "plan_flow",
                "PLAN_UNKNOWN is treated as 상담 질문 플로우 until profile info is sufficient"
            )
            return ctx
        if not summary_confirmed:
            await _route_classification_confirm_gate()
            return ctx
        ctx.routing_state = PLAN_ROUTING_DRAFT_SECTIONS
        if not ctx.routing_message:
            ctx.routing_message = (
                "입력이 애매해도 수집한 정보 기반으로 초안 작성 단계로 이어서 진행할게요."
            )
        ctx.add_log("plan_flow", "PLAN_UNKNOWN + 충분한 정보 → 초안 작성 플로우로 이동")
        return ctx

    # 기본은 질문 흐름 보강
    if plan_intent == PLAN_INTENT_PROFILE_CAPTURE:
        ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
        ctx.routing_message = None
        ctx.add_log("plan_flow", "PLAN_PROFILE_CAPTURE → PLAN_QUESTION_FLOW")
        return ctx

    ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
    ctx.add_log("plan_flow", "Fallback: default PLAN_QUESTION_FLOW")
    return ctx


async def reserve_plan_question_slot(ctx: StreamContext) -> None:
    """
    서버 슬롯 할당 우선:
    - 사용자 입력에서 슬롯(회사/고객문제/매출자금)을 먼저 추출해 SSOT에 저장
    - 누락 슬롯 기준으로 다음 질문 1개만 출력
    - PLAN_QUESTION_FLOW에서는 LLM 자유응답을 막고 서버 질문 템플릿만 사용
    """
    if ctx.routing_state != PLAN_ROUTING_QUESTION_FLOW:
        return

    if not ctx.plan_intent:
        ctx.plan_intent = PLAN_INTENT_PROFILE_CAPTURE

    state = await get_project_policy_state(ctx.project_id, thread_id=ctx.thread_id)
    if not state or state.policy_version != POLICY_VERSION_V1:
        ctx.routing_message = ctx.routing_message or "질문을 이어서 진행하겠습니다."
        return

    last_asked_slot = getattr(state, "last_asked_slot", None)
    slots = await get_plan_profile_slots(ctx.project_id, thread_id=ctx.thread_id)
    extracted_updates = _extract_plan_slot_updates(ctx.user_input_norm, last_asked_slot=last_asked_slot)
    if not extracted_updates and _is_unknown_or_skip_reply(ctx.user_input_norm):
        if last_asked_slot in PLAN_REQUIRED_SLOT_ORDER and not slots.get(last_asked_slot):
            extracted_updates[last_asked_slot] = "미확인(추후 보완 필요)"

    if extracted_updates:
        slots = await merge_plan_profile_slots(
            project_id=ctx.project_id,
            slot_updates=extracted_updates,
            thread_id=ctx.thread_id,
            touch_plan_data_version=True,
        )
        state = await get_project_policy_state(ctx.project_id, thread_id=ctx.thread_id)

    missing_required = _find_missing_required_slots(slots)
    if not missing_required:
        await set_last_asked_slot(ctx.project_id, None, thread_id=ctx.thread_id)
        summary_confirmed = await _is_summary_confirmed(
            ctx.project_id,
            artifact_type="business_plan",
            thread_id=ctx.thread_id,
        )
        if summary_confirmed:
            # 요약 확정 이후에는 "초안/작성/생성" 재요청이 명시된 경우에만 재생성으로 진입한다.
            # 그렇지 않으면 자유 대화(LEGACY)로 우회해 인사/잡담이 다시 초안 생성으로 오인되지 않게 한다.
            wants_draft_regen = (
                ctx.plan_intent in {PLAN_INTENT_DRAFT_SECTIONS, PLAN_INTENT_TEMPLATE_SELECT}
                or _contains_any_token(ctx.user_input_norm, ["초안", "작성", "생성", "재생성", "다시"])
            )
            if wants_draft_regen:
                ctx.routing_state = PLAN_ROUTING_DRAFT_SECTIONS
                ctx.routing_message = PLAN_READY_MESSAGES["summary_confirmed"]
                ctx.add_log(
                    "reserve_plan_question_slot",
                    "summary confirmed + explicit draft regeneration signal -> PLAN_DRAFT_SECTIONS",
                )
            else:
                ctx.routing_state = PLAN_ROUTING_LEGACY
                ctx.routing_message = None
                ctx.add_log(
                    "reserve_plan_question_slot",
                    "summary confirmed but no draft regeneration signal -> keep free-chat (LEGACY)",
                )
        else:
            ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
            ctx.plan_intent = PLAN_INTENT_PROFILE_CAPTURE
            slots_with_transition = dict(slots)
            slots_with_transition[TRANSITION_KEY] = TRANSITION_AWAIT_CLASSIFICATION_CONFIRM
            await replace_plan_profile_slots(
                ctx.project_id,
                slots_with_transition,
                thread_id=ctx.thread_id,
                touch_plan_data_version=False,
            )
            ctx.routing_message = _build_classification_confirmation_card(
                slots,
                state.consultation_mode if state else None,
            )
        return

    next_slot = missing_required[0]
    await set_last_asked_slot(ctx.project_id, next_slot, thread_id=ctx.thread_id)
    requested = "required"

    try:
        allocation = await update_question_counters(
            ctx.project_id,
            requested_question_type=requested,
            touch_plan_data_version=False,
            thread_id=ctx.thread_id,
        )
        ctx.allocated_question_type = allocation.get("allocated_question_type")
        ctx.question_counters_snapshot = {
            "question_required_count": allocation.get("question_required_count"),
            "question_optional_count": allocation.get("question_optional_count"),
            "question_special_count": allocation.get("question_special_count"),
            "question_required_limit": allocation.get("question_required_limit"),
            "question_optional_limit": allocation.get("question_optional_limit"),
            "question_special_limit": allocation.get("question_special_limit"),
        }

        if not ctx.routing_message or ctx.plan_intent == PLAN_INTENT_PROFILE_CAPTURE:
            question = PLAN_REQUIRED_SLOT_QUESTIONS.get(
                next_slot,
                "사업계획서 작성에 필요한 정보를 한 줄로 알려주세요.",
            )
            ctx.routing_message = (
                f"{question}\n"
                "정답은 짧은 문장으로도 충분해요. 모르면 ‘잘 모르겠어요’라고 답해 주세요."
            )
        ctx.add_log(
            "reserve_plan_question_slot",
            f"project={ctx.project_id}, requested={requested}, allocated={allocation['allocated_question_type']}, next_slot={next_slot}"
        )
    except HTTPException as exc:
        # 미리 계산한 에러를 사용자에게 전달. stream은 실패로 단절시키지 않음.
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        ctx.routing_error = {
            "error_code": detail.get("error_code", "QUESTION_LIMIT_REACHED"),
            "message": detail.get("message", str(exc)),
            "counters": detail.get("counters", {}),
            "limits": detail.get("limits", {}),
        }

        state = await get_project_policy_state(ctx.project_id, thread_id=ctx.thread_id)
        summary_confirmed = False
        if state and state.policy_version == POLICY_VERSION_V1:
            try:
                summary_state = await get_or_create_approval_state(
                    ctx.project_id,
                    "business_plan",
                    thread_id=ctx.thread_id,
                )
                summary_confirmed = bool(getattr(summary_state, "summary_confirmed", False))
            except HTTPException:
                summary_confirmed = False

        if state and state.policy_version == POLICY_VERSION_V1 and _is_business_data_sufficient_for_draft(state):
            if summary_confirmed:
                ctx.routing_state = PLAN_ROUTING_DRAFT_SECTIONS
                ctx.routing_message = PLAN_READY_MESSAGES["summary_prepare"]
            else:
                ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
                slots_with_transition = dict(slots)
                slots_with_transition[TRANSITION_KEY] = TRANSITION_AWAIT_CLASSIFICATION_CONFIRM
                await replace_plan_profile_slots(
                    ctx.project_id,
                    slots_with_transition,
                    thread_id=ctx.thread_id,
                    touch_plan_data_version=False,
                )
                ctx.routing_message = _build_classification_confirmation_card(
                    slots,
                    state.consultation_mode if state else None,
                )
            ctx.add_log(
                "reserve_plan_question_slot",
                f"question limit hit but draft guard passed. summary_confirmed={summary_confirmed}",
            )
            return

        ctx.routing_state = PLAN_ROUTING_QUESTION_FLOW
        ctx.routing_message = (
            "질문 상한에 도달했습니다. 더 이상 신규 질문이 불가합니다. "
            "현재 수집된 내용으로 요약 확인이 필요합니다. ‘지금까지 내용 정리해줘’로 진행해 주세요."
        )
        ctx.add_log("reserve_plan_question_slot", f"question limit hit: {ctx.routing_error}")


async def classify_intent(ctx: StreamContext) -> StreamContext:
    """
    Step 2: Intent 분류 (<= 200줄) - 가장 중요
    
    [v3.2.1 보완] 원칙: LLM 판정 실패 시, **기본값은 무조건 NATURAL**로 설정
    
    규칙:
    - Primary Intent는 1개만
    - Secondary 신호는 flags로만
    - **명시적 키워드가 없으면 모두 NATURAL로 처리** (보수적 접근)
    
    판정 우선순위:
    1. CANCEL (명시적 키워드만: "취소", "중단", "그만", "abort")
    2. FUNCTION_WRITE (confirm_token이 명시 토큰일 때만)
    3. FUNCTION_READ (명확한 조회 패턴)
    4. REQUIREMENT (명확한 작업 요청 패턴)
    5. NATURAL (그 외 모든 경우 - 기본값)
    
    예외 처리:
    - 긴 인사 ("부자야 오늘 날씨도 좋은데 고생이 많다") → NATURAL
    - 외국어 인사 ("Hello Buja") → NATURAL
    - 모호한 명령 ("이거 좀 봐봐") → NATURAL
    """
    msg = ctx.user_input_norm
    msg_lower = msg.lower()
    
    ctx.add_log("classify_intent", f"Classifying: '{msg}'")
    
    # === 우선순위 1: CANCEL (명시적 키워드만) ===
    # [v3.2.1] 확실한 취소 신호만 인정
    cancel_tokens = ["취소", "중단", "그만", "abort"]
    if any(token in msg_lower for token in cancel_tokens):
        ctx.set_primary_intent("CANCEL")
        ctx.add_log("classify_intent", f"명시적 CANCEL 토큰 감지: {msg}")
        return ctx
    
    # === 우선순위 2: FUNCTION_WRITE (confirm_token 필수) ===
    # [v3.2.1] 명시적 확정 토큰만 인정 (CONFIRM_TOKENS: "실행 확정", "변경 확정", "START TASK 실행")
    if ctx.confirm_token_detected:
        ctx.set_primary_intent("FUNCTION_WRITE")
        ctx.add_flag("HAS_CONFIRM_TOKEN")
        ctx.add_log("classify_intent", f"명시적 FUNCTION_WRITE 토큰 감지: {ctx.confirm_token}")
        return ctx
    
    # === 우선순위 3: FUNCTION_READ (엄격한 조회 패턴만) ===
    # [v3.2.1] "현재", "지금" 같은 일상어는 너무 광범위하므로 더 엄격한 조합만 허용
    strict_read_patterns = [
        r"^(현재|지금).*(프로젝트|에이전트|워커|상태|구성|설정).*(보여|알려|확인)",  # "현재 프로젝트 보여줘"
        r"^(등록된|목록|리스트).*(보여|알려|확인)",  # "등록된 목록 보여줘"
        r"(프로젝트|에이전트|워커).*(상태|현황|목록).*(조회|확인|보여|알려)",  # "프로젝트 상태 조회"
        r"(가능한|할 수 있는).*(기능|작업|일).*(뭐야|알려|보여)",  # "가능한 기능 알려줘" [v4.2 보강]
        r"(너|시스템).*(기능|능력|역할).*(확인|설명|알려)",  # "시스템 기능 확인" [v4.2 보강]
    ]
    
    for pattern in strict_read_patterns:
        if re.search(pattern, msg):
            ctx.set_primary_intent("FUNCTION_READ")
            ctx.add_log("classify_intent", f"엄격한 FUNCTION_READ 패턴 감지")

            # [Guardrail] 혼합 발화 감지
            if any(kw in msg for kw in ["안녕", "고마워", "ㅋㅋ"]):
                ctx.add_flag("HAS_NATURAL_SIGNAL")

            return ctx

    if any(kw in msg for kw in ["안녕", "고마워", "ㅋㅋ"]):
        ctx.add_flag("HAS_NATURAL_SIGNAL")
    
    # === 우선순위 3.5: 사업계획서 시작 시그널 ===
    # 엄격한 REQUIREMENT 규칙을 만나기 전에 사업계획서 진입 구문을 먼저 포착
    if _is_plan_seed_signal(msg_lower):
        ctx.set_primary_intent("REQUIREMENT")
        ctx.add_flag("HAS_PLAN_SEED_SIGNAL")
        ctx.add_log("classify_intent", "사업계획서 시작 시그널 감지 -> REQUIREMENT 경로로 라우팅")
        return ctx
    
    # === 우선순위 4: REQUIREMENT (명확한 작업 요청만) ===
    # [v3.2.1] 작업성 발화만 인정 (단순 아이디어는 NATURAL로)
    strict_requirement_patterns = [
        r"(만들어|생성|구현|추가|수정|고쳐).*(줘|주세요|해줘|해 주세요)",  # "만들어줘", "수정해줘"
        r"(설계|계획|정리|요약).*(해줘|하자|해 주세요)",  # "설계해줘", "정리하자"
        r"프로젝트.*(만들|생성|수정|구현|추가)",  # "프로젝트 만들어"
        r"(오류|에러|버그|문제).*(고쳐|수정|해결).*(줘|주세요|해줘)",  # "오류 고쳐줘"
    ]
    
    for pattern in strict_requirement_patterns:
        if re.search(pattern, msg):
            ctx.set_primary_intent("REQUIREMENT")
            ctx.add_flag("HAS_REQUIREMENT_SIGNAL")
            ctx.add_log("classify_intent", f"명확한 REQUIREMENT 패턴 감지")
            return ctx
    
    # === 우선순위 5: TOPIC_SHIFT (극도로 명확한 경우만) ===
    # [v3.2.1] 주제 변경은 매우 명시적인 경우만 인정
    explicit_topic_shift_patterns = [
        r"^(새로|다시|처음부터).*(시작|해줘)$",  # "새로 시작해줘"
        r"주제.*바꿔",  # "주제 바꿔"
        r"다른.*얘기",  # "다른 얘기 하자"
    ]
    
    for pattern in explicit_topic_shift_patterns:
        if re.search(pattern, msg):
            ctx.set_primary_intent("TOPIC_SHIFT")
            ctx.add_log("classify_intent", "명시적 TOPIC_SHIFT 패턴 감지")
            return ctx
    
    # ================================================================
    # === [v3.2.1 핵심] 기본값: NATURAL (모든 예외 케이스 처리) ===
    # ================================================================
    # 위의 명시적 패턴에 매칭되지 않으면 모두 NATURAL로 처리
    # 이로써 다음 케이스들이 안전하게 처리됨:
    # - 긴 인사: "부자야 오늘 날씨도 좋은데 고생이 많다"
    # - 외국어 인사: "Hello Buja", "Bonjour"
    # - 모호한 명령: "이거 좀 봐봐", "저거 어떻게 됐어?"
    # - 단순 감정 표현: "ㅋㅋㅋ", "오 대박", "와 신기하다"
    
    ctx.set_primary_intent("NATURAL")
    ctx.add_log("classify_intent", f"명시적 패턴 미감지 → NATURAL (기본값 정책)")
    
    # [Guardrail] Flag 신호 감지 (응답 품질 향상용)
    if any(kw in msg for kw in ["파일", "코드", "프로젝트", "API", "데이터베이스", "서버"]):
        ctx.add_flag("HAS_BRAINSTORM_SIGNAL")
        ctx.add_log("classify_intent", "설계 관련 키워드 감지 → HAS_BRAINSTORM_SIGNAL 추가")
    
    if any(kw in msg for kw in ["상태", "현황", "어떻게", "어떤"]):
        ctx.add_flag("HAS_STATUS_SIGNAL")
    
    return ctx
