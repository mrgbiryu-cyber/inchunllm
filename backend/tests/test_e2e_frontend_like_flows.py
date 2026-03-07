import os
import uuid
from types import SimpleNamespace

import pytest
from app.services.rules import RulesetRepository
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from app.api.dependencies import get_current_user
from app.api.v1 import admin as admin_module
from app.api.v1 import files as files_module
from app.api.v1 import projects as projects_module
from app.models.schemas import RuleSet, RuleStatus, User, UserRole
from app.models.company import CompanyProfile


@pytest.mark.asyncio
async def test_admin_ruleset_e2e_flow(monkeypatch):
    temp_dir = os.path.join("backend", "data", f"rulesets_test_api_flow_{uuid.uuid4().hex}")
    repo = RulesetRepository(os.path.join(temp_dir, "rulesets"))
    repo.create(
        RuleSet(
            ruleset_id="company-growth-default",
            version="v1",
            status=RuleStatus.ACTIVE,
            company_type_rules=[],
            growth_stage_rules=[],
            matching_rules=[],
        )
    )

    monkeypatch.setattr(admin_module, "ruleset_repository", repo)

    async def fake_user():
        return User(
            id="u-admin",
            username="admin",
            tenant_id="tenant-1",
            role=UserRole.SUPER_ADMIN,
            is_active=True,
        )

    app = FastAPI()
    app.include_router(admin_module.router, prefix="/api/v1/admin")
    app.dependency_overrides[get_current_user] = fake_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        list_res = await client.get("/api/v1/admin/rulesets?ruleset_id=company-growth-default")
        assert list_res.status_code == 200
        versions = list_res.json()
        assert len(versions) == 1
        assert versions[0]["version"] == "v1"

        clone_res = await client.post(
            "/api/v1/admin/rulesets/company-growth-default/v1/clone",
            json={"version": "v1.1"},
        )
        assert clone_res.status_code == 200
        assert clone_res.json()["version"] == "v1.1"

        activate_res = await client.post("/api/v1/admin/rulesets/company-growth-default/v1.1/activate")
        assert activate_res.status_code == 200
        assert activate_res.json()["status"] == "active"

        preview_res = await client.post(
            "/api/v1/admin/rulesets/company-growth-default/v1.1/preview",
            json={
                "profile": {
                    "company_name": "Acme",
                    "item_description": "AI assistant",
                    "years_in_business": 0,
                    "annual_revenue": 0,
                    "employee_count": 1,
                    "has_corporation": False,
                }
            },
        )
        assert preview_res.status_code == 200
        payload = preview_res.json()
        assert "company_type" in payload
        assert "growth_stage" in payload


@pytest.mark.asyncio
async def test_growth_support_e2e_flow(monkeypatch):
    async def fake_get_project_or_recover(project_id: str, current_user: User):
        return {
            "id": project_id,
            "name": "Sample",
            "tenant_id": current_user.tenant_id,
            "user_id": current_user.id,
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }

    async def fake_run_pipeline(
        project_id: str,
        profile: CompanyProfile,
        input_text: str = "",
        research_request=None,
    ):
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
                    "pdf": b"pdf-binary",
                }
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
                    "html": "<html><body>latest-business</body></html>",
                    "markdown": "# latest business",
                    "pdf": b"pdf-binary-latest",
                }
            },
        }

    async def fake_get_artifact(project_id: str, artifact_type: str, format_name: str = "html"):
        if artifact_type != "business_plan":
            raise KeyError(f"Artifact not found: {artifact_type}")
        if format_name == "html":
            return "<html><body>artifact-html</body></html>"
        if format_name == "markdown":
            return "# artifact-md"
        if format_name == "pdf":
            return b"artifact-pdf"
        raise KeyError(f"Format not found: {format_name}")

    monkeypatch.setattr(projects_module, "_get_project_or_recover", fake_get_project_or_recover)
    monkeypatch.setattr(projects_module.growth_support_service, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(projects_module.growth_support_service, "get_latest", fake_get_latest)
    monkeypatch.setattr(projects_module.growth_support_service, "get_artifact", fake_get_artifact)

    async def fake_user():
        return User(
            id="u-founder",
            username="founder",
            tenant_id="tenant-1",
            role=UserRole.STANDARD_USER,
            is_active=True,
        )

    app = FastAPI()
    app.include_router(projects_module.router, prefix="/api/v1/projects")
    app.dependency_overrides[get_current_user] = fake_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        run_res = await client.post(
            "/api/v1/projects/p-founder/growth-support/run",
            json={
                "profile": {
                    "company_name": "Acme",
                    "item_description": "AI workflow",
                    "years_in_business": 2,
                    "annual_revenue": 100,
                    "employee_count": 5,
                    "has_corporation": False,
                },
                "input_text": "existing draft",
            },
        )
        assert run_res.status_code == 200
        run_payload = run_res.json()
        assert run_payload["project_id"] == "p-founder"

        latest_res = await client.get("/api/v1/projects/p-founder/growth-support/latest")
        assert latest_res.status_code == 200
        assert latest_res.json()["classification"]["value"] == "STARTUP"

        artifact_html = await client.get(
            "/api/v1/projects/p-founder/artifacts/business_plan?format=html"
        )
        assert artifact_html.status_code == 200
        assert artifact_html.headers["content-type"].startswith("text/html")

        artifact_md = await client.get(
            "/api/v1/projects/p-founder/artifacts/business_plan?format=markdown"
        )
        assert artifact_md.status_code == 200
        assert artifact_md.text == "# artifact-md"


@pytest.mark.asyncio
async def test_upload_flow_extension_and_dedupe(monkeypatch):
    app = FastAPI()
    app.include_router(files_module.router, prefix="/api/v1")

    async def fake_user():
        return User(
            id="u-support",
            username="support",
            tenant_id="tenant-1",
            role=UserRole.STANDARD_USER,
            is_active=True,
        )

    class DummySession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def add(self, *_):
            return None

        async def commit(self):
            return None

    call_state = SimpleNamespace(count=0)

    class FakeDuplicateMessage:
        message_id = "dup-msg-id"

    async def fake_check_duplicate_file(_session, _project_id, _file_hash):
        call_state.count += 1
        if call_state.count >= 2:
            return FakeDuplicateMessage()
        return None

    class DummySessionFactory:
        def __call__(self):
            return DummySession()

    monkeypatch.setattr(files_module, "AsyncSessionLocal", DummySessionFactory())
    monkeypatch.setattr(files_module, "check_duplicate_file", fake_check_duplicate_file)

    app.dependency_overrides[get_current_user] = fake_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        unsupported = await client.post(
            "/api/v1/files/upload",
            files={"file": ("bad.zzz", b"abc", "text/plain")},
        )
        assert unsupported.status_code == 400
        unsupported_payload = unsupported.json()
        assert unsupported_payload["detail"].startswith("UNSUPPORTED_EXTENSION")

        text = str(uuid.uuid4()).encode("utf-8")
        first = await client.post(
            "/api/v1/files/upload",
            files={"file": ("business.txt", text, "text/plain")},
        )
        assert first.status_code == 200
        first_json = first.json()
        assert first_json["status"] == "queued"

        second = await client.post(
            "/api/v1/files/upload",
            files={"file": ("business.txt", text, "text/plain")},
        )
        assert second.status_code == 200
        second_json = second.json()
        assert second_json["status"] == "skipped"
        assert second_json["reason"] == "duplicate"


@pytest.mark.asyncio
async def test_upload_folder_and_batch_status_harmonization(monkeypatch):
    app = FastAPI()
    app.include_router(files_module.router, prefix="/api/v1")

    async def fake_user():
        return User(
            id="u-support",
            username="support",
            tenant_id="tenant-1",
            role=UserRole.STANDARD_USER,
            is_active=True,
        )

    class DummySession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def add(self, *_):
            return None

        async def commit(self):
            return None

    seen_hashes = set()
    class FakeDuplicateMessage:
        message_id = "dup-msg-id"

    async def fake_check_duplicate_file(_session, _project_id, file_hash):
        if file_hash in seen_hashes:
            return FakeDuplicateMessage()
        seen_hashes.add(file_hash)
        return None

    class DummySessionFactory:
        def __call__(self):
            return DummySession()

    monkeypatch.setattr(files_module, "AsyncSessionLocal", DummySessionFactory())
    monkeypatch.setattr(files_module, "check_duplicate_file", fake_check_duplicate_file)
    monkeypatch.setattr(files_module.settings, "MAX_FILE_SIZE_BYTES", 8)
    app.dependency_overrides[get_current_user] = fake_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        folder_res = await client.post(
            "/api/v1/files/upload-folder",
            files=[
                ("files", ("a.txt", b"ok", "text/plain")),
                ("files", ("bad.zzz", b"no", "text/plain")),
                ("files", ("a-dup.txt", b"ok", "text/plain")),
                ("files", ("big.txt", b"123456789", "text/plain")),
            ],
        )
        assert folder_res.status_code == 200
        folder_payload = folder_res.json()
        assert folder_payload["total"] == 4
        assert folder_payload["results"][0]["status"] == "queued"
        assert folder_payload["results"][0]["filename"] == "a.txt"
        assert folder_payload["results"][1]["status"] == "failed"
        assert folder_payload["results"][1]["reason"] == "unsupported_type"
        assert folder_payload["results"][1]["detail"].startswith("UNSUPPORTED_EXTENSION")
        assert folder_payload["results"][2]["status"] == "skipped"
        assert folder_payload["results"][2]["reason"] == "duplicate"
        assert folder_payload["results"][3]["status"] == "failed"
        assert folder_payload["results"][3]["reason"] == "too_large"
        assert folder_payload["results"][3]["detail"].startswith("FILE_TOO_LARGE")

        batch_res = await client.post(
            "/api/v1/files/upload-batch",
            files=[
                ("files", ("b.txt", b"ok2", "text/plain")),
            ],
        )
        assert batch_res.status_code == 200
        batch_payload = batch_res.json()
        assert batch_payload["results"][0]["status"] == "queued"


@pytest.mark.asyncio
async def test_upload_parser_fallback_returns_saved_only(monkeypatch):
    app = FastAPI()
    app.include_router(files_module.router, prefix="/api/v1")

    async def fake_user():
        return User(
            id="u-support",
            username="support",
            tenant_id="tenant-1",
            role=UserRole.STANDARD_USER,
            is_active=True,
        )

    class DummySession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def add(self, *_):
            return None

        async def commit(self):
            return None

    class DummySessionFactory:
        def __call__(self):
            return DummySession()

    async def fake_check_duplicate_file(_session, _project_id, _file_hash):
        return None

    def fake_parse_file(*_args, **_kwargs):
        raise Exception("temporary parser failure")

    monkeypatch.setattr(files_module, "AsyncSessionLocal", DummySessionFactory())
    monkeypatch.setattr(files_module, "check_duplicate_file", fake_check_duplicate_file)
    monkeypatch.setattr(files_module.document_parser_service, "_parse_file", fake_parse_file)
    app.dependency_overrides[get_current_user] = fake_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        fallback_res = await client.post(
            "/api/v1/files/upload",
            files={"file": ("fallback.txt", b"payload", "text/plain")},
        )
        assert fallback_res.status_code == 200
        payload = fallback_res.json()
        assert payload["status"] == "saved_only"
        assert payload["reason"] == "parser_fallback"


def _build_ruleset(repo_dir: str, version: str, company_type: str) -> RuleSet:
    return RuleSet(
        ruleset_id="company-growth-default",
        version=version,
        status=RuleStatus.ACTIVE if version == "v1" else RuleStatus.DRAFT,
        company_type_rules=[
            {
                "rule_id": f"ct-{version}",
                "name": f"company type {version}",
                "conditions": [
                    {"field": "has_corporation", "op": "eq", "value": False}
                ],
                "actions": [
                    {
                        "target": "company_type",
                        "value": company_type,
                        "score": 1.0,
                        "reason_code": f"CT_{version.upper()}",
                    }
                ],
            }
        ],
        growth_stage_rules=[
            {
                "rule_id": f"gs-{version}",
                "name": f"growth stage {version}",
                "conditions": [
                    {"field": "years_in_business", "op": "lt", "value": 1},
                ],
                "actions": [
                    {
                        "target": "growth_stage",
                        "value": "STARTUP",
                        "score": 1.0,
                        "reason_code": f"GS_{version.upper()}",
                    }
                ],
            }
        ],
        matching_rules=[],
        weights={"company_type": 1.0, "growth_stage": 1.0},
        cutoffs={"minimum_confidence": 0.5},
        fallback_policy={"default_confidence": 0.4, "fallback_on_low_confidence": True},
    )


@pytest.mark.asyncio
async def test_frontend_like_flow_founder_with_full_artifacts(monkeypatch):
    app = FastAPI()
    app.include_router(projects_module.router, prefix="/api/v1/projects")

    async def fake_user():
        return User(
            id="u-founder",
            username="founder",
            tenant_id="tenant-1",
            role=UserRole.STANDARD_USER,
            is_active=True,
        )

    async def fake_get_project_or_recover(project_id: str, current_user: User):
        return {
            "id": project_id,
            "name": "Founder Workspace",
            "tenant_id": current_user.tenant_id,
            "user_id": current_user.id,
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }

    run_counter = {"count": 0}
    latest_payload = {}

    async def fake_run_pipeline(
        project_id: str,
        profile: CompanyProfile,
        input_text: str = "",
        research_request=None,
    ):
        run_counter["count"] += 1
        n = run_counter["count"]
        payload = {
            "project_id": project_id,
            "classification": {"value": "STARTUP"},
            "business_plan": {"title": f"BP_{n}"},
            "matching": {"items": []},
            "roadmap": {"yearly_plan": [{"year": "Y1", "items": []}]},
            "artifacts": {
                "business_plan": {
                    "html": f"<html><body>business-{n}</body></html>",
                    "markdown": f"# business-{n}",
                    "pdf": b"pdf-bytes",
                },
                "matching": {
                    "html": f"<html><body>matching-{n}</body></html>",
                    "markdown": "- matching",
                },
                "roadmap": {
                    "html": f"<html><body>roadmap-{n}</body></html>",
                    "markdown": "- roadmap",
                },
            },
        }
        latest_payload.update({"payload": payload})
        return payload

    async def fake_get_latest(project_id: str):
        if not latest_payload:
            return {
                "project_id": project_id,
                "classification": {"value": "STARTUP"},
                "business_plan": {"title": "BP_0"},
                "matching": {"items": []},
                "roadmap": {"yearly_plan": [{"year": "Y1", "items": []}]},
                "artifacts": {
                    "business_plan": {
                        "html": "<html><body>business-0</body></html>",
                        "markdown": "# business-0",
                        "pdf": b"pdf-bytes",
                    }
                },
            }
        return latest_payload.get("payload")

    async def fake_get_artifact(project_id: str, artifact_type: str, format_name: str = "html"):
        if artifact_type not in {"business_plan", "matching", "roadmap"}:
            raise KeyError(f"Artifact not found: {artifact_type}")
        if format_name == "html":
            return f"<html><body>{artifact_type}</body></html>"
        if format_name == "markdown":
            return f"# {artifact_type}"
            if format_name == "pdf":
                return b"pdf-bytes"
            raise KeyError(f"Format not found: {format_name}")

    async def fake_require_pdf_approval(project_id: str, artifact_type: str = "business_plan"):
        return None

    monkeypatch.setattr(projects_module, "_get_project_or_recover", fake_get_project_or_recover)
    monkeypatch.setattr(projects_module.growth_support_service, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(projects_module.growth_support_service, "get_latest", fake_get_latest)
    monkeypatch.setattr(projects_module.growth_support_service, "get_artifact", fake_get_artifact)
    monkeypatch.setattr(projects_module, "require_pdf_approval", fake_require_pdf_approval)

    app.dependency_overrides[get_current_user] = fake_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first_res = await client.post(
            "/api/v1/projects/f1/growth-support/run",
            json={
                "profile": {
                    "company_name": "Sample Co",
                    "item_description": "AI workflow",
                    "years_in_business": 0,
                    "annual_revenue": 100,
                    "employee_count": 2,
                    "has_corporation": False,
                },
                "input_text": "existing draft",
            },
        )
        assert first_res.status_code == 200
        assert first_res.json()["artifacts"]["business_plan"]["markdown"] == "# business-1"

        second_res = await client.post(
            "/api/v1/projects/f1/growth-support/run",
            json={
                "profile": {
                    "company_name": "Sample Co",
                    "item_description": "AI workflow",
                    "years_in_business": 0,
                    "annual_revenue": 100,
                    "employee_count": 2,
                    "has_corporation": False,
                },
            },
        )
        assert second_res.status_code == 200
        assert second_res.json()["artifacts"]["business_plan"]["markdown"] == "# business-2"

        latest_res = await client.get("/api/v1/projects/f1/growth-support/latest")
        assert latest_res.status_code == 200
        assert latest_res.json()["business_plan"]["title"] == "BP_2"

        artifact_html = await client.get("/api/v1/projects/f1/artifacts/business_plan?format=html")
        assert artifact_html.status_code == 200
        assert artifact_html.text.startswith("<html")

        artifact_md = await client.get("/api/v1/projects/f1/artifacts/business_plan?format=markdown")
        assert artifact_md.status_code == 200
        assert artifact_md.text == "# business_plan"

        artifact_pdf = await client.get("/api/v1/projects/f1/artifacts/business_plan?format=pdf")
        assert artifact_pdf.status_code == 200
        assert artifact_pdf.headers["content-type"] == "application/pdf"


@pytest.mark.asyncio
async def test_admin_ruleset_activation_reflects_runtime_classification(monkeypatch):
    app = FastAPI()
    app.include_router(admin_module.router, prefix="/api/v1/admin")
    app.include_router(projects_module.router, prefix="/api/v1/projects")

    repo_dir = os.path.join("backend", "data", f"rulesets_test_admin_activate_{uuid.uuid4().hex}")
    ruleset_repo = RulesetRepository(repo_dir)
    ruleset_repo.create(_build_ruleset(repo_dir, "v1", "PRE_ENTREPRENEUR"))
    ruleset_repo.create(_build_ruleset(repo_dir, "v1.1", "GROWTH_STAGE"))

    monkeypatch.setattr(admin_module, "ruleset_repository", ruleset_repo)
    from app.services.agents import classification_agent
    monkeypatch.setattr(classification_agent, "ruleset_repository", ruleset_repo)
    from app.services import rules as rules_module
    monkeypatch.setattr(rules_module, "ruleset_repository", ruleset_repo)

    async def fake_user():
        return User(
            id="u-admin",
            username="admin",
            tenant_id="tenant-1",
            role=UserRole.SUPER_ADMIN,
            is_active=True,
        )

    async def fake_get_project_or_recover(project_id: str, current_user: User):
        return {
            "id": project_id,
            "name": "Admin Workspace",
            "tenant_id": current_user.tenant_id,
            "user_id": current_user.id,
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }

    async def fake_run_pipeline(
        project_id: str,
        profile: CompanyProfile,
        input_text: str = "",
        research_request=None,
    ):
        classification = await classification_agent.ClassificationAgent().analyze(profile)
        return {
            "project_id": project_id,
            "classification": classification,
            "business_plan": {"title": "BP"},
            "matching": {"items": []},
            "roadmap": {"yearly_plan": []},
            "artifacts": {
                "business_plan": {"html": "<html/>", "markdown": "# bp", "pdf": b"pdf"},
                "matching": {"html": "<html/>", "markdown": "# m"},
                "roadmap": {"html": "<html/>", "markdown": "# r"},
            },
        }

    monkeypatch.setattr(projects_module, "_get_project_or_recover", fake_get_project_or_recover)
    monkeypatch.setattr(projects_module.growth_support_service, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(
        projects_module.growth_support_service, "get_latest",
        lambda project_id: {
            "project_id": project_id,
            "classification": {"value": "STARTUP", "ruleset_version": "DUMMY"},
            "business_plan": {},
            "matching": {},
            "roadmap": {},
            "artifacts": {},
        }
    )
    monkeypatch.setattr(
        projects_module.growth_support_service,
        "get_artifact",
        lambda project_id, artifact_type, format_name="html": "<html/>",
    )

    app.dependency_overrides[get_current_user] = fake_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        activate_res = await client.post(
            "/api/v1/admin/rulesets/company-growth-default/v1.1/activate"
        )
        assert activate_res.status_code == 200
        assert activate_res.json()["version"] == "v1.1"
        assert activate_res.json()["status"] == "active"

        run_res = await client.post(
            "/api/v1/projects/f-admin/growth-support/run",
            json={
                "profile": {
                    "company_name": "Admin Test",
                    "item_description": "policy run",
                    "years_in_business": 0,
                    "annual_revenue": 0,
                    "employee_count": 1,
                    "has_corporation": False,
                }
            },
        )
        assert run_res.status_code == 200
        data = run_res.json()
        assert data["classification"]["ruleset_version"] == "v1.1"


@pytest.mark.asyncio
async def test_admin_ruleset_api_create_update_activate_validation(monkeypatch):
    async def fake_user():
        return User(
            id="u-admin",
            username="admin",
            tenant_id="tenant-1",
            role=UserRole.SUPER_ADMIN,
            is_active=True,
        )

    repo_dir = os.path.join("backend", "data", f"rulesets_test_admin_api_{uuid.uuid4().hex}")
    repo = RulesetRepository(repo_dir)
    repo.create(
        RuleSet(
            ruleset_id="company-growth-default",
            version="v1",
            status=RuleStatus.DRAFT,
            company_type_rules=[],
            growth_stage_rules=[],
            matching_rules=[],
        )
    )

    app = FastAPI()
    app.include_router(admin_module.router, prefix="/api/v1/admin")
    app.dependency_overrides[get_current_user] = fake_user

    monkeypatch.setattr(admin_module, "ruleset_repository", repo)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        list_res = await client.get("/api/v1/admin/rulesets?ruleset_id=company-growth-default")
        assert list_res.status_code == 200
        assert len(list_res.json()) == 1

        dup_payload = list_res.json()[0]
        dup_res = await client.post("/api/v1/admin/rulesets", json=dup_payload)
        assert dup_res.status_code == 409

        # create new draft from existing seed
        create_payload = {
            **dup_payload,
            "version": "v1.1",
            "status": "draft",
            "author": "admin",
        }
        create_res = await client.post("/api/v1/admin/rulesets", json=create_payload)
        assert create_res.status_code == 201

        mismatch_payload = {
            **create_payload,
            "ruleset_id": "other-id",
        }
        mismatch_res = await client.patch(
            "/api/v1/admin/rulesets/company-growth-default/v1.1",
            json=mismatch_payload
        )
        assert mismatch_res.status_code == 400

        update_payload = {
            **create_payload,
            "ruleset_id": "company-growth-default",
            "version": "v1.1",
        }
        update_res = await client.patch(
            "/api/v1/admin/rulesets/company-growth-default/v1.1",
            json=update_payload
        )
        assert update_res.status_code == 200
        assert update_res.json()["version"] == "v1.1"

        activate_res = await client.post("/api/v1/admin/rulesets/company-growth-default/v1.1/activate")
        assert activate_res.status_code == 200
        assert activate_res.json()["status"] == "active"

        active_res = await client.get("/api/v1/admin/rulesets/company-growth-default/active")
        assert active_res.status_code == 200
        assert active_res.json()["version"] == "v1.1"
