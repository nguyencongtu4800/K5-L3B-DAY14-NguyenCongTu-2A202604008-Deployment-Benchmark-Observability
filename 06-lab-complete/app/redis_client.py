"""
Kết nối Redis dùng chung cho rate limiter và cost guard.

  • Đọc REDIS_URL từ config
  • KHÔNG ping lúc import → app vẫn khởi động được khi Redis chưa sẵn sàng
  • Timeout ngắn → /ready trả 503 nhanh khi Redis chết
"""
import redis

from app.config import settings

# from_url chỉ tạo connection pool, chưa mở kết nối.
r = redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
)


def redis_healthy() -> bool:
    try:
        return bool(r.ping())
    except redis.RedisError:
        return False
