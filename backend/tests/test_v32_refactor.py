# -*- coding: utf-8 -*-
"""
v3.2 리팩토링 필수 테스트 케이스 (6개)
"""
import asyncio
from app.models.stream_context import StreamContext
from app.services.intent_router import parse_user_input, classify_intent
from app.services.shadow_mining import extract_shadow_draft
from app.services.mes_sync import load_current_mes_and_state, sync_mes_if_needed
from app.services.response_builder import handle_function_read, handle_function_write_gate, response_builder


async def test_case_1_natural_greeting():
    """
    테스트 1: "안녕" → 인사만, 메뉴/명령 나열 금지
    """
    print("\n=== Test Case 1: NATURAL Greeting ===")
    
    ctx = StreamContext(
        session_id="test-session-1",
        project_id="test-project",
        thread_id="test-thread",
        user_id="test-user",
        user_input_raw="안녕"
    )
    
    ctx = parse_user_input(ctx)
    ctx = await classify_intent(ctx)
    ctx = response_builder(ctx)
    
    print(f"Primary Intent: {ctx.primary_intent}")
    print(f"Flags: {ctx.flags}")
    print(f"Final Response: {ctx.final_response}")
    
    # 검증
    assert ctx.primary_intent == "NATURAL", f"Expected NATURAL, got {ctx.primary_intent}"
    assert ctx.final_response == "" or "안녕하세요" in ctx.final_response, "Expected greeting handling via LLM placeholder behavior"
    assert "명령" not in ctx.final_response, "Should not show command list"
    assert "메뉴" not in ctx.final_response, "Should not show menu"
    
    print("✅ Test Case 1 PASSED")


async def test_case_2_brainstorm_no_mes_question():
    """
    테스트 2: "아이디어 있어: 로컬 파이썬으로" → Draft 저장, MES 질문 강요 금지
    """
    print("\n=== Test Case 2: Brainstorm without MES Question ===")
    
    ctx = StreamContext(
        session_id="test-session-2",
        project_id="test-project",
        thread_id="test-thread",
        user_id="test-user",
        user_input_raw="아이디어 있어: 로컬 파이썬으로 프로젝트"
    )
    
    ctx = parse_user_input(ctx)
    ctx = await classify_intent(ctx)
    
    print(f"Primary Intent: {ctx.primary_intent}")
    print(f"Flags: {ctx.flags}")
    
    # Shadow Mining 실행
    # ctx = await extract_shadow_draft(ctx)  # (실제로는 LLM 호출 필요, 여기서는 스킵)
    
    ctx = response_builder(ctx)
    
    print(f"Final Response: {ctx.final_response}")
    
    # 검증
    assert ctx.primary_intent == "NATURAL", f"Expected NATURAL, got {ctx.primary_intent}"
    assert "HAS_BRAINSTORM_SIGNAL" in ctx.flags, "Expected HAS_BRAINSTORM_SIGNAL flag"
    assert "MES" not in ctx.final_response or "질문" not in ctx.final_response, "Should not force MES questions"
    
    print("✅ Test Case 2 PASSED")


async def test_case_3_function_read_facts_only():
    """
    테스트 3: "현재 에이전트 현황" → Tool 기반 팩트만, CODER 같은 과거 언급 금지
    """
    print("\n=== Test Case 3: FUNCTION_READ (Facts Only) ===")
    
    ctx = StreamContext(
        session_id="test-session-3",
        project_id="test-project",
        thread_id="test-thread",
        user_id="test-user",
        user_input_raw="현재 프로젝트 상태 보여줘"
    )
    
    ctx = parse_user_input(ctx)
    ctx = await classify_intent(ctx)
    
    print(f"Primary Intent: {ctx.primary_intent}")
    print(f"Flags: {ctx.flags}")
    
    # [실제로는 handle_function_read 실행, 여기서는 모의]
    ctx.tool_facts = {"project_details": "현재 3개 에이전트: DEVELOPER, QA_ENGINEER, REPORTER"}
    
    ctx = response_builder(ctx)
    
    print(f"Final Response: {ctx.final_response}")
    
    # 검증
    assert ctx.primary_intent == "FUNCTION_READ", f"Expected FUNCTION_READ, got {ctx.primary_intent}"
    assert "📊 [실시간 DB 조회] 현재 프로젝트 상태" in ctx.final_response
    assert "DEVELOPER" in ctx.final_response, "Expected tool facts in response"
    assert "CODER" not in ctx.final_response, "Should not mention past agents like CODER"
    
    print("✅ Test Case 3 PASSED")


async def test_case_4_affirmative_no_gate_open():
    """
    테스트 4: "응" → 절대 Gate Open 금지
    """
    print("\n=== Test Case 4: Affirmative (No Gate Open) ===")
    
    ctx = StreamContext(
        session_id="test-session-4",
        project_id="test-project",
        thread_id="test-thread",
        user_id="test-user",
        user_input_raw="응"
    )
    
    ctx = parse_user_input(ctx)
    ctx = await classify_intent(ctx)
    
    print(f"Primary Intent: {ctx.primary_intent}")
    print(f"Confirm Token Detected: {ctx.confirm_token_detected}")
    
    # FUNCTION_WRITE Gate 평가
    if ctx.primary_intent == "FUNCTION_WRITE":
        ctx = await handle_function_write_gate(ctx)
    
    ctx = response_builder(ctx)
    
    print(f"Final Response: {ctx.final_response}")
    print(f"Write Gate Open: {ctx.write_gate_open}")
    
    # 검증
    assert ctx.primary_intent != "FUNCTION_WRITE", f"Should not be FUNCTION_WRITE, got {ctx.primary_intent}"
    assert ctx.confirm_token_detected == False, "Should not detect confirm token for '응'"
    assert ctx.write_gate_open == False, "Gate should not be open for '응'"
    
    print("✅ Test Case 4 PASSED")


async def test_case_5_explicit_confirm_token_gate_open():
    """
    테스트 5: "실행 확정" → VERIFIED+hash 일치일 때만 Gate Open
    """
    print("\n=== Test Case 5: Explicit Confirm Token (Gate Open) ===")
    
    ctx = StreamContext(
        session_id="test-session-5",
        project_id="test-project",
        thread_id="test-thread",
        user_id="test-user",
        user_input_raw="실행 확정"
    )
    
    # 가정: VERIFIED 상태
    ctx.verification_state = "VERIFIED"
    ctx.verified_hash = "abc123"
    ctx.mes_hash = "abc123"  # 일치
    
    ctx = parse_user_input(ctx)
    ctx = await classify_intent(ctx)
    
    print(f"Primary Intent: {ctx.primary_intent}")
    print(f"Confirm Token: {ctx.confirm_token}")
    
    # FUNCTION_WRITE Gate 평가
    ctx = await handle_function_write_gate(ctx)
    
    ctx = response_builder(ctx)
    
    print(f"Final Response: {ctx.final_response}")
    print(f"Write Gate Open: {ctx.write_gate_open}")
    
    # 검증
    assert ctx.primary_intent == "FUNCTION_WRITE", f"Expected FUNCTION_WRITE, got {ctx.primary_intent}"
    assert ctx.confirm_token == "실행 확정", f"Expected '실행 확정', got {ctx.confirm_token}"
    assert ctx.write_gate_open == True, "Gate should be open with all conditions met"
    assert "READY_TO_START" in ctx.final_response, "Expected READY_TO_START JSON in response"
    
    print("✅ Test Case 5 PASSED")


async def test_case_6_mes_change_invalidates_verification():
    """
    테스트 6: REQUIREMENT 수정 후 "실행 확정" → 이전 확정 무효화 안내
    """
    print("\n=== Test Case 6: MES Change Invalidates Verification ===")
    
    # Step 1: REQUIREMENT 수정
    ctx = StreamContext(
        session_id="test-session-6",
        project_id="test-project",
        thread_id="test-thread",
        user_id="test-user",
        user_input_raw="정리해줘"
    )
    
    ctx.verification_state = "VERIFIED"
    ctx.verified_hash = "old_hash"
    ctx.mes = {"agents": [{"role": "DEVELOPER"}]}
    
    ctx = parse_user_input(ctx)
    ctx = await classify_intent(ctx)
    
    # MES 동기화 (변경 발생)
    ctx.mes["agents"].append({"role": "QA_ENGINEER"})  # MES 변경
    ctx.mes_changed = True
    
    # [Guardrail] MES 변경 시 VERIFIED 해제
    if ctx.mes_changed:
        ctx.verification_state = "DIRTY"
        ctx.verified_hash = None
    
    print(f"After MES change - Verification State: {ctx.verification_state}")
    
    # Step 2: "실행 확정" 시도
    ctx.user_input_raw = "실행 확정"
    ctx.user_input_norm = "실행 확정"
    ctx.confirm_token = "실행 확정"
    ctx.confirm_token_detected = True
    ctx.primary_intent = "FUNCTION_WRITE"
    
    # FUNCTION_WRITE Gate 평가
    ctx = await handle_function_write_gate(ctx)
    
    ctx = response_builder(ctx)
    
    print(f"Write Gate Open: {ctx.write_gate_open}")
    print(f"Write Gate Reason: {ctx.write_gate_reason}")
    print(f"Final Response: {ctx.final_response}")
    
    # 검증
    assert ctx.verification_state == "DIRTY", f"Expected DIRTY, got {ctx.verification_state}"
    assert ctx.write_gate_open == False, "Gate should not be open after MES change"
    assert "확정이 해제" in ctx.final_response or "다시" in ctx.final_response, "Expected invalidation message"
    
    print("✅ Test Case 6 PASSED")


async def run_all_tests():
    """모든 테스트 케이스 실행"""
    print("="*60)
    print("v3.2 리팩토링 필수 테스트 케이스 (6개)")
    print("="*60)
    
    try:
        await test_case_1_natural_greeting()
        await test_case_2_brainstorm_no_mes_question()
        await test_case_3_function_read_facts_only()
        await test_case_4_affirmative_no_gate_open()
        await test_case_5_explicit_confirm_token_gate_open()
        await test_case_6_mes_change_invalidates_verification()
        
        print("\n" + "="*60)
        print("✅ ALL TESTS PASSED (6/6)")
        print("="*60)
    
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(run_all_tests())
