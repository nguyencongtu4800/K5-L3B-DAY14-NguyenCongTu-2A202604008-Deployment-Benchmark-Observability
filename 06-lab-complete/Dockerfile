# ============================================================
# Production Dockerfile — multi-stage (tiêu chí 1)
#   • builder: cài dependencies vào /install
#   • runtime: python:3.11-slim, chỉ copy packages + code, chạy bằng user non-root
#   • HEALTHCHECK gọi GET /health
#   • uvicorn là PID 1 (exec) → nhận SIGTERM trực tiếp khi `docker stop`
#   • Lắng nghe theo biến PORT (Railway/Render cấp), mặc định 8000
# ============================================================

# ── Stage 1: builder ──────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: runtime ──────────────────────────────────────────
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

WORKDIR /app
COPY --from=builder /install /usr/local
COPY --chown=app:app app/ ./app/
COPY --chown=app:app utils/ ./utils/

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'), timeout=3)" || exit 1

# `exec` thay sh bằng uvicorn → uvicorn là PID 1, nhận SIGTERM và tắt êm.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2}"]
