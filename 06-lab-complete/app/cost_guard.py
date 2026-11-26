"""
Cost guard (tiêu chí 8).

  • Cộng dồn chi phí LLM (tính theo token) vào Redis, key theo ngày (UTC) → tự reset mỗi ngày
  • Chi phí đã tiêu + chi phí ước tính của request sắp gọi > DAILY_BUDGET_USD → HTTP 402
    (cộng cả chi phí ước tính để request cuối không đẩy tổng vượt budget)
"""
from datetime import datetime, timezone

import redis
from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

# Giá USD / 1K tokens (tham khảo gpt-4o-mini)
PRICE_INPUT_PER_1K = 0.00015
PRICE_OUTPUT_PER_1K = 0.0006


def _day_key() -> str:
    return f"cost:{datetime.now(timezone.utc):%Y-%m-%d}"


def estimate_tokens(text: str) -> int:
    """Ước lượng thô: ~4 ký tự / token."""
    return max(1, len(text) // 4)


def cost_of(input_tokens: int, output_tokens: int) -> float:
    return input_tokens / 1000 * PRICE_INPUT_PER_1K + output_tokens / 1000 * PRICE_OUTPUT_PER_1K


def check_budget(estimated_cost: float = 0.0) -> None:
    """Gọi TRƯỚC khi gọi LLM. Hết budget → raise HTTPException(status_code=402)."""
    try:
        spent = float(r.get(_day_key()) or 0)
    except redis.RedisError:
        raise HTTPException(status_code=503, detail="Cost guard unavailable")
    if spent + estimated_cost > settings.daily_budget_usd:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "Daily budget exceeded",
                "spent_usd": round(spent, 6),
                "budget_usd": settings.daily_budget_usd,
                "resets": "00:00 UTC",
            },
        )


def record_cost(input_tokens: int, output_tokens: int) -> float:
    """Gọi SAU khi gọi LLM. Cộng chi phí của request vào tổng, trả về chi phí request."""
    cost = cost_of(input_tokens, output_tokens)
    key = _day_key()
    try:
        pipe = r.pipeline()
        pipe.incrbyfloat(key, cost)          # atomic, an toàn khi nhiều worker / instance
        pipe.expire(key, 2 * 24 * 3600)
        pipe.execute()
    except redis.RedisError:
        pass  # đã có câu trả lời cho user — không làm hỏng response vì lỗi ghi sổ
    return cost
