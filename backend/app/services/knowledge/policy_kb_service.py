from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


class PolicyKBService:
    _DEFAULT_KB_PATH = Path(__file__).resolve().parents[3] / "data" / "policy_kb" / "cert_ip_baseline_v1.json"

    def __init__(self, kb_path: str = "backend/data/policy_kb/cert_ip_baseline_v1.json"):
        base = Path(kb_path)
        if kb_path == "backend/data/policy_kb/cert_ip_baseline_v1.json" and not base.exists():
            if self._DEFAULT_KB_PATH.exists():
                base = self._DEFAULT_KB_PATH
        self.kb_path = base
        self._cache: Dict[str, Any] | None = None

    def load(self) -> Dict[str, Any]:
        if self._cache is None:
            self._cache = json.loads(self.kb_path.read_text(encoding="utf-8-sig"))
        return self._cache

    def items(self) -> List[Dict[str, Any]]:
        return self.load().get("items", [])


policy_kb_service = PolicyKBService()
