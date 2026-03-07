import pytest
from fastapi import FastAPI, HTTPException
from httpx import AsyncClient, ASGITransport
from types import SimpleNamespace

from app.api.v1 import projects as projects_module
from app.api.dependencies import get_current_user
from app.models.schemas import User, UserRole
from app.services import growth_v1_controls
from app.services import growth_support_service as growth_support_service_module


async def _fake_user() -> User:
    return User(
        id="u1",
        username="admin",
        tenant_id="tenant_hyungnim",
        role=UserRole.SUPER_ADMIN,
        is_active=True,
    )


async def _fake_get_project_or_recover(project_id: str, current_user: User):
    return {
        "id": project_id,
        "name": "P",
        "tenant_id": current_user.tenant_id,
        "user_id": current_user.id,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }


class _DummySession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def merge(self, *_):
        return None

    async def commit(self):
        return None


class _ConversationState:
    def __init__(self):
        self.project_id = "p1"
        self.policy_version = growth_v1_controls.POLICY_VERSION_V1
        self.consultation_mode = growth_v1_controls.CONSULTATION_MODE_PRELIMINARY
        self.question_mode = "server"
        self.question_required_limit = 1
        self.question_optional_limit = 1
        self.question_special_limit = 1
        self.question_required_count = 0
        self.question_optional_count = 0
        self.question_special_count = 0
        self.question_total_count = 0


def _build_policy_app():
    app = FastAPI()
    app.include_router(projects_module.router, prefix="/api/v1/projects")
    return app


@pytest.mark.asyncio
async def test_pdf_gate_artifacts_route_blocks_business_plan_only(monkeypatch):
    app = _build_policy_app()
    app.dependency_overrides[get_current_user] = _fake_user
    monkeypatch.setattr(projects_module, "_get_project_or_recover", _fake_get_project_or_recover)

    calls = []

    async def fake_require_pdf_approval(project_id: str, artifact_type: str = "business_plan"):
        calls.append((project_id, artifact_type))
        if artifact_type == "business_plan":
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "APPROVAL_INCOMPLETE",
                    "message": "사업계획서 PDF 승인 단계가 미완료입니다.",
                    "missing_steps": ["summary_confirmed"],
                },
            )

    async def fake_get_artifact(project_id: str, artifact_type: str, format_name: str = "html"):
        if format_name == "pdf":
            return b"%PDF-1.4"
        return "<html/>"

    monkeypatch.setattr(projects_module, "require_pdf_approval", fake_require_pdf_approval)
    monkeypatch.setattr(projects_module.growth_support_service, "get_artifact", fake_get_artifact)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        business_plan_artifact = await client.get("/api/v1/projects/p1/artifacts/business_plan?format=pdf")
        assert business_plan_artifact.status_code == 409
        assert business_plan_artifact.json()["detail"]["error_code"] == "APPROVAL_INCOMPLETE"

        roadmap_artifact = await client.get("/api/v1/projects/p1/artifacts/roadmap?format=pdf")
        assert roadmap_artifact.status_code == 200
        assert roadmap_artifact.headers["content-type"] == "application/pdf"

        assert ("p1", "business_plan") in calls


@pytest.mark.asyncio
async def test_pdf_gate_documents_download_route_blocks_business_plan_only(monkeypatch):
    app = _build_policy_app()
    app.dependency_overrides[get_current_user] = _fake_user
    monkeypatch.setattr(projects_module, "_get_project_or_recover", _fake_get_project_or_recover)

    calls = []

    async def fake_require_pdf_approval(project_id: str, artifact_type: str = "business_plan"):
        calls.append((project_id, artifact_type))
        if artifact_type == "business_plan":
            raise HTTPException(
                status_code=409,
                detail={"error_code": "APPROVAL_INCOMPLETE", "missing_steps": ["summary_confirmed"]},
            )

    async def fake_get_artifact(project_id: str, artifact_type: str, format_name: str = "html"):
        if format_name == "pdf":
            return b"%PDF-1.4"
        return "<html/>"

    monkeypatch.setattr(projects_module, "require_pdf_approval", fake_require_pdf_approval)
    monkeypatch.setattr(projects_module.growth_support_service, "get_artifact", fake_get_artifact)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        business_plan_doc = await client.get("/api/v1/projects/p1/documents/business_plan/download?format=pdf")
        assert business_plan_doc.status_code == 409
        assert business_plan_doc.json()["detail"]["error_code"] == "APPROVAL_INCOMPLETE"

        roadmap_doc = await client.get("/api/v1/projects/p1/documents/roadmap/download?format=pdf")
        assert roadmap_doc.status_code == 200
        assert roadmap_doc.headers["content-type"] == "application/pdf"

        assert ("p1", "roadmap") in calls


@pytest.mark.asyncio
async def test_pdf_gate_service_path_blocks_business_plan_only(monkeypatch):
    async def fake_get_latest(project_id: str):
        return {
            "artifacts": {
                "business_plan": {"html": "<html>BP</html>"},
                "roadmap": {"html": "<html>RP</html>"},
            }
        }

    calls = []

    async def fake_require_pdf_approval(project_id: str, artifact_type: str = "business_plan"):
        calls.append((project_id, artifact_type))
        if artifact_type == "business_plan":
            raise HTTPException(
                status_code=409,
                detail={"error_code": "APPROVAL_INCOMPLETE", "missing_steps": ["summary_confirmed"]},
            )

    monkeypatch.setattr(growth_support_service_module.growth_support_service, "get_latest", fake_get_latest)
    monkeypatch.setattr(growth_support_service_module, "require_pdf_approval", fake_require_pdf_approval)
    monkeypatch.setattr(growth_support_service_module, "render_pdf_from_html", lambda _: b"%PDF-1.4")

    with pytest.raises(HTTPException) as exc_info:
        await growth_support_service_module.growth_support_service.get_artifact("p1", "business_plan", "pdf")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error_code"] == "APPROVAL_INCOMPLETE"
    assert ("p1", "business_plan") in calls

    roadmap_pdf = await growth_support_service_module.growth_support_service.get_artifact("p1", "roadmap", "pdf")
    assert roadmap_pdf == b"%PDF-1.4"
    assert ("p1", "roadmap") in calls


@pytest.mark.asyncio
async def test_question_counter_server_slot_and_limit_enforced(monkeypatch):
    state = _ConversationState()

    async def fake_get_or_create_conversation_state(_project_id: str, policy_version: str = growth_v1_controls.POLICY_VERSION_LEGACY):
        return state

    monkeypatch.setattr(growth_v1_controls, "get_or_create_conversation_state", fake_get_or_create_conversation_state)
    monkeypatch.setattr(growth_v1_controls, "AsyncSessionLocal", lambda: _DummySession())

    # required
    result = await growth_v1_controls.update_question_counters("p1", "필수")
    assert result["allocated_question_type"] == "required"

    # requested unknown falls back to server slot order required->optional->special
    result2 = await growth_v1_controls.update_question_counters("p1", "unknown")
    assert result2["allocated_question_type"] == "optional"

    # special slot
    result3 = await growth_v1_controls.update_question_counters("p1", "특이사항")
    assert result3["allocated_question_type"] == "special"

    # all 3 slots used, next question should hit global limit
    with pytest.raises(HTTPException) as exc_info:
        await growth_v1_controls.update_question_counters("p1", None)
    detail = exc_info.value.detail
    assert exc_info.value.status_code == 409
    assert detail["error_code"] == "QUESTION_LIMIT_REACHED"
    assert detail["counters"]["question_total_count"] == state.question_total_count


@pytest.mark.asyncio
async def test_growth_mode_policy_blocks_strategy_overgeneration():
    roadmap = {
        "yearly_plan": [
            {
                "actions": ["R&D 투자 집중"],
                "goals": ["국내 시장 점유율 확장"],
                "kpis": ["ARR 성장율"],
            }
        ]
    }
    matching = {"items": []}

    with pytest.raises(HTTPException) as exc_info:
        growth_v1_controls.validate_growth_mode_policy(growth_v1_controls.CONSULTATION_MODE_GROWTH, roadmap, matching)

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["error_code"] == "POLICY_VALIDATION_FAILED"
    assert any("roadmap.y1.action:R&D" in item for item in exc_info.value.detail["violations"])


@pytest.mark.asyncio
async def test_growth_mode_policy_allows_kpi_focused_plan():
    roadmap = {
        "yearly_plan": [
            {"actions": ["고객 접점 정비"], "goals": ["마케팅 확대"], "kpis": ["이탈률 3% 이하"]}
        ]
    }
    matching = {"items": []}

    growth_v1_controls.validate_growth_mode_policy(growth_v1_controls.CONSULTATION_MODE_GROWTH, roadmap, matching)


@pytest.mark.asyncio
async def test_pdf_approval_gate_blocks_business_plan_only(monkeypatch):
    async def fake_policy_state(_project_id: str):
        return SimpleNamespace(policy_version=growth_v1_controls.POLICY_VERSION_V1)

    async def fake_approval_state_incomplete(_project_id: str, _artifact_type: str):
        return SimpleNamespace(
            key_figures_approved=False,
            certification_path_approved=False,
            template_selected=False,
            summary_confirmed=False,
        )

    async def fake_approval_state_complete(_project_id: str, _artifact_type: str):
        return SimpleNamespace(
            key_figures_approved=True,
            certification_path_approved=True,
            template_selected=True,
            summary_confirmed=True,
        )

    monkeypatch.setattr(growth_v1_controls, "get_project_policy_state", fake_policy_state)

    # Business plan should be blocked when required approvals are missing.
    monkeypatch.setattr(growth_v1_controls, "get_or_create_approval_state", fake_approval_state_incomplete)
    with pytest.raises(HTTPException) as exc_info:
        await growth_v1_controls.require_pdf_approval("p1", "business_plan")
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error_code"] == "APPROVAL_INCOMPLETE"

    # Roadmap should bypass approval gate regardless of approval flags.
    await growth_v1_controls.require_pdf_approval("p1", "roadmap")

    # All steps approved => business_plan allowed.
    monkeypatch.setattr(growth_v1_controls, "get_or_create_approval_state", fake_approval_state_complete)
    await growth_v1_controls.require_pdf_approval("p1", "business_plan")
