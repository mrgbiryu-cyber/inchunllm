import asyncio
import copy

import pytest

from app.core.database import ConversationStateModel, ArtifactApprovalStateModel
from app.services import growth_v1_controls


class _MemorySession:
    """in-memory async session mock used only for count/lock 테스트."""

    def __init__(self, persist_fn):
        self._persist_fn = persist_fn
        self._pending = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def merge(self, obj):
        self._pending = copy.deepcopy(obj)

    async def commit(self):
        # 동시성 경합 상황을 재현하기 위해 yield point 추가
        await asyncio.sleep(0)
        if self._pending is not None:
            self._persist_fn(self._pending)


def _clone_state_to_dict(state):
    return {
        "project_id": state.project_id,
        "policy_version": state.policy_version,
        "consultation_mode": state.consultation_mode,
        "profile_stage": state.profile_stage,
        "question_mode": state.question_mode,
        "question_required_count": state.question_required_count,
        "question_optional_count": state.question_optional_count,
        "question_special_count": state.question_special_count,
        "question_total_count": state.question_total_count,
        "question_required_limit": state.question_required_limit,
        "question_optional_limit": state.question_optional_limit,
        "question_special_limit": state.question_special_limit,
    }


def _clone_approval_to_dict(state):
    return {
        "project_id": state.project_id,
        "artifact_type": state.artifact_type,
        "key_figures_approved": state.key_figures_approved,
        "certification_path_approved": state.certification_path_approved,
        "template_selected": state.template_selected,
        "summary_confirmed": state.summary_confirmed,
    }


@pytest.mark.asyncio
async def test_concurrent_question_counter_updates_no_lost_update(monkeypatch):
    project_id = "concurrent-q"
    shared = {
        "state": ConversationStateModel(
            project_id=project_id,
            policy_version=growth_v1_controls.POLICY_VERSION_V1,
            consultation_mode="예비",
            profile_stage="예비",
            question_mode="server",
            question_required_count=0,
            question_optional_count=0,
            question_special_count=0,
            question_total_count=0,
            question_required_limit=8,
            question_optional_limit=3,
            question_special_limit=1,
        )
    }

    async def fake_get_or_create(_project_id: str, policy_version: str = growth_v1_controls.POLICY_VERSION_LEGACY):
        await asyncio.sleep(0)
        state = shared["state"]
        return ConversationStateModel(
            project_id=state.project_id,
            policy_version=state.policy_version,
            consultation_mode=state.consultation_mode,
            profile_stage=state.profile_stage,
            question_mode=state.question_mode,
            question_required_count=state.question_required_count,
            question_optional_count=state.question_optional_count,
            question_special_count=state.question_special_count,
            question_total_count=state.question_total_count,
            question_required_limit=state.question_required_limit,
            question_optional_limit=state.question_optional_limit,
            question_special_limit=state.question_special_limit,
        )

    def persist(state):
        shared["state"] = ConversationStateModel(**_clone_state_to_dict(state))

    def session_factory():
        return _MemorySession(persist)

    monkeypatch.setattr(growth_v1_controls, "get_or_create_conversation_state", fake_get_or_create)
    monkeypatch.setattr(growth_v1_controls, "AsyncSessionLocal", session_factory)

    await asyncio.gather(
        *[
            growth_v1_controls.update_question_counters(project_id, "필수")
            for _ in range(8)
        ]
    )

    final = shared["state"]
    assert final.question_required_count == 8
    assert final.question_total_count == 8


@pytest.mark.asyncio
async def test_concurrent_approval_step_updates_no_lost_update(monkeypatch):
    project_id = "concurrent-approval"
    shared = {
        "state": ArtifactApprovalStateModel(
            project_id=project_id,
            artifact_type="business_plan",
            key_figures_approved=False,
            certification_path_approved=False,
            template_selected=False,
            summary_confirmed=False,
        )
    }

    async def fake_get_or_create_approval_state(_project_id: str, _artifact_type: str):
        await asyncio.sleep(0)
        state = shared["state"]
        return ArtifactApprovalStateModel(
            project_id=state.project_id,
            artifact_type=state.artifact_type,
            key_figures_approved=state.key_figures_approved,
            certification_path_approved=state.certification_path_approved,
            template_selected=state.template_selected,
            summary_confirmed=state.summary_confirmed,
        )

    def persist(state):
        shared["state"] = ArtifactApprovalStateModel(**_clone_approval_to_dict(state))

    def session_factory():
        return _MemorySession(persist)

    monkeypatch.setattr(growth_v1_controls, "get_or_create_approval_state", fake_get_or_create_approval_state)
    async def fake_get_project_policy_state(_project_id: str):
        return ConversationStateModel(
            project_id=project_id,
            policy_version=growth_v1_controls.POLICY_VERSION_V1,
            consultation_mode="예비",
            profile_stage="예비",
            question_mode="server",
            question_required_count=0,
            question_optional_count=0,
            question_special_count=0,
            question_total_count=0,
            question_required_limit=8,
            question_optional_limit=3,
            question_special_limit=1,
        )

    monkeypatch.setattr(growth_v1_controls, "get_project_policy_state", fake_get_project_policy_state)
    monkeypatch.setattr(growth_v1_controls, "AsyncSessionLocal", session_factory)

    await asyncio.gather(
        growth_v1_controls.update_approval_step(project_id, "business_plan", "key_figures_approved", True),
        growth_v1_controls.update_approval_step(project_id, "business_plan", "certification_path_approved", True),
        growth_v1_controls.update_approval_step(project_id, "business_plan", "template_selected", True),
        growth_v1_controls.update_approval_step(project_id, "business_plan", "summary_confirmed", True),
    )

    final = shared["state"]
    assert final.key_figures_approved is True
    assert final.certification_path_approved is True
    assert final.template_selected is True
    assert final.summary_confirmed is True
