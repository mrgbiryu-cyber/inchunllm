import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from app.models.schemas import RuleSet, RuleStatus


class RulesetRepository:
    """File-backed repository for ruleset versioning."""

    _DEFAULT_RULESET_PATH = Path(__file__).resolve().parents[3] / "data" / "rulesets"

    def __init__(self, base_path: str = "backend/data/rulesets"):
        base = Path(base_path)
        if base_path == "backend/data/rulesets":
            has_candidate_ruleset = bool(list(base.glob("company-growth-default_*.json")))
            fallback = self._DEFAULT_RULESET_PATH
            if not has_candidate_ruleset and fallback.exists():
                has_fallback_ruleset = bool(list(fallback.glob("company-growth-default_*.json")))
                if has_fallback_ruleset:
                    base = fallback
        self.base_path = base
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _path_for(self, ruleset_id: str, version: str) -> Path:
        return self.base_path / f"{ruleset_id}_{version}.json"

    def list_rulesets(self, ruleset_id: str = "company-growth-default") -> List[RuleSet]:
        items: List[RuleSet] = []
        pattern = f"{ruleset_id}_*.json"
        for path in self.base_path.glob(pattern):
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            items.append(RuleSet(**data))
        return sorted(items, key=lambda x: self._version_key(x.version))

    def _version_key(self, version: str):
        normalized = (version or "").lstrip("vV").replace("-", ".")
        tokens = []
        for part in normalized.split("."):
            if not part:
                continue
            if part.isdigit():
                tokens.append((0, int(part)))
            else:
                tokens.append((1, part.lower()))
        return tokens

    def get(self, ruleset_id: str, version: str) -> RuleSet:
        path = self._path_for(ruleset_id, version)
        if not path.exists():
            raise FileNotFoundError(f"Ruleset not found: {ruleset_id}:{version}")
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return RuleSet(**data)

    def save(self, ruleset: RuleSet) -> RuleSet:
        payload = ruleset.model_dump(mode="json") if hasattr(ruleset, "model_dump") else ruleset.dict()
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        path = self._path_for(ruleset.ruleset_id, ruleset.version)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return RuleSet(**payload)

    def create(self, ruleset: RuleSet) -> RuleSet:
        path = self._path_for(ruleset.ruleset_id, ruleset.version)
        if path.exists():
            raise ValueError("Ruleset version already exists")
        return self.save(ruleset)

    def clone(self, ruleset_id: str, source_version: str, new_version: str, author: str = "system") -> RuleSet:
        source = self.get(ruleset_id, source_version)
        payload = source.model_dump() if hasattr(source, "model_dump") else source.dict()
        payload = deepcopy(payload)
        payload["version"] = new_version
        payload["status"] = RuleStatus.DRAFT.value
        payload["author"] = author
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        payload["created_at"] = datetime.now(timezone.utc).isoformat()
        return self.create(RuleSet(**payload))

    def get_active(self, ruleset_id: str = "company-growth-default") -> RuleSet:
        rulesets = self.list_rulesets(ruleset_id)
        active = [r for r in rulesets if r.status == RuleStatus.ACTIVE]
        if not active:
            if rulesets:
                return rulesets[-1]
            raise FileNotFoundError("No rulesets available")
        return active[-1]

    def activate(self, ruleset_id: str, version: str) -> RuleSet:
        target = self.get(ruleset_id, version)
        for item in self.list_rulesets(ruleset_id):
            if item.status == RuleStatus.ACTIVE and item.version != version:
                item.status = RuleStatus.ARCHIVED
                self.save(item)
        target.status = RuleStatus.ACTIVE
        return self.save(target)


ruleset_repository = RulesetRepository()
