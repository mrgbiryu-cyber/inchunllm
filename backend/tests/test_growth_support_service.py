import pytest

from app.core.database import init_db
from app.models.company import CompanyProfile
from app.services.growth_v1_controls import POLICY_VERSION_V1, set_project_policy_version
from app.services.growth_support_service import GrowthSupportService


@pytest.mark.asyncio
async def test_growth_support_pipeline_returns_artifacts():
    await init_db()
    await set_project_policy_version("p-test", POLICY_VERSION_V1, consultation_mode="예비")
    service = GrowthSupportService()
    profile = CompanyProfile(
        company_name="T",
        years_in_business=0,
        annual_revenue=0,
        employee_count=1,
        item_description="AI workflow",
        has_corporation=False,
    )

    result = await service.run_pipeline("p-test", profile, input_text="existing draft")

    assert "classification" in result
    assert "business_plan" in result
    assert "matching" in result
    assert "roadmap" in result
    assert "artifacts" in result
    assert "html" in result["artifacts"]["business_plan"]
    assert "markdown" in result["artifacts"]["roadmap"]

    service.artifact_cache.clear()
    latest = await service.get_latest("p-test")
    assert latest is not None
    assert latest["project_id"] == "p-test"
