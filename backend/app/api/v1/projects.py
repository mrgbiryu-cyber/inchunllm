# -*- coding: utf-8 -*-
from typing import List, Optional
import sys

# [UTF-8] Force stdout/stderr to UTF-8
if sys.stdout.encoding is None or sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding is None or sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi import Query
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from sqlalchemy import select
from datetime import datetime, timezone
import uuid

from app.models.schemas import (
    Project,
    ProjectAgentConfig,
    User,
    AgentDefinition,
    ProjectCreate,
    ChatMessageResponse,
    UserRole,
    QuestionAllocationRequest,
    QuestionAllocationResponse,
    ArtifactApprovalState,
    ArtifactApprovalUpdate,
    GrowthSupportRunRequest,
)
from app.models.company import CompanyProfile
from app.api.dependencies import get_current_user
from app.core.neo4j_client import neo4j_client
from app.core.database import get_messages_from_rdb, MessageModel, AsyncSessionLocal
from app.services.knowledge_service import knowledge_queue
from app.services.growth_support_service import growth_support_service
from app.services.growth_v1_controls import (
    POLICY_VERSION_V1,
    get_approval_state_dict,
    require_pdf_approval,
    update_approval_step,
    set_project_active_template,
    update_question_counters,
    set_project_policy_version,
    get_project_policy_version,
)

router = APIRouter()

async def _backfill_thread_owner_for_user(
    session,
    project_id: str,
    normalized_project_id,
    user_id: str,
) -> None:
    """
    Legacy thread owner backfill:
    - owner_user_id가 비어 있고
    - user sender의 metadata.user_id가 단일 사용자로 식별되는 경우에만 소유자 지정
    """
    from app.core.database import ThreadModel

    if project_id == "system-master":
        candidates_stmt = select(ThreadModel).where(
            ((ThreadModel.project_id == None) | (ThreadModel.project_id == "system-master")),
            ThreadModel.owner_user_id.is_(None),
            ThreadModel.is_deleted.is_(False),
        )
    else:
        candidates_stmt = select(ThreadModel).where(
            ThreadModel.project_id == normalized_project_id,
            ThreadModel.owner_user_id.is_(None),
            ThreadModel.is_deleted.is_(False),
        )
    candidates = (await session.execute(candidates_stmt)).scalars().all()
    changed = False
    for thread in candidates:
        msg_stmt = select(MessageModel.metadata_json).where(
            MessageModel.thread_id == thread.id,
            MessageModel.sender_role == "user",
        )
        if project_id == "system-master":
            msg_stmt = msg_stmt.where(
                (MessageModel.project_id == None) | (MessageModel.project_id == "system-master")
            )
        else:
            msg_stmt = msg_stmt.where(MessageModel.project_id == normalized_project_id)
        msg_rows = (await session.execute(msg_stmt)).all()
        owner_ids = set()
        for row in msg_rows:
            metadata = row[0] if row else None
            if isinstance(metadata, dict):
                owner = metadata.get("user_id")
                if owner:
                    owner_ids.add(str(owner))
        if len(owner_ids) == 1 and user_id in owner_ids:
            thread.owner_user_id = user_id
            changed = True
    if changed:
        await session.commit()

async def _get_project_or_recover(project_id: str, current_user: User) -> dict:
    """Helper to get project data from Neo4j, with auto-recovery for system-master"""
    project_data = await neo4j_client.get_project(project_id)
    
    # [CRITICAL] system-master는 데이터가 있어도 설정이 비어있거나 경로가 없으면 강제 복구
    is_broken_system = project_id == "system-master" and (
        not project_data or 
        not project_data.get("agent_config") or 
        not project_data.get("repo_path")
    )

    if is_broken_system:
        # structlog.get_logger(__name__).info(f"System-master missing/broken. Force-recovering for user {current_user.id}")
        now = datetime.now(timezone.utc)
        from app.core.config import settings
        
        # 완벽한 기본 설정 세트
        default_agents = ProjectAgentConfig(
            workflow_type="SEQUENTIAL",
            entry_agent_id="agent_planner_master",
            agents=[
                AgentDefinition(
                    agent_id="agent_planner_master",
                    role="PLANNER",
                    model=settings.PRIMARY_MODEL,
                    provider="OPENROUTER",
                    system_prompt="You are a Master Planner. Break down tasks into steps.",
                    config={
                        "repo_root": "D:/project/myllm",
                        "allowed_paths": ["D:/project/myllm"],
                        "tool_allowlist": ["read_file", "list_dir"],
                        "risk_level": "safe"
                    },
                    next_agents=["agent_coder_master"]
                ),
                AgentDefinition(
                    agent_id="agent_coder_master",
                    role="CODER",
                    model=settings.PRIMARY_MODEL,
                    provider="OPENROUTER",
                    system_prompt="You are a Senior Coder. Write clean, efficient code.",
                    config={
                        "repo_root": "D:/project/myllm",
                        "allowed_paths": ["D:/project/myllm"],
                        "mode": "REPAIR",
                        "change_policy": {"no_full_overwrite": True},
                        "language_stack": ["python", "javascript", "typescript"]
                    },
                    next_agents=["agent_reviewer_master"]
                ),
                AgentDefinition(
                    agent_id="agent_reviewer_master",
                    role="REVIEWER",
                    model=settings.PRIMARY_MODEL,
                    provider="OPENROUTER",
                    system_prompt="You are a Code Reviewer. Check for bugs and style.",
                    config={
                        "repo_root": "D:/project/myllm",
                        "allowed_paths": ["D:/project/myllm"],
                        "tool_allowlist": ["read_file"]
                    },
                    next_agents=[]
                )
            ]
        )

        sys_project = Project(
            id="system-master",
            name="System Master",
            description="System-wide master project for global orchestration",
            project_type="SYSTEM",
            repo_path="D:/project/myllm", # 경로 명시
            tenant_id="tenant_hyungnim",
            user_id="system",
            created_at=now,
            updated_at=now,
            agent_config=default_agents
        )
        await neo4j_client.create_project_graph(sys_project)
        project_data = await neo4j_client.get_project(project_id)
        
    if not project_data:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    # Standard user scope: block system-master and enforce user_projects assignment.
    if current_user.role == UserRole.STANDARD_USER:
        if project_id == "system-master":
            raise HTTPException(
                status_code=403,
                detail={
                    "error_code": "FORBIDDEN_ROLE",
                    "message": "system-master 프로젝트 접근 권한이 없습니다.",
                    "required": "tenant_admin 또는 super_admin",
                },
            )
        from app.core.database import AsyncSessionLocal, UserProjectModel
        async with AsyncSessionLocal() as session:
            assignment = (
                await session.execute(
                    select(UserProjectModel).where(
                        UserProjectModel.user_id == current_user.id,
                        UserProjectModel.project_id == project_id,
                    )
                )
            ).scalar_one_or_none()
            if not assignment:
                project_owner_id = str(project_data.get("user_id") or "")
                is_project_owner = project_owner_id == str(current_user.id)
                # Legacy bootstrap:
                # user_projects 할당 레코드가 프로젝트 전체에 하나도 없고,
                # 프로젝트 owner가 비어있거나 system/현재 사용자인 경우 최초 접근자를 할당한다.
                total_assignments = (
                    await session.execute(
                        select(UserProjectModel).where(
                            UserProjectModel.project_id == project_id,
                        )
                    )
                ).scalars().first()
                has_any_assignment = total_assignments is not None
                owner_bootstrap_allowed = project_owner_id in {"", "system", str(current_user.id)}
                if is_project_owner or (not has_any_assignment and owner_bootstrap_allowed):
                    session.add(
                        UserProjectModel(
                            user_id=current_user.id,
                            project_id=project_id,
                            role="editor",
                        )
                    )
                    await session.commit()
                    assignment = True
                # Shared-project bootstrap:
                # 같은 tenant의 일반 사용자가 URL로 직접 접근한 프로젝트도
                # 스레드 owner 분리 정책 하에서 개별 방을 만들 수 있도록 viewer 할당한다.
                elif project_data.get("tenant_id") == current_user.tenant_id:
                    session.add(
                        UserProjectModel(
                            user_id=current_user.id,
                            project_id=project_id,
                            role="viewer",
                        )
                    )
                    await session.commit()
                    assignment = True
            if not assignment:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error_code": "FORBIDDEN_ROLE",
                        "message": "프로젝트 접근 권한이 없습니다.",
                        "required": "tenant_admin 또는 super_admin",
                    },
                )
        
    # Check access (Tenant isolation)
    if project_id != "system-master" and project_data["tenant_id"] != current_user.tenant_id:
        raise HTTPException(
            status_code=403,
            detail={
                "error_code": "FORBIDDEN_ROLE",
                "message": "프로젝트 접근 권한이 없습니다.",
                "required": "tenant_admin 또는 super_admin",
            },
        )
        
    return project_data

@router.post("/", response_model=Project, status_code=status.HTTP_201_CREATED)
async def create_project(
    project_in: ProjectCreate,
    current_user: User = Depends(get_current_user)
):
    """Create a new project"""
    project_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    
    new_project = Project(
        id=project_id,
        name=project_in.name,
        description=project_in.description,
        project_type=project_in.project_type,
        repo_path=project_in.repo_path,
        company_profile=project_in.company_profile,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        created_at=now,
        updated_at=now,
        agent_config=project_in.agent_config
    )

    # Inject Default Agents if none provided
    if not new_project.agent_config:
        from app.core.config import settings
        # Use project_id prefix to make agent IDs unique to this project
        p_prefix = project_id[:8]
        new_project.agent_config = ProjectAgentConfig(
            workflow_type="SEQUENTIAL",
            entry_agent_id=f"agent_classification_{p_prefix}",
            agents=[
                AgentDefinition(
                    agent_id=f"agent_classification_{p_prefix}",
                    role="CLASSIFICATION",
                    model=settings.PRIMARY_MODEL,
                    provider="OPENROUTER",
                    system_prompt="You are an expert Corporate Growth Diagnostician. Analyze the company profile.",
                    next_agents=[f"agent_business_plan_{p_prefix}"]
                ),
                AgentDefinition(
                    agent_id=f"agent_business_plan_{p_prefix}",
                    role="BUSINESS_PLAN",
                    model=settings.PRIMARY_MODEL,
                    provider="OPENROUTER",
                    system_prompt="You are a Business Plan Architect. Reconstruct or generate the optimal business plan.",
                    next_agents=[f"agent_matching_{p_prefix}"]
                ),
                AgentDefinition(
                    agent_id=f"agent_matching_{p_prefix}",
                    role="MATCHING",
                    model=settings.PRIMARY_MODEL,
                    provider="OPENROUTER",
                    system_prompt="You are a Certification and IP Matching Specialist. Score and identify gaps.",
                    next_agents=[f"agent_roadmap_{p_prefix}"]
                ),
                AgentDefinition(
                    agent_id=f"agent_roadmap_{p_prefix}",
                    role="ROADMAP",
                    model=settings.PRIMARY_MODEL,
                    provider="OPENROUTER",
                    system_prompt="You are a Strategic Growth Roadmap Planner. Create a 3-year timeline.",
                    next_agents=[]
                )
            ]
        )
        # structlog.get_logger(__name__).info(f"Injected unique default agents for project {project_id}")
    
    # Save to Neo4j
    await neo4j_client.create_project_graph(new_project)

    # RDB assignment SSOT: project creator can always access their own project.
    try:
        from app.core.database import AsyncSessionLocal, UserProjectModel
        async with AsyncSessionLocal() as session:
            existing = (
                await session.execute(
                    select(UserProjectModel).where(
                        UserProjectModel.user_id == current_user.id,
                        UserProjectModel.project_id == project_id,
                    )
                )
            ).scalar_one_or_none()
            if not existing:
                session.add(
                    UserProjectModel(
                        user_id=current_user.id,
                        project_id=project_id,
                        role="editor",
                    )
                )
                await session.commit()
    except Exception:
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "PROJECT_ASSIGNMENT_FAILED",
                "message": "프로젝트 접근 권한 할당에 실패했습니다.",
                "project_id": project_id,
            },
        )
    
    # [Task 1.1] Create Default Thread (Room)
    try:
        from app.core.database import AsyncSessionLocal, ThreadModel, _normalize_project_id
        async with AsyncSessionLocal() as session:
            default_thread_id = f"thread-{uuid.uuid4()}"
            default_thread = ThreadModel(
                id=default_thread_id,
                project_id=_normalize_project_id(project_id),
                owner_user_id=current_user.id,
                title="기본 상담방",
                is_deleted=False,
            )
            session.add(default_thread)
            await session.commit()
            # structlog.get_logger(__name__).info(f"Default thread created for project {project_id}: {default_thread_id}")
    except Exception as e:
        # structlog.get_logger(__name__).error(f"Failed to create default thread: {e}")
            pass

    # v1.0 SSOT: 신규 프로젝트 생성 시 상담 정책 버전 강제 기록
    try:
        await set_project_policy_version(
            project_id=project_id,
            policy_version=POLICY_VERSION_V1,
            consultation_mode="예비",
        )
    except Exception:
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "POLICY_VERSION_NOT_ASSIGNED",
                "message": "신규 프로젝트의 policy_version 기록에 실패했습니다. 관리자에게 알림 후 마이그레이션/DB 상태 점검이 필요합니다.",
                "project_id": project_id,
            },
        )

    # [Seed Knowledge] Auto-ingest project description
    if project_in.description and len(project_in.description) > 10:
        try:
            msg_id = str(uuid.uuid4())
            content_seed = f"[Project Seed] Description: {project_in.description}"
            
            async with AsyncSessionLocal() as session:
                msg = MessageModel(
                    message_id=msg_id,
                    project_id=uuid.UUID(project_id),
                    sender_role="user",
                    content=content_seed,
                    timestamp=now,
                    metadata_json={
                        "type": "seed_knowledge",
                        "user_id": current_user.id
                    }
                )
                session.add(msg)
                await session.commit()
            
            knowledge_queue.put_nowait(msg_id)
            # structlog.get_logger(__name__).info(f"Seed knowledge queued for project {project_id}")
        except Exception as e:
            # structlog.get_logger(__name__).warning(f"Failed to ingest seed knowledge: {e}")
            pass
    
    return new_project

@router.get("/", response_model=List[Project])
async def list_projects(
    current_user: User = Depends(get_current_user)
):
    """
    List projects.
    - Super Admin: All projects in tenant
    - Standard User: Projects assigned in user_projects table
    """
    from app.core.database import AsyncSessionLocal, UserProjectModel
    from sqlalchemy import select

    # If standard user, get assigned project IDs from RDB
    assigned_project_ids = None
    
    if current_user.role == UserRole.STANDARD_USER:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(UserProjectModel.project_id).where(UserProjectModel.user_id == current_user.id)
            )
            assigned_project_ids = result.scalars().all()
            
        # If no projects assigned, return empty list immediately
        if not assigned_project_ids:
            return []

    # Query Neo4j with filter
    # If assigned_project_ids is None, it means Super Admin (fetch all)
    # If assigned_project_ids is a list, fetch only those
    projects_data = await neo4j_client.list_projects(
        current_user.tenant_id, 
        project_ids=assigned_project_ids
    )
    return [Project(**p) for p in projects_data]

@router.get("/{project_id}", response_model=Project)
async def get_project(
    project_id: str,
    current_user: User = Depends(get_current_user)
):
    """Get project details"""
    project_data = await _get_project_or_recover(project_id, current_user)
    return Project(**project_data)

@router.patch("/{project_id}", response_model=Project)
async def update_project(
    project_id: str,
    project_update: dict, # Using dict for partial update
    current_user: User = Depends(get_current_user)
):
    """Update project"""
    project_data = await _get_project_or_recover(project_id, current_user)
    
    # Update fields
    for key, value in project_update.items():
        if key in project_data and key not in ["id", "tenant_id", "user_id", "created_at"]:
            project_data[key] = value
            
    project_data["updated_at"] = datetime.now(timezone.utc)
    updated_project = Project(**project_data)
    
    await neo4j_client.create_project_graph(updated_project)
    
    return updated_project

@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    current_user: User = Depends(get_current_user)
):
    """Delete project"""
    await _get_project_or_recover(project_id, current_user)
        
    await neo4j_client.delete_project(project_id)
    return None

@router.post("/{project_id}/agents", response_model=Project)
async def save_agent_config(
    project_id: str,
    config: ProjectAgentConfig,
    current_user: User = Depends(get_current_user)
):
    """Save agent configuration for a project (Admin Only)"""
    if current_user.role == UserRole.STANDARD_USER:
        raise HTTPException(
            status_code=403,
            detail={
                "error_code": "FORBIDDEN_ROLE",
                "message": "상담사 전용 권한이 필요합니다.",
                "required": "tenant_admin 또는 super_admin",
            },
        )
        
    project_data = await _get_project_or_recover(project_id, current_user)
        
    project_data["agent_config"] = config
    project_data["updated_at"] = datetime.now(timezone.utc)
    
    project_obj = Project(**project_data)
    await neo4j_client.create_project_graph(project_obj)
    
    return project_obj

@router.get("/{project_id}/agents", response_model=ProjectAgentConfig)
async def get_agent_config(
    project_id: str,
    current_user: User = Depends(get_current_user)
):
    """Get agent configuration for a project"""
    # structlog.get_logger(__name__).debug(f"GET Agent Config for Project: {project_id}")
    
    project_data = await _get_project_or_recover(project_id, current_user)
        
    if not project_data.get("agent_config"):
        from app.core.config import settings
        # Return default config to allow editor to start with something
        return ProjectAgentConfig(
            workflow_type="SEQUENTIAL",
            entry_agent_id="agent_classification",
            agents=[
                AgentDefinition(
                    agent_id="agent_classification",
                    role="CLASSIFICATION",
                    model=settings.PRIMARY_MODEL,
                    provider="OPENROUTER",
                    system_prompt="You are an expert Corporate Growth Diagnostician.",
                    next_agents=["agent_business_plan"]
                ),
                AgentDefinition(
                    agent_id="agent_business_plan",
                    role="BUSINESS_PLAN",
                    model=settings.PRIMARY_MODEL,
                    provider="OPENROUTER",
                    system_prompt="You are a Business Plan Architect.",
                    next_agents=["agent_matching"]
                ),
                AgentDefinition(
                    agent_id="agent_matching",
                    role="MATCHING",
                    model=settings.PRIMARY_MODEL,
                    provider="OPENROUTER",
                    system_prompt="You are a Certification and IP Matching Specialist.",
                    next_agents=["agent_roadmap"]
                ),
                AgentDefinition(
                    agent_id="agent_roadmap",
                    role="ROADMAP",
                    model=settings.PRIMARY_MODEL,
                    provider="OPENROUTER",
                    system_prompt="You are a Strategic Growth Roadmap Planner.",
                    next_agents=[]
                )
            ]
        )
        
    return ProjectAgentConfig(**project_data["agent_config"])


@router.post("/{project_id}/execute", status_code=status.HTTP_202_ACCEPTED)
async def execute_project(
    request: Request,
    project_id: str,
    current_user: User = Depends(get_current_user)
):
    """
    Start project execution workflow
    """
    # structlog.get_logger(__name__).info(f"Starting execution for Project: {project_id}")
    try:
        project_data = await _get_project_or_recover(project_id, current_user)
        project = Project(**project_data)
        
        # [Defensive] Inject repo_path for system-master if missing
        if project_id == "system-master" and (not project.repo_path or project.repo_path == ""):
            # structlog.get_logger(__name__).debug(f"Injecting default repo_path for {project_id}")
            project.repo_path = "D:/project/myllm"
        
        if not project.agent_config:
            # structlog.get_logger(__name__).error(f"Project {project_id} has no agent config")
            raise HTTPException(status_code=400, detail="Project has no agent configuration")

        # Initialize Orchestrator
        job_manager = request.app.state.job_manager
        redis_client = request.app.state.redis
        
        from app.services.orchestration_service import OrchestrationService
        orchestrator = OrchestrationService(job_manager, redis_client)
        
        # Start execution
        execution_id = await orchestrator.execute_workflow(project, current_user)
        
        import structlog
        structlog.get_logger(__name__).info(
            "Workflow execution started",
            project_id=project_id,
            user_id=current_user.id,
            execution_id=execution_id
        )
        return {"message": "Workflow started", "execution_id": execution_id}
    except HTTPException:
        # Re-raise HTTP exceptions as they are
        raise
    except Exception as e:
        import traceback
        # structlog.get_logger(__name__).error(f"Execution failed for {project_id}: {str(e)}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{project_id}/test-agents")
async def test_agents(
    project_id: str,
    payload: dict, # {message: str, agent_ids: List[str]}
    current_user: User = Depends(get_current_user)
):
    """Test a group of agents in a project"""
    project_data = await _get_project_or_recover(project_id, current_user)
    
    if not project_data.get("agent_config"):
        raise HTTPException(status_code=400, detail="Project has no agent configuration")
    
    message = payload.get("message")
    agent_ids = payload.get("agent_ids", [])
    
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")
    
    # Filter agents to test
    agents_to_test = [
        AgentDefinition(**a) for a in project_data["agent_config"]["agents"]
        if a["agent_id"] in agent_ids
    ]
    
    if not agents_to_test:
        raise HTTPException(status_code=400, detail="No valid agents selected for testing")
    
    from app.services.agent_test_service import agent_test_service
    results = await agent_test_service.test_agent_group(agents_to_test, message)
    
    return results

@router.post("/{project_id}/threads", status_code=status.HTTP_201_CREATED)
async def create_project_thread(
    project_id: str,
    payload: dict, # {title: str}
    current_user: User = Depends(get_current_user)
):
    """Create a new chat thread (Room) for a project"""
    title = payload.get("title", "New Chat")
    
    # RBAC check
    await _get_project_or_recover(project_id, current_user)
    
    from app.core.database import AsyncSessionLocal, ThreadModel, _normalize_project_id, ensure_threads_soft_delete_columns
    await ensure_threads_soft_delete_columns()
    
    async with AsyncSessionLocal() as session:
        thread_id = f"thread-{uuid.uuid4()}"
        new_thread = ThreadModel(
            id=thread_id,
            project_id=_normalize_project_id(project_id),
            owner_user_id=current_user.id,
            title=title,
            is_deleted=False,
        )
        session.add(new_thread)
        await session.commit()
        
        return {"thread_id": thread_id, "title": title}

@router.get("/{project_id}/threads", response_model=List[dict])
async def get_project_threads(
    project_id: str,
    current_user: User = Depends(get_current_user)
):
    """
    Get all unique chat threads for a project.
    """
    from app.core.database import AsyncSessionLocal, MessageModel, ThreadModel, _normalize_project_id, ensure_threads_soft_delete_columns
    from sqlalchemy import select, desc, func, or_

    # RBAC check (reuse get_project logic or simple check)
    await _get_project_or_recover(project_id, current_user)

    await ensure_threads_soft_delete_columns()

    async with AsyncSessionLocal() as session:
        p_id = _normalize_project_id(project_id)
        if current_user.role == UserRole.STANDARD_USER:
            await _backfill_thread_owner_for_user(
                session=session,
                project_id=project_id,
                normalized_project_id=p_id,
                user_id=current_user.id,
            )
        default_thread_id = (
            await session.execute(
                select(ThreadModel.id)
                .where(
                    ThreadModel.project_id == p_id,
                    ThreadModel.is_deleted.is_(False),
                )
                .order_by(ThreadModel.created_at.asc())
                .limit(1)
            )
        ).scalars().first()
        if current_user.role == UserRole.STANDARD_USER:
            default_thread_id = (
                await session.execute(
                    select(ThreadModel.id)
                    .where(
                        ThreadModel.project_id == p_id,
                        ThreadModel.owner_user_id == current_user.id,
                        ThreadModel.is_deleted.is_(False),
                    )
                    .order_by(ThreadModel.created_at.asc())
                    .limit(1)
                )
            ).scalars().first()
        if project_id == "system-master":
            default_thread_id = (
                await session.execute(
                    select(ThreadModel.id)
                    .where(
                        (ThreadModel.project_id == None) | (ThreadModel.project_id == p_id),
                        ThreadModel.is_deleted.is_(False),
                    )
                    .order_by(ThreadModel.created_at.asc())
                    .limit(1)
                )
            ).scalars().first()
            if current_user.role == UserRole.STANDARD_USER:
                default_thread_id = (
                    await session.execute(
                        select(ThreadModel.id)
                        .where(
                            ((ThreadModel.project_id == None) | (ThreadModel.project_id == p_id)),
                            ThreadModel.owner_user_id == current_user.id,
                            ThreadModel.is_deleted.is_(False),
                        )
                        .order_by(ThreadModel.created_at.asc())
                        .limit(1)
                    )
                ).scalars().first()
        
        # [Task 1] Query real ThreadModel table first
        if project_id == "system-master":
            stmt = select(ThreadModel).where(
                or_(ThreadModel.project_id == None, ThreadModel.project_id == p_id)
                ,
                ThreadModel.is_deleted.is_(False),
            ).order_by(ThreadModel.updated_at.desc())
        else:
            stmt = select(ThreadModel).where(
                ThreadModel.project_id == p_id,
                ThreadModel.is_deleted.is_(False),
            ).order_by(ThreadModel.updated_at.desc())
        if current_user.role == UserRole.STANDARD_USER:
            stmt = stmt.where(ThreadModel.owner_user_id == current_user.id)

        result = await session.execute(stmt)
        threads = result.scalars().all()
        seen_thread_ids: set[str] = set()
        thread_rows = []
        for t in threads:
            if not t.id or t.id in seen_thread_ids:
                continue
            seen_thread_ids.add(t.id)
            thread_rows.append(
                {
                    "thread_id": t.id,
                    "title": t.title or "새 상담방",
                    "updated_at": t.updated_at,
                    "is_default": t.id == default_thread_id,
                }
            )

        if thread_rows:
            return thread_rows

        if current_user.role == UserRole.STANDARD_USER:
            # 표준 사용자는 소유 스레드만 조회. legacy fallback(group by messages)은 교차노출 위험이 있어 차단.
            return []

        # [Fix] If no threads found in ThreadModel, fallback to Message grouping (legacy support)
        # But ensure we return consistent structure.
        if project_id == "system-master":
            stmt = (
                select(
                    MessageModel.thread_id,
                    func.max(MessageModel.timestamp).label("last_update"),
                    func.min(MessageModel.content).label("preview"),
                )
                .where(or_(MessageModel.project_id == None, MessageModel.project_id == p_id))
                .group_by(MessageModel.thread_id)
                .order_by(desc("last_update"))
            )
        else:
            stmt = (
                select(
                    MessageModel.thread_id,
                    func.max(MessageModel.timestamp).label("last_update"),
                    func.min(MessageModel.content).label("preview"),
                )
                .where(MessageModel.project_id == p_id)
                .group_by(MessageModel.thread_id)
                .order_by(desc("last_update"))
            )
        result = await session.execute(stmt)
        raw_threads = result.all()
        return [
            {
                "thread_id": t.thread_id,
                "title": (t.preview[:30] + "...") if t.preview else "새 상담방",
                "updated_at": t.last_update,
            }
            for t in raw_threads
            if t.thread_id and t.thread_id not in seen_thread_ids
        ]

@router.get("/{project_id}/threads/{thread_id}/messages", response_model=List[ChatMessageResponse])
async def get_thread_messages(
    project_id: str,
    thread_id: str,
    limit: int = 50,
    current_user: User = Depends(get_current_user)
):
    """
    Get messages for a specific thread in a project.
    Strictly isolated by thread_id.
    """
    from app.core.database import (
        AsyncSessionLocal,
        MessageModel,
        ThreadModel,
        _normalize_project_id,
        ensure_threads_soft_delete_columns,
    )

    await ensure_threads_soft_delete_columns()

    p_id = _normalize_project_id(project_id)
    async with AsyncSessionLocal() as session:
        if current_user.role == UserRole.STANDARD_USER:
            await _backfill_thread_owner_for_user(
                session=session,
                project_id=project_id,
                normalized_project_id=p_id,
                user_id=current_user.id,
            )
        from sqlalchemy import select
        stmt = select(ThreadModel).where(
            ThreadModel.id == thread_id,
            ThreadModel.is_deleted.is_(False),
        )
        if project_id == "system-master":
            stmt = stmt.where((ThreadModel.project_id == None) | (ThreadModel.project_id == "system-master"))
        else:
            stmt = stmt.where(ThreadModel.project_id == p_id)
        if current_user.role == UserRole.STANDARD_USER:
            stmt = stmt.where(ThreadModel.owner_user_id == current_user.id)

        row = (await session.execute(stmt)).scalar_one_or_none()

        if not row:
            if current_user.role == UserRole.STANDARD_USER:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error_code": "THREAD_NOT_FOUND",
                        "message": "해당 상담방을 찾을 수 없거나 접근 권한이 없습니다.",
                        "thread_id": thread_id,
                    },
                )
            # [v1.0 Fix] legacy/legacy migration에서 ThreadModel이 없더라도 메시지 이력이 있으면 허용
            msg_stmt = select(MessageModel.thread_id).where(
                MessageModel.thread_id == thread_id,
                MessageModel.project_id == p_id,
            ).limit(1)
            if project_id == "system-master":
                msg_stmt = select(MessageModel.thread_id).where(
                    MessageModel.thread_id == thread_id,
                    (MessageModel.project_id == None) | (MessageModel.project_id == "system-master"),
                ).limit(1)
            msg_exists = (await session.execute(msg_stmt)).scalar_one_or_none()
            if not msg_exists:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error_code": "THREAD_NOT_FOUND",
                        "message": "해당 상담방을 찾을 수 없거나 삭제되었습니다.",
                        "thread_id": thread_id,
                    },
                )

    import structlog
    logger = structlog.get_logger(__name__)
    logger.info("AUDIT: get_thread_messages called", project_id=project_id, thread_id=thread_id, user_id=current_user.id)
    
    # [CRITICAL FIX] Print Debug - Force visibility
    print(f"DEBUG: get_thread_messages - ProjectID: {project_id}, ThreadID: {thread_id}")

    # Reuse existing logic but force thread_id
    result = await get_chat_history(
        project_id=project_id,
        limit=limit,
        thread_id=thread_id,
        current_user=current_user,
    )
    
    # [CRITICAL FIX] Print result count
    print(f"DEBUG: Returning {len(result)} messages for thread {thread_id}")
    
    return result


@router.delete("/{project_id}/threads/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project_thread(
    project_id: str,
    thread_id: str,
    current_user: User = Depends(get_current_user),
):
    """Soft-delete thread for user (DB data remains for potential recovery/traceability)."""
    await _get_project_or_recover(project_id, current_user)
    from app.core.database import _normalize_project_id, _naive_utcnow, ThreadModel, ensure_threads_soft_delete_columns
    from sqlalchemy import select

    await ensure_threads_soft_delete_columns()

    async with AsyncSessionLocal() as session:
        p_id = _normalize_project_id(project_id)
        if project_id == "system-master":
            stmt = select(ThreadModel).where(
                ThreadModel.id == thread_id,
                (ThreadModel.project_id == None) | (ThreadModel.project_id == "system-master")
            )
        else:
            stmt = select(ThreadModel).where(
                ThreadModel.id == thread_id,
                ThreadModel.project_id == p_id
            )
        if current_user.role == UserRole.STANDARD_USER:
            stmt = stmt.where(ThreadModel.owner_user_id == current_user.id)
        row = (await session.execute(stmt)).scalar_one_or_none()
        if not row:
            raise HTTPException(
                status_code=404,
                detail={
                    "error_code": "THREAD_NOT_FOUND",
                    "message": "삭제할 상담방을 찾을 수 없습니다.",
                    "thread_id": thread_id,
                },
            )

        default_thread_id = (
            await session.execute(
                select(ThreadModel.id)
                .where(
                    ThreadModel.project_id == p_id,
                    ThreadModel.is_deleted.is_(False),
                )
                .order_by(ThreadModel.created_at.asc())
                .limit(1)
            )
        ).scalars().first()
        if current_user.role == UserRole.STANDARD_USER:
            default_thread_id = (
                await session.execute(
                    select(ThreadModel.id)
                    .where(
                        ThreadModel.project_id == p_id,
                        ThreadModel.owner_user_id == current_user.id,
                        ThreadModel.is_deleted.is_(False),
                    )
                    .order_by(ThreadModel.created_at.asc())
                    .limit(1)
                )
            ).scalars().first()
        if project_id == "system-master":
            default_thread_id = (
                await session.execute(
                    select(ThreadModel.id)
                    .where(
                        (ThreadModel.project_id == None) | (ThreadModel.project_id == "system-master"),
                        ThreadModel.is_deleted.is_(False),
                    )
                    .order_by(ThreadModel.created_at.asc())
                    .limit(1)
                )
            ).scalars().first()
            if current_user.role == UserRole.STANDARD_USER:
                default_thread_id = (
                    await session.execute(
                        select(ThreadModel.id)
                        .where(
                            ((ThreadModel.project_id == None) | (ThreadModel.project_id == "system-master")),
                            ThreadModel.owner_user_id == current_user.id,
                            ThreadModel.is_deleted.is_(False),
                        )
                        .order_by(ThreadModel.created_at.asc())
                        .limit(1)
                    )
                ).scalars().first()

        if row.title == "기본 상담방" or row.id == default_thread_id:
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "THREAD_DELETE_BLOCKED",
                    "message": "기본 상담방은 삭제할 수 없습니다.",
                    "thread_id": thread_id,
                },
            )

        if row.is_deleted:
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        row.is_deleted = True
        now = _naive_utcnow()
        row.deleted_at = now
        row.updated_at = now
        await session.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)
@router.get("/{project_id}/chat-history", response_model=List[ChatMessageResponse])
async def get_chat_history(
    project_id: str,
    limit: int = 50,
    include_all: bool = False,
    thread_id: Optional[str] = Query(default=None),
    threadId: Optional[str] = Query(default=None, alias="threadId"),
    current_user: User = Depends(get_current_user)
):
    """Get chat history for a project (Global Timeline for the project)"""
    import structlog
    logger = structlog.get_logger(__name__)
    logger.info("AUDIT: get_chat_history called", project_id=project_id, thread_id=thread_id, user_id=current_user.id)

    # 기본적으로 최신 방의 대화만 조회해 room 단위 분리를 유지한다.
    resolved_thread_id = thread_id or threadId
    owner_user_id = current_user.id if current_user.role == UserRole.STANDARD_USER else None
    if current_user.role == UserRole.STANDARD_USER:
        include_all = False
    if not include_all and resolved_thread_id and current_user.role == UserRole.STANDARD_USER:
        from app.core.database import resolve_thread_id_for_project
        scoped_thread_id = await resolve_thread_id_for_project(
            project_id,
            requested_thread_id=resolved_thread_id,
            create_if_missing=False,
            owner_user_id=owner_user_id,
        )
        if not scoped_thread_id:
            return []
        resolved_thread_id = scoped_thread_id

    if not include_all and not resolved_thread_id:
        from app.core.database import resolve_thread_id_for_project
        resolved_thread_id = await resolve_thread_id_for_project(
            project_id,
            requested_thread_id=None,
            create_if_missing=False,
            owner_user_id=owner_user_id,
        )
        if resolved_thread_id is None and current_user.role != UserRole.STANDARD_USER:
            include_all = True

    # [Resilience] Neo4j에 프로젝트 노드가 없더라도 RDB에 히스토리가 있으면 보여줌 (404 방지)
    try:
        await _get_project_or_recover(project_id, current_user)
    except HTTPException as e:
        if e.status_code == 404:
            pass # RDB fallback
        else:
            raise e
    
    import structlog
    logger = structlog.get_logger(__name__)
    logger.info(
        "AUDIT: get_chat_history resolved thread",
        project_id=project_id,
        requested_thread_id=thread_id,
        resolved_thread_id=resolved_thread_id,
        include_all=include_all,
        user_id=current_user.id,
    )

    from sqlalchemy import select, or_
    from app.core.database import AsyncSessionLocal, MessageModel, _normalize_project_id
    
    async with AsyncSessionLocal() as session:
        # [CRITICAL] system-master인 경우 project_id가 NULL인 데이터도 함께 가져와야 함
        p_id = _normalize_project_id(project_id)
        
        # [CRITICAL FIX] Print debug before query
        print(f"DEBUG: SQL Query - project_id normalized: {p_id}, thread_id filter: {resolved_thread_id}, include_all={include_all}")
        
        if project_id == "system-master":
            stmt = select(MessageModel).where(
                or_(MessageModel.project_id == None, MessageModel.project_id == "system-master")
            )
        else:
            stmt = select(MessageModel).where(MessageModel.project_id == p_id)
            
        # [Fix] Filter by Thread ID if provided (Room Architecture)
        if resolved_thread_id and not include_all:
            stmt = stmt.where(MessageModel.thread_id == resolved_thread_id)
            print(f"DEBUG: Applied thread_id filter: {resolved_thread_id}")
            
        stmt = stmt.order_by(MessageModel.timestamp.desc()).limit(limit)
        
        result = await session.execute(stmt)
        messages = result.scalars().all()
        
        # [CRITICAL FIX] Print raw query result
        print(f"DEBUG: Raw query returned {len(messages)} messages")
        if messages:
            print(f"DEBUG: Sample message thread_id: {messages[0].thread_id}")
        
        # 사용자가 보기 편하게 다시 과거->현재 순으로 뒤집음
        messages = sorted(messages, key=lambda x: x.timestamp)
        
        logger.info("AUDIT: get_chat_history result", count=len(messages), project_id=project_id, thread_id=resolved_thread_id, include_all=include_all)

    # Filter roles and empty content for chat history
    chat_list = []
    for m in messages:
        if not m.content or not m.content.strip():
            continue
            
        role = m.sender_role
        if role in ["assistant_partial", "master", "agent", "tool", "auditor"]:
            role = "assistant"
        elif role not in ["user", "assistant", "system"]:
            role = "assistant"
        
        chat_list.append(ChatMessageResponse(
            id=str(m.message_id),
            role=role,
            content=m.content,
            created_at=m.timestamp.isoformat() if m.timestamp else datetime.now(timezone.utc).isoformat(),
            thread_id=m.thread_id,
            project_id=str(m.project_id) if m.project_id else project_id,
            request_id=m.metadata_json.get("request_id") if m.metadata_json else None, # [v4.2] Restore request_id
            metadata=m.metadata_json,
        ))
    return chat_list

@router.get("/{project_id}/knowledge-graph")
async def get_knowledge_graph(
    project_id: str,
    current_user: User = Depends(get_current_user)
):
    """Get the knowledge graph for a project (Admin Only)"""
    if current_user.role == UserRole.STANDARD_USER:
        raise HTTPException(
            status_code=403,
            detail={
                "error_code": "FORBIDDEN_ROLE",
                "message": "상담사 전용 권한이 필요합니다.",
                "required": "tenant_admin 또는 super_admin",
            },
        )

    # [v5.0 CRITICAL] Log project_id for isolation verification
    print(f"DEBUG: [API] get_knowledge_graph called for project: '{project_id}' by user: {current_user.email}")
    
    await _get_project_or_recover(project_id, current_user)
        
    graph = await neo4j_client.get_knowledge_graph(project_id)
    
    # [v5.0 CRITICAL] Log result for isolation verification
    print(f"DEBUG: [API] Returning {len(graph.get('nodes', []))} nodes and {len(graph.get('links', []))} links for project '{project_id}'")
    
    return graph


@router.post("/{project_id}/growth-support/run")
async def run_growth_support_pipeline(
    project_id: str,
    payload: GrowthSupportRunRequest,
    current_user: User = Depends(get_current_user),
):
    """Run E2E growth support pipeline (classification -> plan -> matching -> roadmap)."""
    await _get_project_or_recover(project_id, current_user)
    profile_payload = payload.profile
    if not profile_payload:
        raise HTTPException(status_code=400, detail="profile is required")

    profile = CompanyProfile(**(profile_payload.model_dump() if hasattr(profile_payload, "model_dump") else profile_payload))
    result = await growth_support_service.run_pipeline(
        project_id,
        profile,
        input_text=payload.input_text,
        research_request=payload.research.model_dump() if payload.research else None,
    )
    return result


@router.post("/{project_id}/growth-support/questions/allocate", response_model=QuestionAllocationResponse)
async def allocate_growth_question(
    project_id: str,
    payload: QuestionAllocationRequest,
    thread_id: Optional[str] = Query(default=None, alias="threadId"),
    current_user: User = Depends(get_current_user),
):
    """Server slot allocation for question progress (v1.0 only)."""
    await _get_project_or_recover(project_id, current_user)
    allocation = await update_question_counters(
        project_id,
        payload.question_type,
        thread_id=thread_id,
        touch_plan_data_version=False,
    )
    return QuestionAllocationResponse(
        project_id=project_id,
        policy_version=allocation["policy_version"],
        consultation_mode=allocation["consultation_mode"],
        requested_question_type=allocation["requested_question_type"],
        allocated_question_type=allocation["allocated_question_type"],
        question_required_count=allocation["question_required_count"],
        question_optional_count=allocation["question_optional_count"],
        question_special_count=allocation["question_special_count"],
        question_total_count=allocation["question_total_count"],
        question_required_limit=allocation["question_required_limit"],
        question_optional_limit=allocation["question_optional_limit"],
        question_special_limit=allocation["question_special_limit"],
        plan_data_version=allocation.get("plan_data_version", 0),
        summary_revision=allocation.get("summary_revision", 0),
    )


@router.get("/{project_id}/growth-support/latest")
async def get_latest_growth_support_result(
    project_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get latest growth support pipeline result from cache."""
    await _get_project_or_recover(project_id, current_user)
    data = await growth_support_service.get_latest(project_id)
    if not data:
        raise HTTPException(status_code=404, detail="No growth support result found")
    return data


@router.get("/{project_id}/artifacts/{artifact_type}")
async def get_growth_artifact(
    project_id: str,
    artifact_type: str,
    format: str = "html",
    thread_id: Optional[str] = Query(default=None, alias="threadId"),
    current_user: User = Depends(get_current_user),
):
    """Return generated artifact in requested format."""
    await _get_project_or_recover(project_id, current_user)
    if format == "pdf":
        if thread_id:
            await require_pdf_approval(
                project_id=project_id,
                artifact_type=artifact_type,
                thread_id=thread_id,
            )
        else:
            await require_pdf_approval(project_id=project_id, artifact_type=artifact_type)
    try:
        if thread_id:
            content = await growth_support_service.get_artifact(
                project_id,
                artifact_type,
                format_name=format,
                thread_id=thread_id,
            )
        else:
            content = await growth_support_service.get_artifact(
                project_id,
                artifact_type,
                format_name=format,
            )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

    if format == "pdf":
        headers = {"Content-Disposition": f"attachment; filename={artifact_type}_{project_id}.pdf"}
        return Response(content=content, media_type="application/pdf", headers=headers)
    if format == "html":
        return HTMLResponse(content=content)
    return PlainTextResponse(content=content)

@router.get("/{project_id}/documents/{doc_type}/download")
async def download_document(
    project_id: str,
    doc_type: str, # "business_plan", "roadmap"
    format: str = "html",
    thread_id: Optional[str] = Query(default=None, alias="threadId"),
    current_user: User = Depends(get_current_user)
):
    """Download generated artifacts as HTML"""
    await _get_project_or_recover(project_id, current_user)

    doc_mapping = {
        "business_plan": "business_plan",
        "roadmap": "roadmap",
        "matching": "matching",
        "bm_diagnosis": "bm_diagnosis",
    }
    artifact_type = doc_mapping.get(doc_type)
    if not artifact_type:
        raise HTTPException(status_code=400, detail="Unknown document type")

    if format == "pdf":
        if thread_id:
            await require_pdf_approval(
                project_id=project_id,
                artifact_type=artifact_type,
                thread_id=thread_id,
            )
        else:
            await require_pdf_approval(project_id=project_id, artifact_type=artifact_type)

    try:
        if thread_id:
            content = await growth_support_service.get_artifact(
                project_id,
                artifact_type,
                format_name=format,
                thread_id=thread_id,
            )
        else:
            content = await growth_support_service.get_artifact(
                project_id,
                artifact_type,
                format_name=format,
            )
        if format == "pdf":
            headers = {"Content-Disposition": f"attachment; filename={artifact_type}_{project_id}.pdf"}
            return Response(content=content, media_type="application/pdf", headers=headers)
        headers = {"Content-Disposition": f"attachment; filename={artifact_type}_{project_id}.html"}
        return HTMLResponse(content=content, headers=headers)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{project_id}/growth-support/templates/{template_id}/select", response_model=ArtifactApprovalState)
async def select_growth_template(
    project_id: str,
    template_id: str,
    thread_id: Optional[str] = Query(default=None, alias="threadId"),
    current_user: User = Depends(get_current_user),
):
    """Select a growth template for this project."""
    await _get_project_or_recover(project_id, current_user)
    await set_project_active_template(
        project_id=project_id,
        template_id=template_id,
        thread_id=thread_id,
    )
    policy_version = await get_project_policy_version(project_id, thread_id=thread_id)
    data = await get_approval_state_dict(
        project_id=project_id,
        artifact_type="business_plan",
        thread_id=thread_id,
    )
    data["policy_version"] = policy_version
    data["project_id"] = project_id
    return ArtifactApprovalState(
        project_id=project_id,
        thread_id=data.get("thread_id", thread_id or ""),
        artifact_type="business_plan",
        requirement_version=data.get("requirement_version", 0),
        key_figures_approved=data["key_figures_approved"],
        certification_path_approved=data["certification_path_approved"],
        template_selected=data["template_selected"],
        summary_confirmed=data["summary_confirmed"],
        summary_revision=data["summary_revision"],
        plan_data_version=data["plan_data_version"],
        current_requirement_version=data.get("current_requirement_version", 0),
        policy_version=policy_version,
        missing_steps=data.get("missing_steps", []),
        missing_step_guides=data.get("missing_step_guides", []),
    )


@router.get("/{project_id}/artifacts/{artifact_type}/approval", response_model=ArtifactApprovalState)
async def get_artifact_approval(
    project_id: str,
    artifact_type: str,
    thread_id: Optional[str] = Query(default=None, alias="threadId"),
    current_user: User = Depends(get_current_user),
):
    """Return approval state for artifact-level PDF gate."""
    await _get_project_or_recover(project_id, current_user)
    state = await get_approval_state_dict(
        project_id,
        artifact_type,
        thread_id=thread_id,
    )
    return ArtifactApprovalState(
        project_id=project_id,
        thread_id=state.get("thread_id", thread_id or ""),
        artifact_type=artifact_type,
        requirement_version=state.get("requirement_version", 0),
        key_figures_approved=state["key_figures_approved"],
        certification_path_approved=state["certification_path_approved"],
        template_selected=state["template_selected"],
        summary_confirmed=state["summary_confirmed"],
        summary_revision=state["summary_revision"],
        plan_data_version=state["plan_data_version"],
        current_requirement_version=state.get("current_requirement_version", 0),
        policy_version=await get_project_policy_version(project_id, thread_id=thread_id),
        missing_steps=state.get("missing_steps", []),
        missing_step_guides=state.get("missing_step_guides", []),
    )


@router.post("/{project_id}/artifacts/{artifact_type}/approval", response_model=ArtifactApprovalState)
async def set_artifact_approval(
    project_id: str,
    artifact_type: str,
    payload: ArtifactApprovalUpdate,
    thread_id: Optional[str] = Query(default=None, alias="threadId"),
    current_user: User = Depends(get_current_user),
):
    """Update one approval flag for the artifact PDF gate."""
    await _get_project_or_recover(project_id, current_user)
    state = await update_approval_step(
        project_id,
        artifact_type,
        payload.step,
        payload.approved,
        thread_id=thread_id,
    )
    return ArtifactApprovalState(
        project_id=project_id,
        thread_id=state.get("thread_id", thread_id or ""),
        artifact_type=artifact_type,
        requirement_version=state.get("requirement_version", 0),
        key_figures_approved=state["key_figures_approved"],
        certification_path_approved=state["certification_path_approved"],
        template_selected=state["template_selected"],
        summary_confirmed=state["summary_confirmed"],
        summary_revision=state["summary_revision"],
        plan_data_version=state["plan_data_version"],
        current_requirement_version=state.get("current_requirement_version", 0),
        policy_version=await get_project_policy_version(project_id, thread_id=thread_id),
        missing_steps=state.get("missing_steps", []),
        missing_step_guides=state.get("missing_step_guides", []),
    )
