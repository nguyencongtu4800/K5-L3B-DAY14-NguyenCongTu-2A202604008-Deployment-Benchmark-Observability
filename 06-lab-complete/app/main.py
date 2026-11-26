"""
Production AI Agent — Part 6 Final Project.

Chạy:   uvicorn app.main:app --workers 2
Kiểm tra: python check_production_ready.py
"""
import json
import logging
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, cost_of, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import r, redis_healthy
from utils.mock_llm import ask as llm_ask


# ── Tiêu chí 10: mỗi dòng log của app = 1 JSON object hợp lệ ────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "lvl": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update(getattr(record, "fields", {}))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Dựng cả object rồi json.dumps MỘT lần (không lồng chuỗi JSON vào format string)
        return json.dumps(payload, ensure_ascii=False, default=str)


_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(JsonFormatter())
logger = logging.getLogger("agent")
logger.handlers = [_handler]
logger.setLevel(settings.log_level)
logger.propagate = False  # không đụng tới logger của uvicorn


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"fields": {"event": event, **fields}})


# ── Tiêu chí 11: graceful shutdown qua lifespan ─────────────────────────────
# uvicorn tự bắt SIGTERM: ngừng nhận kết nối mới, chờ request đang chạy xong,
# rồi mới chạy phần sau `yield`. Không tự đăng ký signal handler để khỏi đè handler của uvicorn.
_state = {"ready": False}


@asynccontextmanager
async def lifespan(app: FastAPI):
    _state["ready"] = True
    log_event("startup", environment=settings.environment, port=settings.port,
              rate_limit_per_minute=settings.rate_limit_per_minute,
              daily_budget_usd=settings.daily_budget_usd)
    yield
    _state["ready"] = False
    log_event("shutdown")
    try:
        r.close()
    except Exception:
        pass


app = FastAPI(title="Production AI Agent", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def access_log(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    log_event("request", method=request.method, path=request.url.path,
              status=response.status_code, ms=round((time.perf_counter() - start) * 1000, 1))
    return response


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    # Không lộ stack trace ra client; ghi đầy đủ vào log
    logger.error("unhandled_error", exc_info=exc,
                 extra={"fields": {"event": "error", "path": request.url.path}})
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=100)


# ── Tiêu chí 4: liveness — luôn 200 khi process còn chạy (không phụ thuộc Redis) ──
@app.get("/health")
def health():
    return {"status": "ok", "environment": settings.environment}


# ── Tiêu chí 5: readiness — 503 khi Redis không kết nối được hoặc đang shutdown ──
@app.get("/ready")
def ready():
    if not _state["ready"] or not redis_healthy():
        return JSONResponse(status_code=503, content={"ready": False})
    return {"ready": True}


# ── /ask: auth (401) → rate limit (429) → budget (402) → LLM → ghi chi phí ──
# `def` (không async): FastAPI chạy trong threadpool, nên time.sleep của LLM
# không chặn event loop — request đang chạy vẫn hoàn thành khi nhận SIGTERM.
@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    check_rate_limit(api_key)
    input_tokens = estimate_tokens(body.question)
    check_budget(estimated_cost=cost_of(input_tokens, 0))
    answer = llm_ask(body.question)
    cost = record_cost(input_tokens, estimate_tokens(answer))
    log_event("ask", question_len=len(body.question), cost_usd=round(cost, 8))
    return {"question": body.question, "answer": answer}
