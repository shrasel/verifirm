import json
import logging
import time
from collections import OrderedDict
from typing import Optional

from app.config import get_settings
from app.models.response import EnrichmentResponse

logger = logging.getLogger(__name__)


class CacheManager:
    """
    Enterprise-hardened Cache layer supporting both Redis and an in-memory
    LRU cache with bounded capacity and TTL expiration.
    Key format: company:{normalized_domain}
    """
    MAX_IN_MEMORY_ITEMS = 5000
    _memory_cache: OrderedDict[str, tuple[float, str]] = OrderedDict()

    def __init__(self):
        self.settings = get_settings()
        self._redis_client = None
        self._init_redis()

    def _init_redis(self):
        if self.settings.REDIS_URL:
            try:
                import redis
                self._redis_client = redis.from_url(
                    self.settings.REDIS_URL,
                    decode_responses=True,
                    socket_connect_timeout=2.0
                )
                self._redis_client.ping()
                logger.info("Connected to Redis cache successfully.")
            except Exception as e:
                logger.warning(f"Could not connect to Redis ({e}); falling back to bounded in-memory LRU cache.")
                self._redis_client = None

    def _make_key(self, domain: str) -> str:
        return f"company:{domain.strip().lower()}"

    def get(self, domain: str) -> Optional[EnrichmentResponse]:
        key = self._make_key(domain)

        # 1. Try Redis if connected
        if self._redis_client:
            try:
                val = self._redis_client.get(key)
                if val:
                    data = json.loads(val)
                    res = EnrichmentResponse(**data)
                    res.metadata["cache_hit"] = True
                    return res
            except Exception as e:
                logger.warning(f"Redis get error: {e}")

        # 2. In-memory LRU fallback
        if key in self._memory_cache:
            expires_at, payload_str = self._memory_cache[key]
            if time.time() < expires_at:
                # Mark as Most Recently Used
                self._memory_cache.move_to_end(key)
                data = json.loads(payload_str)
                res = EnrichmentResponse(**data)
                res.metadata["cache_hit"] = True
                return res
            else:
                del self._memory_cache[key]

        return None

    def set(self, domain: str, response: EnrichmentResponse, ttl_seconds: Optional[int] = None):
        key = self._make_key(domain)
        ttl = ttl_seconds or self.settings.CACHE_TTL_SECONDS
        payload_str = response.model_dump_json()

        # 1. Try Redis
        if self._redis_client:
            try:
                self._redis_client.setex(key, ttl, payload_str)
                return
            except Exception as e:
                logger.warning(f"Redis set error: {e}")

        # 2. In-memory LRU fallback
        expires_at = time.time() + ttl
        if key in self._memory_cache:
            self._memory_cache.move_to_end(key)
        self._memory_cache[key] = (expires_at, payload_str)

        # Enforce LRU capacity limit
        if len(self._memory_cache) > self.MAX_IN_MEMORY_ITEMS:
            self._memory_cache.popitem(last=False)  # Evict Least Recently Used

    def invalidate(self, domain: str):
        key = self._make_key(domain)
        if self._redis_client:
            try:
                self._redis_client.delete(key)
            except Exception:
                pass
        self._memory_cache.pop(key, None)
