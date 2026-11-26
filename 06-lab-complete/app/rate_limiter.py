"""
Rate limiting (tiêu chí 7).

  • Mỗi API key tối đa RATE_LIMIT_PER_MINUTE request trong 60 giây (sliding window)
  • Vượt giới hạn → HTTP 429 kèm header Retry-After
  • State lưu trong Redis (sorted set, score = timestamp) → mọi worker / instance dùng chung
    và vẫn còn sau khi restart agent
"""
import hashlib
import time
import uuid

import redis
from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

WINDOW_SECONDS = 60


def check_rate_limit(key: str) -> None:
    limit = settings.rate_limit_per_minute
    # Không lưu API key thô trong Redis — chỉ lưu hash.
    rkey = "ratelimit:" + hashlib.sha256(key.encode()).hexdigest()[:16]
    now = time.time()
    member = f"{now:.6f}-{uuid.uuid4().hex[:8]}"

    try:
        # Một transaction (MULTI/EXEC): 2 worker gọi cùng lúc cũng không đếm sai.
        pipe = r.pipeline(transaction=True)
        pipe.zremrangebyscore(rkey, 0, now - WINDOW_SECONDS)  # bỏ request ngoài cửa sổ
        pipe.zadd(rkey, {member: now})                         # ghi request này
        pipe.zcard(rkey)                                       # đếm trong cửa sổ
        pipe.expire(rkey, WINDOW_SECONDS + 1)
        _, _, count, _ = pipe.execute()

        if count <= limit:
            return

        r.zrem(rkey, member)  # request bị chặn không chiếm chỗ trong cửa sổ
        oldest = r.zrange(rkey, 0, 0, withscores=True)
        retry_after = int(oldest[0][1] + WINDOW_SECONDS - now) + 1 if oldest else WINDOW_SECONDS
    except redis.RedisError:
        raise HTTPException(status_code=503, detail="Rate limiter unavailable")

    raise HTTPException(
        status_code=429,
        detail={"error": "Rate limit exceeded", "limit": limit, "window_seconds": WINDOW_SECONDS},
        headers={"Retry-After": str(max(retry_after, 1))},
    )
