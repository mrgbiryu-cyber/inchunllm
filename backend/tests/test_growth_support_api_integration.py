import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from app.api.dependencies import get_current_user
from app.api.v1 import projects as projects_module
from app.models.schemas import User, UserRole


@pytest.mark.asyncio
async def test_growth_support_api_e2e(monkeypatch):
    app = FastAPI()
    app.include_router(projects_module.router, prefix="/api/v1/projects")

    async def fake_user():
        return User(
            id="u1",
            username="admin",
            tenant_id="tenant_hyungnim",
            role=UserRole.SUPER_ADMIN,
            is_active=True,
        )

    async def fake_get_project_or_recover(project_id: str, current_user: User):
        return {
            "id": project_id,
            "name": "P",
            "tenant_id": current_user.tenant_id,
            "user_id": current_user.id,
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }

    monkeypatch.setattr(projects_module, "_get_project_or_recover", fake_get_project_or_recover)
    async def fake_run_pipeline(project_id: str, profile, input_text: str = "", research_request=None):
        return {
            "project_id": project_id,
            "classification": {"value": "STARTUP"},
            "business_plan": {"title": "BP"},
            "matching": {"items": []},
            "roadmap": {"yearly_plan": []},
            "artifacts": {
                "business_plan": {"html": "<html><body>business</body></html>", "markdown": "# business"},
                "matching": {"html": "<html><body>matching</body></html>", "markdown": "- matching"},
                "roadmap": {"html": "<html><body>roadmap</body></html>", "markdown": "- roadmap"},
            },
        }

    async def fake_get_latest(project_id: str):
        return {
            "project_id": project_id,
            "classification": {"value": "STARTUP"},
            "business_plan": {"title": "BP"},
            "matching": {"items": []},
            "roadmap": {"yearly_plan": []},
            "artifacts": {
                "business_plan": {
                    "html": "<html><body>business</body></html>",
                    "markdown": "# business",
                }
            },
        }

    async def fake_get_artifact(project_id: str, artifact_type: str, format_name: str = "html"):
        if artifact_type != "business_plan":
            raise KeyError(f"Artifact not found: {artifact_type}")
        if format_name == "html":
            return "<html><body>business</body></html>"
        if format_name == "markdown":
            return "# business"
        if format_name == "pdf":
            return b"pdf-bytes"
        raise KeyError(f"Format not found: {format_name}")

    async def fake_require_pdf_approval(project_id: str, artifact_type: str = "business_plan"):
        return None

    monkeypatch.setattr(projects_module.growth_support_service, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(projects_module.growth_support_service, "get_latest", fake_get_latest)
    monkeypatch.setattr(projects_module.growth_support_service, "get_artifact", fake_get_artifact)
    monkeypatch.setattr(projects_module, "require_pdf_approval", fake_require_pdf_approval)
    app.dependency_overrides[get_current_user] = fake_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        run_res = await client.post(
            "/api/v1/projects/p1/growth-support/run",
            json={
                "profile": {
                    "company_name": "Sample",
                    "years_in_business": 0,
                    "annual_revenue": 0,
                    "employee_count": 1,
                    "item_description": "AI assistant",
                    "has_corporation": False,
                },
                "input_text": "existing draft",
            },
        )
        assert run_res.status_code == 200
        payload = run_res.json()
        assert "business_plan" in payload
        assert "matching" in payload
        assert "roadmap" in payload

        latest_res = await client.get("/api/v1/projects/p1/growth-support/latest")
        assert latest_res.status_code == 200

        artifact_res = await client.get("/api/v1/projects/p1/artifacts/business_plan?format=html")
        assert artifact_res.status_code == 200
        assert "text/html" in artifact_res.headers.get("content-type", "")
