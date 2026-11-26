# Deployment Information

## Public URL

https://agent-production-e3fbaa11.up.railway.app

## Platform

**Railway**: project `day14_NguyenCongTu_04008`, environment `production`. Gồm 2 service:

| Service | Loại | Trạng thái |
|---|---|---|
| `agent` | Build từ `06-lab-complete/Dockerfile` (theo `railway.toml`: `builder = "DOCKERFILE"`, `healthcheckPath = "/health"`) | ● Online, deploy SUCCESS |
| `Redis` | Railway Redis database (có volume) | ● Online |

Code được deploy là **agent Part 6** trong `06-lab-complete/`. Cách deploy:

```powershell
cd 06-lab-complete
railway login
railway init                       # tạo project
railway add --database redis       # thêm Redis
railway add --service agent        # tạo service agent + nhập biến môi trường (xem bên dưới)
railway up --service agent         # upload + build bằng Dockerfile
railway domain --service agent     # tạo public URL
```

## Environment Variables Set

Biến môi trường đặt trên Railway (service `agent`), **không ghi trong code hay trong file nào của repo**:

| Biến | Giá trị | Ghi chú |
|---|---|---|
| `AGENT_API_KEY` | `********` (chuỗi ngẫu nhiên, sinh bằng `python -c "import secrets; print(secrets.token_urlsafe(32))"`) | Secret: chỉ nằm trên Railway |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` | Biến tham chiếu của Railway, trỏ tới Redis qua mạng nội bộ |
| `ENVIRONMENT` | `production` | |
| `RATE_LIMIT_PER_MINUTE` | `10` | |
| `DAILY_BUDGET_USD` | `1.0` | |
| `PORT` | _(Railway tự cấp)_ | Dockerfile lắng nghe `${PORT:-8000}` |

## Test Commands

Đặt `BASE` là URL public và `AGENT_API_KEY` là key đã đặt trên Railway (gõ bằng `read -s` để key không hiện ra màn hình hay lưu vào history):

```bash
export BASE="https://agent-production-e3fbaa11.up.railway.app"
read -s -p "API key: " AGENT_API_KEY; echo
```

### Health check (liveness)

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl "$BASE/health"
# {"status":"ok","environment":"production"}
```

### Readiness (đã kết nối Redis)

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl "$BASE/ready"
# {"ready":true}
```

### API không có key → 401

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" \
  -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
# {"detail":"Invalid or missing API key"}
```

### API có key → 200

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" \
  -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" \
  -d '{"question":"Hello"}'   # 200

curl -X POST "$BASE/ask" \
  -H "X-API-Key: $AGENT_API_KEY" -H "Content-Type: application/json" \
  -d '{"user_id": "test", "question": "Hello"}'
```

### Kết quả 4 lệnh tự kiểm (CP6)

| Lệnh | Mong đợi | Thực tế |
|---|---|---|
| `GET /health` | 200 | ✅ 200 |
| `GET /ready` | 200 | ✅ 200 |
| `POST /ask` không key | 401 | ✅ 401 |
| `POST /ask` có key | 200 | ✅ 200 |

### Script chấm với URL public

```text
$ python check_production_ready.py --public-url https://agent-production-e3fbaa11.up.railway.app
     ✅ Public URL: GET https://agent-production-e3fbaa11.up.railway.app/health → 200
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 44/44 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
```

## Xem logs

```powershell
railway logs --service agent           # log runtime (mỗi dòng là JSON)
railway logs --service agent --build   # log build Docker
```

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png): project có 2 service `agent` + `Redis` đều Online
- [Service running](screenshots/running.png): deployment của `agent` ở trạng thái Active / Success
- [Test results](screenshots/test.png): 4 lệnh tự kiểm trả 200 / 200 / 401 / 200 (chỉ in mã HTTP, không lộ key)
