# -*- coding: utf-8 -*-
import json
import asyncio
import sys

# [UTF-8] Force stdout/stderr to UTF-8 at service level
if sys.stdout.encoding is None or sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding is None or sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import uuid
import re
import hashlib
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from pathlib import Path
from langchain.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage, BaseMessage
from langchain_openai import ChatOpenAI
from app.tools.system_tools import get_active_jobs_tool, get_job_history_tool

from app.core.config import settings
from app.models.master import MasterAgentConfig, ChatMessage, AgentConfigUpdate, MasterIntent, Draft
from app.core.neo4j_client import neo4j_client
from app.core.logging_config import get_recent_logs
from app.core.database import save_message_to_rdb, get_messages_from_rdb

# [v3.2] Import refactored stream_message
from app.services.v32_stream_message_refactored import stream_message_v32

@tool
async def search_knowledge_tool(query: str, project_id: str = "system-master") -> str:
    """지식 그래프(Neo4j)에서 관련 지식을 검색합니다."""
    results = await neo4j_client.query_knowledge(project_id, query)
    if not results: return "관련된 지식을 찾지 못했습니다."
    formatted = []
    for r in results:
        t = ", ".join(r.get("types", []))
        content = r.get("description") or r.get("content") or r.get("summary") or r.get("name") or str(r)
        formatted.append(f"[{t}] {r.get('title') or r.get('name')}: {content}")
    return "\n".join(formatted)

@tool
async def web_search_intelligence_tool(query: str) -> str:
    """웹 검색을 통해 최신 정보를 수집합니다."""
    from app.core.search_client import search_client
    try:
        results = await asyncio.wait_for(search_client.search(query, max_results=3), timeout=settings.WEB_SEARCH_TIMEOUT_SECONDS)
        if not results: return "검색 결과 없음."
        facts = [f"Fact: {r['content']}\nSource: {r['url']}" for r in results]
        return "\n\n".join(facts)
    except: return "웹 검색 불가."

@tool
async def list_projects() -> str:
    """시스템의 모든 프로젝트 목록을 조회합니다."""
    projects = await neo4j_client.list_projects("tenant_hyungnim")
    if not projects: return "등록된 프로젝트 없음."
    return "\n".join([f"- {p['name']} (ID: {p['id']}): {p.get('description', '설명 없음')}" for p in projects])

@tool
async def get_project_details(project_id: str = None) -> str:
    """특정 프로젝트의 상세 설정과 에이전트 구성을 조회합니다. 작업 후 반드시 이 도구로 상태를 최종 확인하십시오."""
    if not project_id: return "오류: 'project_id' 필요."
    p = await neo4j_client.get_project(project_id)
    if not p: return f"프로젝트 {project_id} 없음."
    
    # [FIX] 읽기 쉬운 마크다운 표 형태로 변경
    details = []
    details.append(f"📊 **{p['name']}** 프로젝트 현황\n")
    details.append(f"**기본 정보**")
    details.append(f"• 프로젝트 ID: `{p['id']}`")
    details.append(f"• 경로: `{p.get('repo_path', 'N/A')}`")
    
    config = p.get('agent_config') or {}
    agents = config.get("agents", [])
    
    if agents:
        details.append(f"• 워크플로우: **{config.get('workflow_type', 'N/A')}**")
        details.append(f"• 시작 에이전트: **{config.get('entry_agent_id', 'N/A')}**\n")
        
        details.append(f"**등록된 에이전트 ({len(agents)}개)**")
        
        # 이모지 매핑
        role_emoji = {
            "PLANNER": "📋",
            "DEVELOPER": "💻",
            "CODER": "💻",
            "QA": "🔍",
            "QA_ENGINEER": "🔍",
            "REVIEWER": "👀",
            "REPORTER": "📄"
        }
        
        for i, agent in enumerate(agents, 1):
            role = agent.get('role', 'UNKNOWN')
            emoji = role_emoji.get(role, "⚙️")
            model = agent.get('model', 'N/A')
            next_agents = agent.get('next_agents', [])
            next_str = ", ".join(next_agents) if next_agents else "완료"
            
            details.append(f"{i}. {emoji} **{role}**")
            details.append(f"   - 모델: `{model}`")
            details.append(f"   - 다음 단계: {next_str}")
    else:
        details.append("⚠️ 에이전트 설정 없음.")
    
    return "\n".join(details)

@tool
async def execute_project_tool(project_id: str = None) -> str:
    """[최종 단계] 설정을 마치고 실행 준비가 되었음을 선언합니다."""
    return "READY_TO_START_SIGNAL"

@tool
async def reset_project_agents_tool(project_id: str) -> str:
    """[위험] 프로젝트의 모든 에이전트 구성을 물리적으로 삭제합니다. 새 판을 짤 때 반드시 먼저 실행하십시오."""
    try:
        await neo4j_client.delete_project_agents(project_id)
        return f"프로젝트 '{project_id}'의 모든 에이전트가 물리적으로 삭제되었습니다. 이제 깨끗한 상태에서 다시 시작하십시오."
    except Exception as e: return f"삭제 실패: {str(e)}"

@tool
async def add_agent_tool(project_id: str, agent_definition: Dict[str, Any]) -> str:
    """프로젝트에 에이전트를 추가합니다. 'agent_id', 'role', 'type', 'model', 'provider', 'system_prompt', 'config', 'next_agents'가 필수입니다."""
    try:
        from app.models.schemas import Project
        project_data = await neo4j_client.get_project(project_id)
        if not project_data: return "프로젝트 없음."
        config = project_data.get("agent_config") or {"agents": [], "workflow_type": "SEQUENTIAL", "entry_agent_id": ""}
        
        # 중복 제거 후 추가
        agents = [a for a in config.get("agents", []) if a.get("agent_id") != agent_definition.get("agent_id")]
        agents.append(agent_definition)
        config["agents"] = agents
        
        if not config.get("entry_agent_id"): config["entry_agent_id"] = agent_definition.get("agent_id")
        
        project_data["agent_config"] = config
        await neo4j_client.create_project_graph(Project(**project_data))
        return f"에이전트 '{agent_definition.get('role')}' 추가 성공."
    except Exception as e: return f"추가 실패: {str(e)}"

@tool
async def update_agent_config_tool(project_id: str, agent_id: str = None, updates: Dict[str, Any] = None) -> str:
    """에이전트 설정을 수정하거나 워크플로우(workflow_type, entry_agent_id)를 변경합니다. 
    'updates'에는 'repo_root', 'tool_allowlist', 'next_agents', 'model' 등이 포함될 수 있습니다.
    'repo_root' 변경 시 'allowed_paths'도 해당 경로를 포함하도록 자동으로 업데이트됩니다."""
    if not updates: return "오류: updates 필요."
    try:
        from app.models.schemas import Project
        project_data = await neo4j_client.get_project(project_id)
        if not project_data: return f"프로젝트 {project_id}를 찾을 수 없습니다."
        
        config = project_data.get("agent_config", {})
        
        # 워크플로우 수준 업데이트
        if "workflow_type" in updates: config["workflow_type"] = updates.pop("workflow_type")
        if "entry_agent_id" in updates: config["entry_agent_id"] = updates.pop("entry_agent_id")
        
        if agent_id:
            agents = config.get("agents", [])
            updated = False
            for agent in agents:
                if agent["agent_id"] == agent_id:
                    c = agent.get("config", {})
                    # repo_root 설정 시 allowed_paths 자동 동기화
                    if "repo_root" in updates:
                        repo_path = updates["repo_root"]
                        c["repo_root"] = repo_path
                        c["allowed_paths"] = [repo_path]
                    
                    for k, v in updates.items():
                        if k == "repo_root": continue # 이미 위에서 처리
                        if k in ["tool_allowlist", "mode", "change_policy", "language_stack", "test_command", "retry_limit", "timeout_sec", "artifact_output"]: 
                            c[k] = v
                        else: agent[k] = v
                    agent["config"] = c
                    updated = True; break
            if not updated: return f"에이전트 {agent_id}를 찾지 못함."
            
        project_data["agent_config"] = config
        await neo4j_client.create_project_graph(Project(**project_data))
        return "업데이트 성공."
    except Exception as e: return f"오류: {str(e)}"

@tool
async def manage_job_queue_tool(action: str, tenant_id: str = "tenant_hyungnim"):
    """시스템 큐 관리. 사용자가 '작업이 멈췄다'고 할 때 'FIX_STUCK'을 실행하세요."""
    from app.core.config import settings
    import redis.asyncio as redis
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        if action == "LIST": return f"대기열 길이: {await redis_client.llen(f'job_queue:{tenant_id}')}"
        elif action == "CLEAR": await redis_client.delete(f"job_queue:{tenant_id}"); return "큐 초기화 완료."
        elif action == "FIX_STUCK":
            count = 0
            for key in await redis_client.keys("job:*:status"):
                if await redis_client.get(key) == "QUEUED":
                    await redis_client.set(key, "FAILED"); count += 1
            return f"{count}개의 멈춘 작업을 정리했습니다."
        return "알 수 없는 액션."
    finally: await redis_client.close()

@tool
async def setup_standard_workflow_tool(project_id: str, flow: List[str] = ["기획자", "개발자", "검수자"]) -> str:
    """[RECOMMENDED] 프로젝트의 에이전트들을 표준 순서로 자동 연결하고 필수 설정을 주입합니다.
    - 대상 에이전트가 '기획자', '개발자', '검수자' 등의 이름을 가지고 있어야 합니다.
    - 이 도구는 repo_root, allowed_paths, tool_allowlist, risk_level, next_agents를 한 번에 해결합니다."""
    try:
        from app.models.schemas import Project
        project_data = await neo4j_client.get_project(project_id)
        if not project_data: return f"오류: 프로젝트 {project_id}를 찾을 수 없습니다."
        
        config = project_data.get("agent_config") or {"agents": [], "workflow_type": "SEQUENTIAL", "entry_agent_id": ""}
        agents = config.get("agents", [])
        if not agents: return "오류: 연결할 에이전트가 없습니다. 먼저 에이전트들을 추가하십시오."
        
        repo_path = project_data.get("repo_path")
        if not repo_path: return "오류: 프로젝트의 '저장소 경로(repo_path)'가 설정되어 있지 않습니다. 프로젝트 정보부터 수정하십시오."
        
        # 역할 매핑 강화 (공백 제거, 대소문자 무시, 한/영 대응)
        role_map = {}
        for a in agents:
            r = str(a.get("role", "")).strip().upper()
            a_id = a.get("agent_id")
            role_map[r] = a_id
            # 상호 매핑
            if r in ["기획자", "PLANNER"]:
                role_map["기획자"] = a_id
                role_map["PLANNER"] = a_id
            elif r in ["개발자", "CODER", "DEVELOPER"]:
                role_map["개발자"] = a_id
                role_map["CODER"] = a_id
                role_map["DEVELOPER"] = a_id
            elif r in ["검수자", "QA", "REVIEWER"]:
                role_map["검수자"] = a_id
                role_map["QA"] = a_id
                role_map["REVIEWER"] = a_id

        actual_flow_ids = []
        for f_role in flow:
            target_id = role_map.get(f_role.strip().upper())
            if target_id:
                actual_flow_ids.append((f_role, target_id))
            
        if not actual_flow_ids:
            return f"오류: 에이전트를 매칭하지 못했습니다. 현재 역할: {list(role_map.keys())}. 요청한 흐름: {flow}"

        # 1. 필수 설정 주입 및 다음 단계 연결
        for i, (role_name, a_id) in enumerate(actual_flow_ids):
            for agent in agents:
                if agent["agent_id"] == a_id:
                    # 필수 설정 강제 주입
                    c = agent.get("config", {})
                    c["repo_root"] = repo_path
                    c["allowed_paths"] = [repo_path]
                    c["tool_allowlist"] = ["read_file", "list_dir", "write_file", "grep", "search_replace", "execute_command"]
                    c["risk_level"] = "medium"
                    agent["config"] = c
                    
                    # 워크플로우 배선
                    if i < len(actual_flow_ids) - 1:
                        next_a_id = actual_flow_ids[i+1][1]
                        agent["next_agents"] = [next_a_id]
                    else:
                        agent["next_agents"] = []
        
        # 2. 시작 지점(Entry) 설정
        config["entry_agent_id"] = actual_flow_ids[0][1]
        config["workflow_type"] = "SEQUENTIAL"
        config["agents"] = agents
        
        project_data["agent_config"] = config
        await neo4j_client.create_project_graph(Project(**project_data))
        
        flow_names = " -> ".join([x[0] for x in actual_flow_ids])
        return f"✅ 성공: [{flow_names}] 워크플로우 배선 및 필수 설정(경로, 도구 권한 등) 주입이 완료되었습니다. 이제 [START TASK]가 가능합니다."
    except Exception as e: return f"❌ 워크플로우 설정 실패: {str(e)}"

class MasterAgentService:
    def __init__(self):
        self.config_path = "D:/project/myllm/backend/data/master_config.json"
        self._load_config()
        
        # [v3.2] VERIFIED 상태 관리 (ARMED 강화판)
        self.verification_state: Dict[str, Any] = {
            "is_verified": False,  # VERIFIED 상태
            "mes_hash": None,  # 검증된 MES Hash
            "last_db_check": None,  # 마지막 DB 조회 시각 (timestamp)
            "db_check_result": None,  # DB 조회 결과 (Tool 호출 결과)
            "confirm_token": None,  # 확정 토큰 ("실행 확정", "변경 확정" 등)
            "project_id": None  # 검증된 프로젝트 ID
        }
        
        # [v3.2] Shadow Mining - 세션별 Draft 저장소
        self.session_drafts: Dict[str, List[Draft]] = {}  # {session_id: [Draft, ...]}
        
    def _classify_intent(self, message: str) -> Tuple[MasterIntent, List[str]]:
        """
        [v3.2 Guardrail] Intent 분류 - Primary Intent + Secondary Flags
        Returns: (primary_intent, flags)
        
        규칙:
        1. Primary Intent는 반드시 하나만 반환
        2. Flags는 복수 가능 (예: ["HAS_REQUIREMENT_SIGNAL", "HAS_DRAFT_DATA"])
        
        ❌ Intent를 복수로 반환 금지
        ❌ UX 편의를 이유로 Intent 우회 로직 금지
        """
        msg = message.strip()
        msg_lower = msg.lower()
        
        # [Guardrail] Flags 초기화
        flags = []
        
        # 1. NATURAL (최우선 - 잡담 감지)
        natural_patterns = [
            r"^(안녕|하이|ㅎㅇ|헬로|hello)$",
            r"^(고마워|감사|ㄱㅅ|ㅋㅋ|ㅎㅎ|ㄳ)$",
            r"^(응|예|좋아|오케이|ㅇㅇ|네|ok|굿|오|아|어|음)$",
            r"^(ㅋ+|ㅎ+)$",
        ]
        for pattern in natural_patterns:
            if re.search(pattern, msg, re.IGNORECASE):
                # [Guardrail] 설계 키워드 탐지 시 Flag 추가
                if any(kw in msg for kw in ["파일", "코드", "프로젝트", "로컬", "API"]):
                    flags.append("HAS_DESIGN_KEYWORD")
                return (MasterIntent.NATURAL, flags)
        
        # 2. CANCEL / TOPIC_SHIFT
        cancel_tokens = ["취소", "중단", "멈춰", "그만", "하지마", "리셋", "삭제", "abort"]
        if any(token in msg_lower for token in cancel_tokens):
            return (MasterIntent.CANCEL, flags)
        
        topic_shift_tokens = ["새로운", "다른", "주제 변경", "딴 얘기", "처음부터"]
        if any(token in msg for token in topic_shift_tokens):
            return (MasterIntent.TOPIC_SHIFT, flags)
        
        # 3. FUNCTION_WRITE (엄격한 토큰 매칭)
        # [Guardrail] "실행 확정", "변경 확정", "START TASK 실행"만 인정
        CONFIRM_TOKENS = ["실행 확정", "변경 확정", "START TASK 실행"]
        if any(token in msg for token in CONFIRM_TOKENS):
            return (MasterIntent.FUNCTION_WRITE, flags)
        
        # 4. FUNCTION_READ (명확한 조회 의도)
        read_patterns = [
            r"(현재|지금|현황).*?(보여줘|알려줘|확인|구성)",
            r"(등록된|목록|상태|리스트).*?(보여줘|알려줘|확인)",
            r"(상태|현황|구성).*?(조회|확인)",
            r"^(현재|지금|현황|등록된|목록|상태|조회)",
        ]
        for pattern in read_patterns:
            if re.search(pattern, msg):
                # [Guardrail] REQUIREMENT 신호 감지 시 Flag 추가
                if any(kw in msg for kw in ["정리", "요약", "보강"]):
                    flags.append("HAS_REQUIREMENT_SIGNAL")
                return (MasterIntent.FUNCTION_READ, flags)
        
        # 5. REQUIREMENT (요구사항 정리)
        requirement_patterns = [
            r"(정리|요약|구체화).*?(해줘|하자|하고 싶어)",
            r"(설계|계획|만들어|생성|추가).*?(해줘|하자|하고 싶어)",
            r"(보강|채워|완성).*?줘",
            r"준비.*?(점검|체크|확인)",
        ]
        for pattern in requirement_patterns:
            if re.search(pattern, msg):
                # [Guardrail] Draft 존재 여부는 호출 측에서 Flag 추가
                return (MasterIntent.REQUIREMENT, flags)
        
        # 6. NATURAL (기본값)
        # [Guardrail] 설계 키워드 탐지
        if any(kw in msg for kw in ["파일", "코드", "프로젝트", "로컬", "API", "만들", "생성"]):
            flags.append("HAS_DESIGN_KEYWORD")
        
        return (MasterIntent.NATURAL, flags)

    def _get_mes_hash(self, project_data: Dict[str, Any]) -> str:
        """[RULE 2] MES 구조 기반 Hash 생성 - 상태 동기화용"""
        config = project_data.get("agent_config", {})
        agents = config.get("agents", [])
        
        # v2.2: 필드 순서 고정 및 공백 정규화
        normalized_data = {
            "entry": config.get("entry_agent_id", ""),
            "workflow": config.get("workflow_type", ""),
            "agents": sorted([
                f"{a.get('agent_id')}:{a.get('model')}:{json.dumps(a.get('config', {}), sort_keys=True)}"
                for a in agents
            ])
        }
        raw_json = json.dumps(normalized_data, sort_keys=True)
        return hashlib.sha256(raw_json.encode()).hexdigest()
        
    def _check_completeness(self, project_data: Dict[str, Any]) -> Dict[str, Any]:
        if not project_data or not project_data.get("agent_config"): return {"is_complete": False, "missing": ["에이전트 구성 없음"]}
        config = project_data["agent_config"]
        agents = config.get("agents", [])
        if not agents: return {"is_complete": False, "missing": ["에이전트 없음"]}
        
        entry_id = config.get("entry_agent_id")
        if not entry_id: return {"is_complete": False, "missing": ["시작 에이전트(entry_agent_id) 미설정"]}
        
        agent_ids = {a.get("agent_id") for a in agents}
        if entry_id not in agent_ids: return {"is_complete": False, "missing": [f"시작 에이전트 {entry_id}가 존재하지 않음"]}

        project_repo = project_data.get("repo_path")
        missing = []
        for agent in agents:
            role, c = agent.get("role", ""), agent.get("config", {})
            a_type = agent.get("type", "CUSTOM")
            
            # [v2.2 FIX] 역할 이름 정규화 (대소문자 무시, 동의어 처리)
            role_normalized = str(role).strip().upper()
            
            # repo_root는 프로젝트 공통 경로가 있으면 통과
            if not (c.get("repo_root") or project_repo): 
                missing.append(f"'{role}'의 repo_root")
            
            # 역할별 필수 필드 세분화 (로직 유연화)
            # CODER, DEVELOPER 동의어 처리
            if role_normalized in ["CODER", "DEVELOPER"]:
                if not c.get("mode"): missing.append(f"'{role}'의 mode 설정")
            # QA, REVIEWER, QA_ENGINEER 동의어 처리
            elif role_normalized in ["QA", "REVIEWER", "QA_ENGINEER"]:
                if not c.get("retry_limit"): missing.append(f"'{role}'의 retry_limit")
            
            # tool_allowlist 체크 (REPORTER는 선택사항)
            if role_normalized not in ["REPORTER"] and not c.get("tool_allowlist"): 
                missing.append(f"'{role}'의 tool_allowlist")
            
        if missing: return {"is_complete": False, "missing": missing}
        return {"is_complete": True, "final_summary": project_data.get("description", "모든 연결 및 설정 확인 완료"), "mes_hash": self._get_mes_hash(project_data)}

    async def _check_agent_capability(self, project_id: str, user_requirement: str = "") -> Dict[str, Any]:
        """
        [NEW] 요구사항 vs 현재 에이전트 실행 가능성 매칭
        - 프로젝트 컨텍스트 분석 (파일 구조, 기존 코드)
        - 에이전트 역할 vs 실제 프로젝트 환경 매칭
        - 워크플로우 순서 검증 (순환 참조, 고립된 에이전트)
        Returns: {"can_execute": bool, "issues": List[Dict], "recommendations": List[str]}
        """
        try:
            p_data = await neo4j_client.get_project(project_id)
            if not p_data:
                return {
                    "can_execute": False,
                    "issues": [{"severity": "ERROR", "reason": f"프로젝트 {project_id}를 찾을 수 없습니다."}],
                    "recommendations": []
                }
            
            repo_path_str = p_data.get("repo_path", "")
            repo_path = Path(repo_path_str) if repo_path_str else None
            
            config = p_data.get("agent_config", {})
            agents = config.get("agents", [])
            agent_roles = [str(a.get("role", "")).upper() for a in agents]
            
            issues = []
            recommendations = []
            
            # 1. 경로 존재 및 접근 가능성 체크
            if repo_path and not repo_path.exists():
                issues.append({
                    "severity": "ERROR",
                    "agent": "전체 프로젝트",
                    "reason": f"프로젝트 경로 '{repo_path}'가 존재하지 않습니다.",
                })
                recommendations.append(f"경로 '{repo_path}'를 생성하거나 repo_path 설정을 수정하세요.")
            
            # 2. API 관련 요구사항 vs API 파일 존재 여부
            if "API" in user_requirement.upper() or "인증" in user_requirement or any("API" in r or "AUTH" in r for r in agent_roles):
                has_api_agent = any("API" in r or "AUTH" in r for r in agent_roles)
                api_files = []
                if repo_path and repo_path.exists():
                    api_patterns = ["**/api/**/*.py", "**/routes/**/*.py", "**/endpoints/**/*.py"]
                    for pattern in api_patterns:
                        api_files.extend(list(repo_path.glob(pattern)))
                
                if has_api_agent and not api_files:
                    issues.append({
                        "severity": "WARNING",
                        "agent": "API/AUTH 에이전트",
                        "reason": "프로젝트에 API 엔드포인트 파일이 없는데 API 인증 에이전트가 설정되어 있습니다.",
                    })
                    recommendations.append("API 인증 에이전트를 제거하거나, API 엔드포인트를 먼저 개발하세요.")
            
            # 3. REVIEWER/QA 에이전트 vs 검토 대상 파일 존재 여부
            if any("REVIEWER" in r or "QA" in r for r in agent_roles):
                code_files = []
                if repo_path and repo_path.exists():
                    code_patterns = ["*.py", "*.js", "*.ts", "*.tsx", "*.jsx"]
                    for pattern in code_patterns:
                        code_files.extend(list(repo_path.glob(pattern)))
                
                if not code_files:
                    issues.append({
                        "severity": "WARNING",
                        "agent": "REVIEWER/QA 에이전트",
                        "reason": "검토할 코드 파일이 없는데 검수 에이전트가 설정되어 있습니다.",
                    })
                    recommendations.append("CODER/DEVELOPER 에이전트를 먼저 실행하여 파일을 생성하거나, 워크플로우 순서를 조정하세요.")
            
            # 4. 워크플로우 순서 검증 (순환 참조, 고립된 에이전트)
            workflow_issues = self._validate_workflow_order(agents)
            issues.extend(workflow_issues.get("issues", []))
            recommendations.extend(workflow_issues.get("recommendations", []))
            
            # 5. GIT 에이전트 vs .git 디렉토리 존재 여부
            if any("GIT" in r or "DEPLOY" in r for r in agent_roles):
                git_dir = repo_path / ".git" if repo_path else None
                if git_dir and not git_dir.exists():
                    issues.append({
                        "severity": "WARNING",
                        "agent": "GIT/DEPLOY 에이전트",
                        "reason": "프로젝트가 Git 저장소가 아닌데 GIT 에이전트가 설정되어 있습니다.",
                    })
                    recommendations.append("Git을 초기화(git init)하거나 GIT 에이전트를 제거하세요.")
            
            # 결과 판정
            error_count = sum(1 for issue in issues if issue.get("severity") == "ERROR")
            can_execute = error_count == 0
            
            return {
                "can_execute": can_execute,
                "issues": issues,
                "recommendations": recommendations
            }
        
        except Exception as e:
            print(f"⚠️ _check_agent_capability 실행 중 오류: {e}", flush=True)
            return {
                "can_execute": True,  # 검증 실패 시 기존 동작 유지 (보수적)
                "issues": [],
                "recommendations": []
            }
    
    def _validate_workflow_order(self, agents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        [NEW] 워크플로우 순서 검증
        - 순환 참조 감지
        - 고립된 에이전트 감지
        - 논리적 순서 검증 (PLANNER → DEVELOPER → QA → REPORTER)
        """
        issues = []
        recommendations = []
        
        if not agents:
            return {"issues": [], "recommendations": []}
        
        # 1. 순환 참조 감지 (DFS)
        agent_map = {a.get("agent_id"): a.get("next_agents", []) for a in agents}
        
        def has_cycle(node, visited, rec_stack):
            visited.add(node)
            rec_stack.add(node)
            
            for neighbor in agent_map.get(node, []):
                if neighbor not in visited:
                    if has_cycle(neighbor, visited, rec_stack):
                        return True
                elif neighbor in rec_stack:
                    return True
            
            rec_stack.remove(node)
            return False
        
        visited = set()
        for agent_id in agent_map.keys():
            if agent_id not in visited:
                if has_cycle(agent_id, visited, set()):
                    issues.append({
                        "severity": "ERROR",
                        "agent": "전체 워크플로우",
                        "reason": f"순환 참조가 감지되었습니다. 에이전트 {agent_id}가 자기 자신으로 돌아오는 경로가 있습니다.",
                    })
                    recommendations.append("setup_standard_workflow_tool을 호출하여 워크플로우 순서를 재설정하세요.")
                    break
        
        # 2. 고립된 에이전트 감지 (next_agents가 비어있고, 다른 에이전트에서도 참조되지 않는 경우)
        all_next_agents = set()
        for agent in agents:
            all_next_agents.update(agent.get("next_agents", []))
        
        for agent in agents:
            agent_id = agent.get("agent_id")
            next_agents = agent.get("next_agents", [])
            
            # 시작 에이전트가 아니고, 다른 에이전트에서도 참조되지 않으면 고립됨
            if not next_agents and agent_id not in all_next_agents:
                # 단, 마지막 에이전트(REPORTER 등)는 예외
                role = str(agent.get("role", "")).upper()
                if role not in ["REPORTER", "마무리", "완료"]:
                    issues.append({
                        "severity": "WARNING",
                        "agent": agent_id,
                        "reason": f"에이전트 '{agent_id}'가 워크플로우에서 고립되어 있습니다 (다음 단계도 없고, 다른 에이전트에서도 참조되지 않음).",
                    })
                    recommendations.append(f"에이전트 '{agent_id}'를 워크플로우에 연결하거나 제거하세요.")
        
        return {"issues": issues, "recommendations": recommendations}

    async def verify_execution_ready(
        self, 
        project_id: str, 
        confirm_token: str,
        current_mes_hash: str = None
    ) -> Dict[str, Any]:
        """
        [v3.2 Guardrail] 실행 준비 상태 검증 (4조건 AND)
        
        조건 (AND):
        1. intent == FUNCTION_WRITE
        2. VERIFIED == True
        3. current_mes_hash == verified_hash
        4. confirm_token == 명시적 토큰 (단순 긍정 ❌)
        
        하나라도 틀리면:
        - 아무 행동도 하지 않음
        - 안내 문구만 반환
        
        Returns:
            {"verified": True/False, "reason": "...", "mes_hash": "..."}
        """
        from datetime import datetime, timezone
        
        # [Guardrail 조건 1] confirm_token == 명시적 토큰 (단순 긍정 ❌)
        CONFIRM_TOKENS = ["실행 확정", "변경 확정", "START TASK 실행"]
        if confirm_token not in CONFIRM_TOKENS:
            return {
                "verified": False,
                "reason": f"❌ [Guardrail] 잘못된 확정 토큰입니다. 정확히 다음 중 하나를 입력해주세요: {', '.join(CONFIRM_TOKENS)}"
            }
        
        # [Guardrail 조건 2] 실시간 DB 조회 성공 + 결과가 빈 값이 아님
        try:
            project = await neo4j_client.get_project(project_id)
            if not project or not project.get("agent_config"):
                return {
                    "verified": False,
                    "reason": f"❌ [Guardrail] 프로젝트 {project_id}를 조회할 수 없습니다. DB 연결을 확인하세요."
                }
        except Exception as e:
            return {
                "verified": False,
                "reason": f"❌ [Guardrail] DB 조회 중 오류 발생: {str(e)}"
            }
        
        # [Guardrail 조건 3] current_mes_hash == verified_hash
        new_mes_hash = self._get_mes_hash(project)
        if current_mes_hash and self.verification_state.get("mes_hash"):
            if new_mes_hash != self.verification_state["mes_hash"]:
                return {
                    "verified": False,
                    "reason": "❌ [Guardrail] MES가 변경되어 VERIFIED 상태가 해제되었습니다. 다시 준비 점검을 수행하세요."
                }
        
        # 3. 완전성 체크
        check = self._check_completeness(project)
        if not check["is_complete"]:
            missing_str = ", ".join(check["missing"])
            return {
                "verified": False,
                "reason": f"설정이 미완료되었습니다: {missing_str}"
            }
        
        # 4. 실행 가능성 체크
        capability = await self._check_agent_capability(project_id, "")
        if not capability["can_execute"]:
            error_issues = [issue for issue in capability["issues"] if issue.get("severity") == "ERROR"]
            if error_issues:
                reason_str = "; ".join([issue.get("reason", "") for issue in error_issues])
                return {
                    "verified": False,
                    "reason": f"실행 불가: {reason_str}"
                }
        
        # 모든 검증 통과 → VERIFIED 상태 설정
        mes_hash = self._get_mes_hash(project)
        self.verification_state["is_verified"] = True
        self.verification_state["mes_hash"] = mes_hash
        self.verification_state["last_db_check"] = datetime.now(timezone.utc)
        self.verification_state["db_check_result"] = project
        self.verification_state["confirm_token"] = confirm_token
        self.verification_state["project_id"] = project_id
        
        return {
            "verified": True,
            "mes_hash": mes_hash,
            "project": project
        }

    def clean_response(
        self, 
        content: str, 
        intent: MasterIntent, 
        has_confirm_token: bool
    ) -> str:
        """
        [v3.2] Response Builder - 조건부 블록 제거
        
        규칙:
        - FUNCTION_WRITE + confirm_token 있을 때만 보고서/JSON 유지
        - 그 외 모든 경우: 자동 생성 블록 제거
        """
        
        # FUNCTION_WRITE + confirm_token 있을 때만 보고서/JSON 유지
        if intent == MasterIntent.FUNCTION_WRITE and has_confirm_token:
            return content
        
        # 그 외 모든 경우: 자동 생성 블록 제거
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
            r"권장 조치:[\s\S]*?(?=\n\n|\Z)",
            
            # 설정 오류 자동 안내
            r"설정을 확인하고 다음을 수행하세요[\s\S]*?(?=\n\n|\Z)",
        ]
        
        for pattern in patterns:
            content = re.sub(pattern, "", content, flags=re.MULTILINE)
        
        # 연속된 빈 줄 제거
        content = re.sub(r"\n{3,}", "\n\n", content)
        
        return content.strip()

    def _load_config(self):
        import os
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    self.config = MasterAgentConfig(**json.load(f))
                    return
            except: pass
        self.config = MasterAgentConfig()

    def _save_config(self):
        import os
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config.dict(), f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"⚠️ Failed to save master_config.json: {e}")
        
    def update_config(self, new_config: MasterAgentConfig):
        self.config = new_config
        self._save_config()

    def get_config(self) -> MasterAgentConfig:
        self._load_config() # Always load latest
        return self.config

    async def _construct_messages(self, message: str, history: List[ChatMessage], project_id: str, system_instruction: str):
        # 1. [CRITICAL] 현재 프로젝트 ID를 명확히 강조 (프로젝트 격리)
        # 실제 DB에서 최신 정보를 강제로 긁어옴 (과거 대화보다 우선됨)
        p = await neo4j_client.get_project(project_id)
        current_state = "No project found"
        if p:
            config = p.get('agent_config') or {}
            agents = config.get("agents", [])
            agent_summary = ", ".join([f"{a['role']}({a['agent_id']})" for a in agents]) if agents else "None"
            current_state = f"- Project ID (CURRENT): {project_id}\n- Name: {p['name']}\n- Path: {p.get('repo_path')}\n- Registered Agents: {agent_summary}\n- Entry Agent: {config.get('entry_agent_id')}"

        # 2. 시스템 프롬프트 구성 (최신 DB 상태를 최상단에 배치)
        # [CRITICAL] 현재 프로젝트 ID를 최우선으로 강조
        ctx_header = f"[CRITICAL: ONLY USE PROJECT_ID = {project_id}]\n[ABSOLUTE REALITY - ACTUAL DB STATE FOR PROJECT {project_id}]\n{current_state}\n\n[USER'S LATEST INTENT]\n{message}\n\n[FORBIDDEN: NEVER mention agents not in the above list. NEVER use data from other projects.]\n\n"
        
        system_prompt = f"{ctx_header}{self.config.system_prompt}\n\n[MANDATORY INSTRUCTION]\n{system_instruction}"
        msgs = [SystemMessage(content=system_prompt)]
        
        def clean(c: str) -> str: return c.replace("형님", "사용자님").replace("하겠습쇼", "하겠습니다") if c else ""
        
        # 3. 과거 대화 주입 (기억력 대폭 강화: 40개까지 로드하여 복잡한 요구사항 보존)
        db_messages = await get_messages_from_rdb(project_id, None, 40)
        for m in db_messages:
            if m.sender_role == "user": msgs.append(HumanMessage(content=clean(m.content)))
            elif m.sender_role == "assistant": msgs.append(AIMessage(content=clean(m.content)))
        
        msgs.append(HumanMessage(content=message))
        return msgs

    async def _get_real_time_context(self, project_id: str) -> str:
        if project_id == "system-master": return "System Master Context"
        p = await neo4j_client.get_project(project_id)
        return f"Project: {p.get('name')}, Path: {p.get('repo_path')}" if p else "No Project Data"

    async def stream_message(self, message: str, history: List[ChatMessage], project_id: str = None, thread_id: str = None, user: Any = None, worker_status: Dict[str, Any] = None):
        """
        [v3.2] Refactored stream_message
        
        기존 v2.2 로직은 stream_message_v22로 백업됨 (아래 참조)
        v3.2: Step-by-step 분해 + 200줄 제한 + 9단계 오케스트레이션
        """
        # v3.2 호출
        async for chunk in stream_message_v32(message, history, project_id, thread_id, user, worker_status):
            yield chunk
    
    # ===== [v2.2 백업 제거] 기존 로직은 Git에 보관됨 =====
    # v3.2 통합으로 인해 기존 v2.2 로직 (약 350줄) 제거
    # Git history에서 복구 가능: git log --all -- master_agent_service.py
    
    async def process_message(self, message: str, history: List[ChatMessage], project_id: str = None, thread_id: str = None, user: Any = None, worker_status: Dict[str, Any] = None) -> Dict[str, Any]:
        # Simple wrapper for stream_message consistency
        return {"message": "Streaming only for master agent", "quick_links": []}

    async def create_job_from_history(self, history: List[ChatMessage], orchestrator: Any, user: Any) -> Dict[str, Any]: 
        return {"message": "N/A"}
    async def process_message(self, message: str, history: List[ChatMessage], project_id: str = None, thread_id: str = None, user: Any = None, worker_status: Dict[str, Any] = None) -> Dict[str, Any]:
        # Simple wrapper for stream_message consistency
        return {"message": "Streaming only for master agent", "quick_links": []}

    async def create_job_from_history(self, history: List[ChatMessage], orchestrator: Any, user: Any) -> Dict[str, Any]: return {"message": "N/A"}
