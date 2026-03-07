import pytest
from fastapi import FastAPI, HTTPException
from httpx import AsyncClient, ASGITransport
from types import SimpleNamespace

from app.api.dependencies import get_current_user
from app.api.v1 import projects as projects_module
from app.models.schemas import User, UserRole
from app.services import growth_v1_controls


async def _admin_user():
    return User(
        id="u-admin",
        username="admin",
        tenant_id="tenant-1",
        role=UserRole.SUPER_ADMIN,
        is_active=True,
    )


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def add(self, *_):
        return None

    async def merge(self, *_):
        return None

    async def commit(self):
        return None


class _FakeNeo4j:
    def __init__(self):
        self._projects = {}

    async def create_project_graph(self, project):
        self._projects[str(project.id)] = project

    async def get_project(self, project_id: str):
        return self._projects.get(project_id)

    async def list_projects(self, tenant_id: str, project_ids=None):
        if not project_ids:
            return [vars(v) for v in self._projects.values()]
        return [vars(self._projects[pid]) for pid in project_ids if pid in self._projects]


def _build_app():
    app = FastAPI()
    app.include_router(projects_module.router, prefix="/api/v1/projects")
    app.dependency_overrides[get_current_user] = _admin_user
    return app


@pytest.mark.asyncio
async def test_e2e_v10_new_project_normal_flow(monkeypatch):
    fake_neo4j = _FakeNeo4j()
    policy_calls = []

    async def fake_set_project_policy_version(project_id: str, policy_version: str, consultation_mode: str | None = None):
        policy_calls.append((project_id, policy_version, consultation_mode))
        return SimpleNamespace(
            project_id=project_id,
            policy_version=policy_version,
            consultation_mode=consultation_mode or "예비",
            profile_stage=consultation_mode or "예비",
            question_required_count=0,
            question_optional_count=0,
            question_special_count=0,
        )

    async def fake_get_project_or_recover(project_id: str, current_user: User):
        return {
            "id": project_id,
            "name": "샘플 프로젝트",
            "tenant_id": current_user.tenant_id,
            "user_id": current_user.id,
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }

    async def fake_run_pipeline(project_id, profile, input_text="", research_request=None):
        return {
            "project_id": project_id,
            "classification": {"value": "STARTUP"},
            "business_plan": {"title": "BP"},
            "matching": {"items": []},
            "roadmap": {"yearly_plan": []},
            "artifacts": {
                "business_plan": {
                    "html": "<html><body>BP</body></html>",
                    "markdown": "# BP",
                }
            },
        }

    async def fake_get_artifact(project_id: str, artifact_type: str, format_name: str = "html"):
        if artifact_type != "business_plan":
            raise KeyError(f"Artifact not found: {artifact_type}")
        if format_name == "html":
            return "<html><body>business</body></html>"
        if format_name == "markdown":
            return "# markdown"
        if format_name == "pdf":
            return b"%PDF-1.4"
        raise KeyError(f"Format not found: {format_name}")

    class _FakeGrowthSupportService:
        async def run_pipeline(self, project_id, profile, input_text="", research_request=None):
            return await fake_run_pipeline(project_id, profile, input_text, research_request)

        async def get_artifact(self, project_id: str, artifact_type: str, format_name: str = "html"):
            return await fake_get_artifact(project_id, artifact_type, format_name)

        async def get_latest(self, project_id: str):
            return {
                "artifacts": {
                    "business_plan": {
                        "html": "<html><body>business</body></html>",
                        "markdown": "# business",
                    }
                }
            }

    monkeypatch.setattr(projects_module, "neo4j_client", fake_neo4j)
    monkeypatch.setattr(projects_module, "set_project_policy_version", fake_set_project_policy_version)
    monkeypatch.setattr(projects_module, "AsyncSessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(projects_module, "_get_project_or_recover", fake_get_project_or_recover)
    monkeypatch.setattr(projects_module, "growth_support_service", _FakeGrowthSupportService())

    app = _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create_res = await client.post(
            "/api/v1/projects/",
            json={
                "name": "v1 신규 프로젝트",
                "description": "통합 테스트",
                "project_type": "GROWTH_SUPPORT",
            },
        )
        assert create_res.status_code == 201
        project_id = create_res.json()["id"]
        assert any(call[1] == growth_v1_controls.POLICY_VERSION_V1 for call in policy_calls)

        # growth-support 실행
        run_res = await client.post(
            f"/api/v1/projects/{project_id}/growth-support/run",
            json={
                "profile": {
                    "company_name": "Acme",
                    "item_description": "AI assistant",
                    "years_in_business": 0,
                    "annual_revenue": 100,
                    "employee_count": 1,
                    "has_corporation": False,
                },
                "input_text": "first run",
            },
        )
        assert run_res.status_code == 200
        run_payload = run_res.json()
        assert "business_plan" in run_payload

        # 아티팩트 조회 (PDF는 approval 테스트에서 별도 검증)
        artifact_res = await client.get(f"/api/v1/projects/{project_id}/artifacts/business_plan?format=html")
        assert artifact_res.status_code == 200
        assert "text/html" in artifact_res.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_e2e_v10_business_plan_pdf_blocked(monkeypatch):
    project_id = "proj-policy-deny"
    calls = []

    async def fake_get_project_or_recover(_project_id: str, current_user: User):
        return {
            "id": _project_id,
            "name": "P",
            "tenant_id": current_user.tenant_id,
            "user_id": current_user.id,
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }

    async def fake_require_pdf_approval(project_id: str, artifact_type: str = "business_plan"):
        calls.append((project_id, artifact_type))
        if artifact_type == "business_plan":
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "APPROVAL_INCOMPLETE",
                    "missing_steps": ["summary_confirmed"],
                },
            )

    async def fake_get_artifact(_project_id: str, artifact_type: str, format_name: str = "html"):
        if format_name == "pdf":
            return b"%PDF-1.4"
        return "<html/>"

    monkeypatch.setattr(projects_module, "_get_project_or_recover", fake_get_project_or_recover)
    monkeypatch.setattr(projects_module, "require_pdf_approval", fake_require_pdf_approval)
    monkeypatch.setattr(projects_module.growth_support_service, "get_artifact", fake_get_artifact)
    monkeypatch.setattr(projects_module.growth_support_service, "get_latest", lambda _: {"artifacts": {"business_plan": {"html": "<html/>", "markdown": "#"}}})

    app = _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 차단 대상: business_plan만
        bp = await client.get(f"/api/v1/projects/{project_id}/artifacts/business_plan?format=pdf")
        assert bp.status_code == 409
        assert bp.json()["detail"]["error_code"] == "APPROVAL_INCOMPLETE"

        roadmap = await client.get(f"/api/v1/projects/{project_id}/artifacts/roadmap?format=pdf")
        assert roadmap.status_code == 200
        assert roadmap.headers["content-type"] == "application/pdf"

        # documents/download 경로도 동일 규칙 적용
        doc_bp = await client.get(f"/api/v1/projects/{project_id}/documents/business_plan/download?format=pdf")
        assert doc_bp.status_code == 409

        doc_roadmap = await client.get(
            f"/api/v1/projects/{project_id}/documents/roadmap/download?format=pdf"
        )
        assert doc_roadmap.status_code == 200
        assert doc_roadmap.headers["content-type"] == "application/pdf"

        assert ("proj-policy-deny", "business_plan") in calls


@pytest.mark.asyncio
async def test_e2e_legacy_project_keeps_existing_behavior(monkeypatch):
    project_id = "proj-legacy"
    state = SimpleNamespace(
        project_id=project_id,
        policy_version=growth_v1_controls.POLICY_VERSION_LEGACY,
        consultation_mode="legacy",
        profile_stage="legacy",
        question_required_count=0,
        question_optional_count=0,
        question_special_count=0,
        question_total_count=0,
        question_required_limit=0,
        question_optional_limit=0,
        question_special_limit=0,
    )

    async def fake_get_project_or_recover(_project_id: str, current_user: User):
        return {
            "id": _project_id,
            "name": "legacy",
            "tenant_id": current_user.tenant_id,
            "user_id": current_user.id,
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }

    async def fake_get_or_create(project_id: str, policy_version: str = growth_v1_controls.POLICY_VERSION_LEGACY):
        return state

    async def fake_get_artifact(_project_id: str, artifact_type: str, format_name: str = "html"):
        if format_name == "pdf":
            return b"%PDF-1.4"
        return "<html/>"

    async def fake_require_pdf_approval(project_id: str, artifact_type: str = "business_plan"):
        return None

    monkeypatch.setattr(projects_module, "_get_project_or_recover", fake_get_project_or_recover)
    monkeypatch.setattr(projects_module.growth_support_service, "get_artifact", fake_get_artifact)
    monkeypatch.setattr(projects_module, "require_pdf_approval", fake_require_pdf_approval)
    monkeypatch.setattr(growth_v1_controls, "get_or_create_conversation_state", fake_get_or_create)

    app = _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        alloc_res = await client.post(
            f"/api/v1/projects/{project_id}/growth-support/questions/allocate",
            json={"question_type": "필수"},
        )
        assert alloc_res.status_code == 200
        assert alloc_res.json()["allocated_question_type"] == "필수"

        legacy_pdf = await client.get(f"/api/v1/projects/{project_id}/artifacts/business_plan?format=pdf")
        assert legacy_pdf.status_code == 200
        assert legacy_pdf.headers["content-type"] == "application/pdf"
