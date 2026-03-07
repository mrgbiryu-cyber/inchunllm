import json
import redis.asyncio as redis
from typing import Optional
from app.core.config import settings
from app.schemas.debug import DebugInfo
from structlog import get_logger

logger = get_logger(__name__)

class DebugService:
    def __init__(self):
        self.redis_url = settings.REDIS_URL or "redis://localhost:6379/0"
        self.redis = redis.from_url(self.redis_url, decode_responses=True)
        self.ttl = 600  # 10분

    async def save_debug_info(self, request_id: str, debug_info: DebugInfo):
        """
        Admin 전용 디버그 정보를 Redis에 저장 (TTL 10분)
        """
        try:
            key = f"debug:{request_id}"
            # JSON 직렬화 시 datetime 처리를 위해 mode='json' 사용하거나 default=str 처리
            data = debug_info.model_dump_json()
            await self.redis.setex(key, self.ttl, data)
            logger.debug("Debug info cached", request_id=request_id)
        except Exception as e:
            logger.error("Failed to cache debug info", error=str(e))

    async def get_debug_info(self, request_id: str) -> Optional[DebugInfo]:
        """
        request_id로 디버그 정보 조회 (TTL 연장 안함)
        """
        try:
            key = f"debug:{request_id}"
            data = await self.redis.get(key)
            if not data:
                return None
            return DebugInfo.model_validate_json(data)
        except Exception as e:
            logger.error("Failed to retrieve debug info", error=str(e))
            return None

debug_service = DebugService()
