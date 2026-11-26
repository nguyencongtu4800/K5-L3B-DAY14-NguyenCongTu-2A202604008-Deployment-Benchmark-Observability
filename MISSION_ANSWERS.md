# Day 14 Lab — Mission Answers

> Học viên: Nguyễn Công Tú · MSSV 2A202604008 · Lớp K5-L3B

---

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns trong `01-localhost-vs-production/develop/app.py`

| # | Anti-pattern | Dòng code | Vì sao nguy hiểm |
|---|---|---|---|
| 1 | **Hardcode secret** | L17 `OPENAI_API_KEY = "sk-hardcoded-fake-key-never-do-this"`, L18 `DATABASE_URL = "postgresql://admin:password123@..."` | Push lên GitHub là lộ key và mật khẩu DB. Bot quét repo public tìm key chỉ trong vài phút. Xoá file sau đó cũng không đủ vì key vẫn nằm trong lịch sử commit, phải revoke. |
| 2 | **Log ra secret** | L34 `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` | Key bị ghi vào log. Log thường được gửi sang hệ thống khác (Loki, Datadog), ai đọc được log cũng có key. |
| 3 | **Config cố định trong code** | L21 `DEBUG = True`, L22 `MAX_TOKENS = 500` | Muốn đổi giữa dev/staging/prod phải sửa code và build lại. Production chạy luôn với debug bật. |
| 4 | **`print()` thay cho logging** | L33, L34, L38 | Không có level, không có timestamp, không parse được bằng máy, không lọc được theo mức độ. |
| 5 | **Không có health check** | (không có `/health`, `/ready`) | Platform (Railway, Render, K8s) không biết agent còn sống hay không để tự restart. Load balancer không biết khi nào được gửi traffic vào. |
| 6 | **Bind `localhost`** | L51 `host="localhost"` | Chạy trong container thì bên ngoài không gọi vào được, phải bind `0.0.0.0`. |
| 7 | **Port cố định** | L52 `port=8000` | Railway và Render inject cổng qua biến `PORT`. Cố định 8000 thì platform không route được. |
| 8 | **`reload=True`** | L53 | Chế độ auto-reload chỉ dành cho dev: tốn CPU để theo dõi file, chạy thêm process con, không dùng cho production. |
| 9 | **Không xử lý SIGTERM** | (không có handler, không có lifespan) | Platform gửi SIGTERM khi deploy hoặc scale down. Không xử lý thì request đang chạy bị cắt ngang, kết nối không được đóng gọn. |
| 10 | **Không validate input, nhận qua query string** | L31 `def ask_agent(question: str)` | Câu hỏi nằm trên URL, nên bị ghi vào access log và lịch sử proxy. Không giới hạn độ dài. |

### Exercise 1.2: Chạy bản develop

Chạy `python app.py` trong `develop/` (Python 3.11.9, Windows):

```text
$ curl -i -X POST "http://localhost:8000/ask?question=Hello"
HTTP/1.1 500 Internal Server Error
Internal Server Error
```

Log server:

```text
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this      <- secret bị in ra log
...
  File ".../develop/app.py", line 38, in ask_agent
    print(f"[DEBUG] Response: {response}")
UnicodeEncodeError: 'charmap' codec can't encode character 'Đ' ...
```

**Nhận xét:** đây là một ca "works on my machine" thật. Câu trả lời mock có tiếng Việt, mà console Windows dùng mã hoá cp1252, nên lệnh `print()` ném exception và cả request trả 500. Trên Linux/macOS (UTF-8) thì lỗi này không xảy ra. Dùng `print` để debug đã làm hỏng tính năng chính.

Đặt `PYTHONIOENCODING=utf-8` để ép encoding giống môi trường Linux thì chạy được:

```text
$ curl -i -X POST "http://localhost:8000/ask?question=Hello"
HTTP/1.1 200 OK
{"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}
```

Các trường hợp khác:

```text
$ curl -i -X POST localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 422 Unprocessable Entity          # bản develop chỉ nhận question qua query
$ curl -i localhost:8000/health
HTTP/1.1 404 Not Found                     # không có health check
```

**Kết luận:** chạy được, nhưng chưa sẵn sàng cho production. Nó lộ secret trong log, không có health check, và hỏng tuỳ theo môi trường chạy.

### Exercise 1.3: Chạy bản production và so sánh

Chạy `python app.py` trong `production/` (không đặt `OPENAI_API_KEY`, dùng mock):

```text
$ curl -i localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":13.9,"version":"1.0.0","environment":"development","timestamp":"2026-11-26T07:24:32.770811+00:00"}

$ curl -i localhost:8000/ready
HTTP/1.1 200 OK
{"ready":true}

$ curl -i -X POST localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic.","model":"gpt-4o-mini"}

$ curl -i -X POST localhost:8000/ask -H "Content-Type: application/json" -d '{}'
HTTP/1.1 422 Unprocessable Entity
{"detail":"question field is required"}     # lỗi rõ ràng, không lộ stack trace
```

| Feature | Develop | Production | Tại sao quan trọng? |
|---|---|---|---|
| **Config** | Hardcode (`DEBUG = True`, `MAX_TOKENS = 500`) | Env vars qua `config.py` (`os.getenv("PORT", "8000")`...) | Một image chạy được ở mọi môi trường, chỉ đổi biến môi trường (12-Factor III). Không phải sửa code khi deploy. |
| **Secrets** | `OPENAI_API_KEY = "sk-..."` trong code | `os.getenv("OPENAI_API_KEY", "")`. `.env` bị gitignore, chỉ commit `.env.example` | Secret không vào Git. Lộ key thì chỉ cần đổi biến trên platform, không phải sửa lịch sử commit. |
| **Fail fast** | Không kiểm tra | `validate()` raise lỗi nếu `ENVIRONMENT=production` mà thiếu `AGENT_API_KEY` | Phát hiện cấu hình sai ngay lúc khởi động, thay vì chạy lên rồi để hở API. |
| **Health check** | Không có (`/health` → 404) | `GET /health` (liveness) + `GET /ready` (readiness, 503 khi chưa sẵn sàng) | Platform dùng `/health` để tự restart khi agent chết, load balancer dùng `/ready` để chỉ gửi traffic vào instance đã sẵn sàng. |
| **Logging** | `print()`, có in cả API key | Log JSON với `event`, `question_length`; không log secret, không log nguyên câu hỏi | Log aggregator parse và lọc được. Không lộ secret hay dữ liệu người dùng. |
| **Shutdown** | Tắt đột ngột | `lifespan` (đặt `is_ready = False` rồi dọn dẹp) + handler `SIGTERM` | Khi deploy hoặc scale down, request đang chạy được xử lý xong. `/ready` chuyển sang 503 để LB ngừng gửi traffic mới. |
| **Host / Port** | `localhost:8000` cố định | `HOST=0.0.0.0`, `PORT` từ env | Chạy được trong container và trên Railway/Render, nơi platform inject `PORT`. |
| **Reload/Debug** | `reload=True` luôn bật | `reload=settings.debug`, mặc định `false` | Production không chạy file watcher, không bật debug. |
| **Input** | Query string, không kiểm tra | JSON body, câu hỏi rỗng trả 422 với thông báo rõ ràng | API rõ ràng, lỗi dễ hiểu, câu hỏi không bị ghi vào access log. |
| **CORS** | Không cấu hình | `ALLOWED_ORIGINS` từ env | Chỉ frontend được phép mới gọi được từ trình duyệt. |

**Quan sát thêm khi chạy bản production:**
- Các dòng log JSON mức INFO (`startup`, `agent_request`) **không hiện ra**, chỉ có `WARNING:root:OPENAI_API_KEY not set`. Lý do: `config.py` gọi `logging.warning()` lúc import, trước khi `app.py` gọi `logging.basicConfig(...)`. Root logger đã được cấu hình mặc định (level WARNING, format thường), nên `basicConfig` sau đó không có tác dụng. Cách sửa: gọi `basicConfig(..., force=True)`, hoặc cấu hình logging trước khi import `config`.
- `requirements.txt` có `python-dotenv` nhưng code không gọi `load_dotenv()`, nên bước `cp .env.example .env` thực tế không có tác dụng. App chạy được nhờ giá trị mặc định trong `os.getenv(...)`. Trên cloud thì không sao, vì biến được đặt trực tiếp trên platform.

### Checkpoint 1 — trả lời câu hỏi

- **Vì sao hardcode secret nguy hiểm?** Code đi qua rất nhiều nơi: Git, CI, image Docker, máy đồng nghiệp. Lộ ở một chỗ là lộ hết. Lịch sử Git giữ lại vĩnh viễn, bot quét GitHub public tìm key liên tục, nên bị lạm dụng tính phí chỉ sau vài phút. Cách xử lý duy nhất là revoke key và tạo key mới.
- **Dùng environment variables thế nào?** Đọc bằng `os.getenv("NAME", default)` ở một chỗ tập trung (`config.py`). Liệt kê đủ biến trong `.env.example` (không có giá trị thật). Local thì dùng `.env` đã gitignore, cloud thì đặt trên dashboard của platform.
- **Vai trò của health check:** `/health` (liveness) trả lời "process còn sống không", fail thì platform restart. `/ready` (readiness) trả lời "có nhận traffic được chưa", fail thì load balancer tạm bỏ instance đó ra nhưng không restart.
- **Graceful shutdown là gì:** khi nhận SIGTERM, ngừng nhận request mới (readiness → 503), xử lý xong các request đang chạy, đóng kết nối DB/Redis, rồi thoát với exit code 0. Platform sẽ gửi SIGKILL sau một khoảng timeout, nên việc dọn dẹp phải xong trước đó.

### Câu hỏi thảo luận

1. **Push code có API key lên GitHub public thì sao?** Bot quét secret phát hiện trong vài phút và dùng key để gọi API, bên mình chịu tiền. Nhà cung cấp có thể tự khoá key, gây gián đoạn dịch vụ. Xoá commit cũng không đủ vì đã có bản fork hoặc cache, phải revoke key ngay.
2. **Vì sao stateless quan trọng khi scale?** Khi có nhiều instance sau load balancer, request liên tiếp của cùng một người dùng có thể vào các instance khác nhau. State nằm trong RAM thì instance này không thấy state của instance kia, và mất hết khi restart. Đưa state ra Redis/DB thì instance nào cũng xử lý được, thêm hay bớt instance thoải mái.
3. **"Dev/prod parity" nghĩa là gì?** Môi trường dev giống production nhiều nhất có thể: cùng phiên bản Python, cùng OS và encoding, cùng loại backing service (Redis thật thay vì dict trong RAM). Lỗi `UnicodeEncodeError` ở Exercise 1.2 là ví dụ cụ thể: Windows dùng cp1252, Linux dùng UTF-8, cùng một đoạn code nhưng một bên 500, một bên 200. Chạy bằng Docker giúp hai môi trường giống nhau.

---

## Part 2: Docker

### Exercise 2.1: Đọc `02-docker/develop/Dockerfile`

1. **Base image:** `python:3.11` (L8), bản Python đầy đủ dựa trên Debian. Bản này có sẵn gcc, header, git… nên nặng, khoảng 1.6 GB.
2. **Working directory:** `/app` (L11, `WORKDIR /app`). Các lệnh `COPY`, `RUN`, `CMD` sau đó đều chạy tương đối với `/app`.
3. **Vì sao `COPY requirements.txt` đứng trước `COPY app.py`:** Docker cache theo từng layer. Một layer bị build lại khi file nó `COPY` thay đổi, và mọi layer phía sau cũng build lại theo. `requirements.txt` ít thay đổi, còn code thay đổi liên tục. Copy requirements rồi `pip install` trước (L14, L17) thì khi chỉ sửa `app.py`, layer `pip install` vẫn dùng cache. Lấy ví dụ từ `docker history my-agent:develop`: layer `pip install` nặng 53.3 MB, còn layer `COPY app.py` chỉ 12 kB. Nếu viết `COPY . .` trước, mỗi lần sửa một dòng code sẽ phải tải và cài lại toàn bộ thư viện.
4. **`CMD` và `ENTRYPOINT` khác nhau thế nào:**
   - `ENTRYPOINT` là chương trình cố định của container.
   - `CMD` là lệnh hoặc tham số mặc định, bị ghi đè khi gõ thêm lệnh sau `docker run <image> ...`.
   - Khi dùng cả hai, `CMD` trở thành tham số truyền cho `ENTRYPOINT`.
   - Ở đây chỉ có `CMD ["python", "app.py"]` (L30), nên `docker run my-agent:develop sh` sẽ chạy `sh` thay cho app, rất tiện để debug.
   - Dạng mảng JSON (exec form) giúp process chạy là PID 1 và nhận được SIGTERM trực tiếp. Dạng shell `CMD python app.py` thì `/bin/sh` mới là PID 1 và có thể không chuyển tiếp signal.

### Exercise 2.2: Build và chạy bản develop

```bash
# từ gốc repo
docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .     # 3m24s (lần đầu, phải tải python:3.11)
docker run -d --name cp2-dev -p 8000:8000 my-agent:develop
```

```text
$ docker ps
cp2-dev   my-agent:develop   Up 1 second   0.0.0.0:8000->8000/tcp

$ curl -i localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":0.9,"container":true}

$ curl -i -X POST "http://localhost:8000/ask?question=What%20is%20Docker%3F"
HTTP/1.1 200 OK
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}

$ docker exec cp2-dev sh -c 'whoami; python --version'
root                 <- bản develop chạy bằng root
Python 3.11.18
```

**Nhận xét:** trong container, câu trả lời tiếng Việt được in ra log bình thường. Ở Part 1, cùng kiểu `print()` này lại gây lỗi 500 trên Windows (cp1252). Container luôn là Linux UTF-8, nên môi trường giống nhau ở mọi máy. Đây là lợi ích "dev/prod parity" của Docker.

### Exercise 2.3: Multi-stage build và so sánh dung lượng

Đọc `02-docker/production/Dockerfile`:
- **Stage 1 `builder`** (`python:3.11-slim`, L16–29): cài `gcc` và `libpq-dev` để biên dịch thư viện, rồi `pip install --user`, cài thư viện vào `/root/.local`. Stage này chỉ dùng để build, không được đem đi deploy.
- **Stage 2 `runtime`** (`python:3.11-slim`, L36–72): base slim mới tinh, tạo user `appuser` không phải root, `COPY --from=builder /root/.local` chỉ lấy thư viện đã cài, copy `main.py` và `utils/`, rồi `USER appuser`, thêm `HEALTHCHECK` và `CMD uvicorn ... --workers 2`.
- **Vì sao image nhỏ hơn:**
  - Base `slim` thay cho bản full, bỏ được gcc, header và tài liệu.
  - Công cụ build (`gcc`, `libpq-dev`, apt cache) chỉ nằm ở stage 1 và bị bỏ lại. Image cuối chỉ gồm các layer của stage 2.
  - `--no-cache-dir` không giữ cache tải của pip.

```bash
docker build -f 02-docker/production/Dockerfile -t my-agent:advanced .   # 6m20s (lần đầu)
```

**Kết quả đo thật bằng `docker images`:**

```text
$ docker images my-agent
REPOSITORY:TAG      SIZE      IMAGE ID
my-agent:advanced   240MB     125aaae68c57
my-agent:develop    1.67GB    79bb03e5ec21
```

| Image | Size (`docker image inspect .Size`) |
|---|---|
| `my-agent:develop` (single-stage, `python:3.11`) | 1 667 776 078 B ≈ **1667.8 MB** |
| `my-agent:advanced` (multi-stage, `python:3.11-slim`) | 239 815 408 B ≈ **239.8 MB** |
| **Chênh lệch** | Nhỏ hơn **1428.0 MB = 85.6 %** (khoảng 7 lần) |

Cách tính: (1667.8 − 239.8) / 1667.8 = 85.6 %.

Ngoài dung lượng, bản advanced còn chạy bằng user không phải root, có `HEALTHCHECK` và chạy 2 worker. Vì ít package hơn, bề mặt tấn công (CVE) cũng nhỏ hơn.

### Exercise 2.4: Docker Compose stack

**Sơ đồ** (đọc từ `02-docker/production/docker-compose.yml` và `nginx/nginx.conf`):

```text
                 host :80 / :443
                        │
               ┌────────▼────────┐
  Client ────▶ │  nginx:alpine   │  reverse proxy + load balancer
               │  rate limit     │  10 req/s mỗi IP (burst 20) → 429
               └────────┬────────┘
                        │ proxy_pass http://agent_backend  (upstream agent:8000)
          ══════════════╪══════════════  network "internal" (bridge)
               ┌────────▼────────┐
               │  agent (build   │  FastAPI, uvicorn 2 workers, user appuser
               │  target runtime)│  KHÔNG mở port ra host, chỉ đi qua Nginx
               └───┬─────────┬───┘
   REDIS_URL=redis://redis:6379/0   QDRANT_URL=http://qdrant:6333
          ┌────────▼───┐   ┌─▼──────────────┐
          │ redis:7    │   │ qdrant v1.9.0  │
          │ alpine     │   │ vector DB (RAG)│
          │ vol redis_ │   │ vol qdrant_    │
          │ data       │   │ data           │
          └────────────┘   └────────────────┘
```

- **Services được start:** `nginx`, `agent`, `redis`, `qdrant` (4 service).
- **Cách các service giao tiếp:**
  - Tất cả nằm chung network `internal`. Các service gọi nhau bằng tên service thông qua DNS nội bộ của Docker, ví dụ `redis:6379`, `qdrant:6333`, `agent:8000`.
  - Chỉ Nginx mở port ra host. `agent` không có `ports:`, nên bên ngoài muốn gọi agent phải đi qua Nginx.
- **Thứ tự khởi động:**
  - `agent` có `depends_on` với `condition: service_healthy` lên `redis` (healthcheck `redis-cli ping`) và `qdrant` (healthcheck mở TCP tới 6333). Agent chỉ start khi hai service này đã healthy.
  - `nginx` `depends_on: agent`.
- **Dữ liệu:** hai named volume `redis_data` và `qdrant_data` giữ dữ liệu khi container bị tạo lại.
- **Secret:** đọc từ `env_file: .env.local`. File này bị gitignore (`.gitignore:5: .env.*`), không ghi thẳng vào compose.

**Kết quả test qua Nginx:**

```bash
cd 02-docker/production
touch .env.local                                   # bị gitignore
docker compose -p k5-day14-docker up -d --build
```

Lần đầu chạy, Docker Hub trả `429 Too Many Requests` vì pull ẩn danh vượt giới hạn. Đăng nhập Docker Hub (`docker login`) rồi chạy lại thì được.

```text
$ docker compose -p k5-day14-docker ps
SERVICE   IMAGE                   STATUS                    PORTS
agent     k5-day14-docker-agent   Up 39 seconds (healthy)   8000/tcp            <- không mở ra host
nginx     nginx:alpine            Up 39 seconds             0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
qdrant    qdrant/qdrant:v1.9.0    Up 50 seconds (healthy)   6333-6334/tcp
redis     redis:7-alpine          Up 49 seconds (healthy)   6379/tcp
```

Thứ tự khởi động khớp với `depends_on`: `redis` và `qdrant` báo **Healthy** xong thì `agent` mới **Started**, sau đó tới `nginx`.

```text
$ curl -i localhost/health                          # qua Nginx, cổng 80
HTTP/1.1 200 OK
Server: nginx                                       <- server_tokens off: không lộ version
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block
{"status":"ok","uptime_seconds":7.3,"version":"2.0.0","timestamp":"2026-11-26T07:57:58.986978"}

$ curl -i -X POST localhost/ask -H "Content-Type: application/json" -d '{"question": "Explain microservices"}'
HTTP/1.1 200 OK
Server: nginx
{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}

$ curl -m 3 localhost:8000/health                   # gọi thẳng agent, bỏ qua Nginx
000  (không kết nối được, đúng vì agent không có `ports:`)

$ seq 40 | xargs -P 40 -I{} curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost/ask ... | sort | uniq -c
     30 200
     10 429        <- Nginx rate limit (10 r/s, burst 20) chặn phần vượt

$ docker compose -p k5-day14-docker exec -T agent python -c "import socket; ..."
redis -> 172.19.0.3       <- DNS nội bộ: gọi nhau bằng tên service
qdrant -> 172.19.0.2
```

**Debug container:**
- `docker compose -p k5-day14-docker logs agent` xem log.
- `docker compose -p k5-day14-docker exec agent sh` vào shell trong container.
- `docker exec cp2-dev sh -c 'whoami'` chạy một lệnh lẻ.

Xong thì chạy `docker compose -p k5-day14-docker down` để trả cổng 80.

**Quan sát trong log agent:** dòng log có dạng `{"time":"...","level":"INFO","msg":"{"event": "request", "q_len": 2}"}`. Đây **không phải JSON hợp lệ**, vì `msg` lồng một chuỗi JSON có dấu `"` mà không được escape. Log aggregator sẽ không parse được dòng này. Muốn log JSON đúng chuẩn, phải dựng cả object rồi `json.dumps` một lần, thay vì chèn chuỗi JSON vào format string của `logging`. Điểm này sẽ được áp dụng ở Part 6 (tiêu chí Structured logging).

---

## Part 3: Cloud Deployment

> Theo CHECKPOINTS (CP6): deploy luôn **agent Part 6** (`06-lab-complete/`), không deploy app mẫu `03-cloud-deployment/`.

### Exercise 3.1: Railway deployment (nền tảng chính)

- **URL:** https://agent-production-e3fbaa11.up.railway.app
- **Nền tảng:** Railway. 2 service: `agent` (build từ `06-lab-complete/Dockerfile`) và `Redis`. `REDIS_URL=${{Redis.REDIS_URL}}`, `AGENT_API_KEY` đặt bằng biến môi trường trên Railway, không nằm trong code.
- **Chi tiết, lệnh test và ảnh:** xem [DEPLOYMENT.md](DEPLOYMENT.md) và [screenshots/test.png](screenshots/test.png)

4 lệnh tự kiểm ở CP6 (chỉ in mã HTTP):

```text
$ curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"
200
$ curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"
200
$ curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'
401
$ curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'
200
```

Response mẫu: `/health` → `{"status":"ok","environment":"production"}` (agent đọc đúng `ENVIRONMENT` từ Railway), `/ready` → `{"ready":true}` (đã kết nối Redis của Railway qua mạng nội bộ).

`python check_production_ready.py --public-url https://agent-production-e3fbaa11.up.railway.app` cho kết quả **PASS 12/12 · 44/44 checks**, trong đó có `✅ Public URL: GET .../health → 200`.

**Xem logs trên cloud:** `railway logs --service agent` (log runtime, mỗi dòng là một JSON) và `railway logs --service agent --build` (log build).

### Exercise 3.2: So sánh `railway.toml` và `render.yaml`

So sánh cả bản mẫu trong `03-cloud-deployment/` lẫn bản mình viết trong `06-lab-complete/`:

| Khía cạnh | `railway.toml` | `render.yaml` |
|---|---|---|
| **Bản chất** | File cấu hình **cho một service**: chỉ mô tả cách build và chạy service chứa file đó | **Blueprint (IaC) cho cả hạ tầng**: khai báo nhiều service trong một file (web + Redis/Key Value), Render tạo tất cả |
| **Định dạng** | TOML: `[build]`, `[deploy]` | YAML: danh sách `services:` |
| **Cách build** | `builder = "DOCKERFILE"` (bản của mình) hoặc `"NIXPACKS"` (bản mẫu: tự nhận diện Python, cần `startCommand`) | `runtime: docker` (bản của mình) hoặc `runtime: python` + `buildCommand` + `startCommand` (bản mẫu) |
| **Health check** | `healthcheckPath = "/health"`, `healthcheckTimeout`. Railway chỉ dùng health check **lúc deploy** để quyết định chuyển traffic sang bản mới | `healthCheckPath: /health`. Render dùng cho cả lúc deploy (zero-downtime) lẫn theo dõi khi đang chạy |
| **Restart** | `restartPolicyType = "ON_FAILURE"`, `restartPolicyMaxRetries = 3` | Render tự restart, không cấu hình trong file |
| **Biến môi trường / secret** | **Không** khai báo trong file. Đặt bằng CLI (`railway variables --set`) hoặc dashboard. Tham chiếu sang service khác bằng `${{Redis.REDIS_URL}}` | Khai báo **ngay trong file** qua `envVars`. Secret không ghi giá trị mà dùng `generateValue: true` (Render tự sinh, như `AGENT_API_KEY`) hoặc `sync: false` (nhập tay trên dashboard). Nối Redis bằng `fromService: {type: keyvalue, property: connectionString}` |
| **Redis** | Thêm riêng: `railway add --database redis`, rồi tham chiếu biến | Khai báo cùng file (`type: keyvalue`, bản mẫu dùng `type: redis` cũ), nên tạo cùng lúc với web service |
| **Port** | Railway cấp `PORT`. Dockerfile của mình đọc `${PORT:-8000}` | Render cũng cấp `PORT` (mặc định 10000). Cùng Dockerfile chạy được ở cả hai |
| **Vị trí file / thư mục con** | Đặt cạnh code. `railway up` chạy trong `06-lab-complete/` thì Railway tự dùng file đó | Blueprint phải nằm ở **gốc repo**. Code ở thư mục con thì dùng `rootDir: 06-lab-complete` |
| **Kích hoạt deploy** | `railway up` (upload từ máy) hoặc tự deploy khi push GitHub nếu đã nối repo | Push GitHub, Render đọc Blueprint, `autoDeploy: true` thì tự deploy khi push |

**Kết luận:**
- `railway.toml` chỉ cấu hình **build và chạy một service**, còn biến và hạ tầng quản lý trên nền tảng.
- `render.yaml` là **IaC đầy đủ**: một file mô tả cả service, database và biến (trừ giá trị secret), nên dễ tái tạo lại toàn bộ môi trường.
- Cả hai đều không ghi secret vào file, và cùng dùng được một Dockerfile đọc `PORT`. Đó là lợi ích của 12-Factor: một image chạy được trên nhiều nền tảng.

### Exercise 3.3: Pipeline Cloud Run (`03-cloud-deployment/production-cloud-run/`)

**CI/CD pipeline** trong `cloudbuild.yaml`, chạy khi push lên `main`:
1. **test**: dùng `python:3.11-slim` cài requirements và chạy `pytest tests/`. Test fail thì pipeline dừng.
2. **build**: build Docker image, gắn 2 tag `ai-agent:$COMMIT_SHA` và `ai-agent:latest`. Dùng `--cache-from latest` để tận dụng layer cache.
3. **push**: đẩy image lên Container Registry (`gcr.io/$PROJECT_ID`).
4. **deploy**: `gcloud run deploy` image **theo `$COMMIT_SHA`** (biết chính xác commit nào đang chạy, rollback dễ) lên `asia-southeast1`, 1–10 instance, 512 Mi / 1 CPU. `OPENAI_API_KEY` lấy từ **Secret Manager** (`--set-secrets`), không nằm trong code.

`waitFor` nối các bước thành chuỗi test → build → push → deploy. Bước nào fail thì không deploy.

**`service.yaml`** mô tả cùng service theo kiểu IaC (Knative):
- `minScale: 1` để tránh cold start, `maxScale: 10`, `containerConcurrency: 80`.
- Giới hạn tài nguyên: 1 CPU / 512 Mi.
- Secret (`OPENAI_API_KEY`, `AGENT_API_KEY`) qua `secretKeyRef`.
- `livenessProbe` gọi `/health`, `startupProbe` gọi `/ready`.

Đây cũng là cách phân biệt liveness và readiness như ở Part 5.

---

## Part 4: API Security

> Chạy trong Ubuntu 24.04 (WSL2), Python 3.12. Token JWT đã được che trong mọi output.

### Exercise 4.1: API Key authentication (`04-api-gateway/develop/app.py`)

- **Key được kiểm tra ở đâu?**
  - Trong hàm dependency `verify_api_key` (L39–54). Hàm đọc header `X-API-Key` qua `APIKeyHeader(name="X-API-Key", auto_error=False)` (L36).
  - Hàm được gắn vào endpoint bằng `_key: str = Depends(verify_api_key)` (L70). FastAPI chạy nó **trước** khi vào thân hàm `/ask`.
  - `/` và `/health` không gắn dependency này, nên vẫn public để platform health check được.
- **Sai key thì sao?** Thiếu header trả **401** "Missing API key". Có header nhưng sai trả **403** "Invalid API key." Request bị chặn ngay, không gọi tới LLM.
- **Rotate key thế nào?**
  - Key đọc từ env `AGENT_API_KEY` (L35), nên chỉ cần đặt giá trị mới trên platform rồi restart hoặc redeploy, không sửa code.
  - Muốn rotate không downtime: cho phép **hai key** song song trong một khoảng thời gian (ví dụ `AGENT_API_KEYS=old,new`), báo client chuyển sang key mới, rồi gỡ key cũ.
  - ⚠️ Default `"demo-key-change-in-production"` vẫn nằm trong code. Production nên fail fast nếu không đặt env.

```bash
AGENT_API_KEY=secret-key-123 python app.py
```

```text
### Không có key
$ curl -i -X POST localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 401 Unauthorized
{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

### Sai key
$ curl -i -X POST localhost:8000/ask -H "X-API-Key: wrong" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 403 Forbidden
{"detail":"Invalid API key."}

### Đúng key
$ curl -i -X POST localhost:8000/ask -H "X-API-Key: secret-key-123" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}
```

### Exercise 4.2: JWT authentication (`04-api-gateway/production/`)

**JWT flow** (`auth.py`):
1. `POST /auth/token` nhận `{username, password}`. `authenticate_user` kiểm tra với `DEMO_USERS` (L70–75), sai thì trả 401.
2. `create_token` (L35–43) ký payload `{sub, role, iat, exp = now + 60'}` bằng HS256 với `JWT_SECRET` lấy từ env.
3. Client gửi `Authorization: Bearer <token>` cho mỗi request.
4. `verify_token` (L46–67) gọi `jwt.decode`, tức là kiểm tra chữ ký và hạn dùng:
   - thiếu token → **401**;
   - token hết hạn → **401**;
   - chữ ký sai hoặc token giả → **403**;
   - hợp lệ → trả `{username, role}`.

   Server **không cần tra DB** cho mỗi request (stateless), vì thông tin user và role nằm sẵn trong token và được chữ ký bảo đảm.

```text
### Lấy token: POST /auth/token với student/demo123
{"access_token": "eyJhbGciOi...<đã che>", "token_type": "bearer", "expires_in_minutes": 60, "hint": "Include in header: Authorization: Bearer<đã che>"}

### Sai mật khẩu
HTTP/1.1 401 Unauthorized
{"detail":"Invalid credentials"}

### Không có token
$ curl -i -X POST localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "Explain JWT"}'
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer
x-content-type-options: nosniff
x-frame-options: DENY
x-xss-protection: 1; mode=block
referrer-policy: strict-origin-when-cross-origin
{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

### Token giả mạo (Bearer abc.def.ghi)
HTTP/1.1 403 Forbidden
{"detail":"Invalid token."}

### Có token hợp lệ
$ curl -i -X POST localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question": "Explain JWT"}'
HTTP/1.1 200 OK
{"question":"Explain JWT","answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic.","usage":{"requests_remaining":9,"budget_remaining_usd":2.1e-05}}
```

### Exercise 4.3: Rate limiting (`production/rate_limiter.py`)

- **Thuật toán:** **Sliding Window** (dạng sliding log).
  - Mỗi user có một `deque` chứa timestamp các request (L27).
  - Mỗi lần gọi, bỏ các timestamp cũ hơn `now − 60s` (L39–40).
  - Nếu số timestamp còn lại ≥ `max_requests` thì trả **429**, kèm `Retry-After` tính theo request cũ nhất trong cửa sổ (L45–62). Nếu chưa vượt thì `append(now)`.
  - Khác *fixed window* ở chỗ không bị dồn gấp đôi request ở ranh giới giữa hai phút.
- **Giới hạn:** user **10 req/phút**, admin **100 req/phút** (L86–87).
- **Bypass cho admin:** `app.py` L141 chọn `rate_limiter_admin if role == "admin" else rate_limiter_user`. Role nằm trong JWT nên client không tự sửa được. Tài khoản `teacher` có role `admin`.

Vòng lặp với token của `student`. Request thứ 1 đã dùng ở Exercise 4.2, nên vòng lặp chỉ còn 9 request được trả 200:

```text
$ for i in $(seq 1 15); do curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:8000/ask -H "Authorization: Bearer $TOKEN" ...; done
Request  1: 200
Request  2: 200
Request  3: 200
Request  4: 200
Request  5: 200
Request  6: 200
Request  7: 200
Request  8: 200
Request  9: 200
Request 10: 429
Request 11: 429
Request 12: 429
Request 13: 429
Request 14: 429
Request 15: 429

$ curl -i -X POST localhost:8000/ask -H "Authorization: Bearer $TOKEN" ... -d '{"question": "one more"}'
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795680586
retry-after: 59
{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}

### Admin (teacher), 15 request liên tiếp, không bị chặn
200 200 200 200 200 200 200 200 200 200 200 200 200 200 200
```

**Hạn chế:** state nằm trong RAM của process. Restart là reset, và chạy N instance thì mỗi user thực tế được N×10 req/phút. Production cần lưu trong Redis (sorted set: `ZREMRANGEBYSCORE` → `ZCARD` → `ZADD` → `EXPIRE`). Part 6 làm theo cách này.

### Exercise 4.4: Cost guard (`production/cost_guard.py`)

**Cách làm trong code mẫu:**
1. **Trước khi gọi LLM**, `check_budget(user)` (L60–91):
   - tổng chi phí toàn hệ thống ≥ `$10/ngày` → **503** (bảo vệ cả hệ thống);
   - chi phí của user ≥ `$1/ngày` → **402 Payment Required**;
   - đã dùng ≥ 80% → log cảnh báo.
2. **Sau khi gọi LLM**, `record_usage(user, input_tokens, output_tokens)` (L93–110) cộng token, quy ra tiền theo giá GPT-4o-mini ($0.15/1M input, $0.60/1M output) và cộng vào tổng chi phí hệ thống.
3. **Reset mỗi ngày:** `_get_record` tạo `UsageRecord` mới khi ngày (`%Y-%m-%d`) thay đổi (L53–58).

```text
$ curl localhost:8000/me/usage -H "Authorization: Bearer $TOKEN"
{"user_id":"student","date":"2026-11-26","requests":10,"input_tokens":40,"output_tokens":308,"cost_usd":0.000191,"budget_usd":1.0,"budget_remaining_usd":0.999809,"budget_used_pct":0.0}

# log server
INFO:cost_guard:Usage: user=teacher req=15 cost=$0.0003/1.0
```

**Demo 402:** mỗi request mock tốn khoảng $0.000019, nên với budget mặc định $1/ngày phải gọi khoảng 52 000 lần mới vượt. `student` lại bị giới hạn 10 req/phút, tức mất khoảng 3,6 ngày. Để thấy 402, mình hạ budget **lúc chạy** mà không sửa file, rồi dùng `teacher` (100 req/phút) để rate limit không chặn trước:

```bash
python -c "
import cost_guard; cost_guard.cost_guard.daily_budget_usd = 0.0001
import uvicorn, app; uvicorn.run(app.app, host='0.0.0.0', port=8000)"
```

```text
Request  1: 200
Request  2: 200
Request  3: 200
Request  4: 200
Request  5: 200
Request  6: 200
Request  7: 402
Request  8: 402
Request  9: 402
Request 10: 402

$ curl -i -X POST localhost:8000/ask -H "Authorization: Bearer $AT" ... -d '{"question": "Hello"}'
HTTP/1.1 402 Payment Required
{"detail":{"error":"Daily budget exceeded","used_usd":0.00011,"budget_usd":0.0001,"resets_at":"midnight UTC"}}

$ curl localhost:8000/me/usage -H "Authorization: Bearer $AT"
{"user_id":"teacher",...,"requests":6,"cost_usd":0.00011,"budget_usd":0.0001,"budget_remaining_usd":0,"budget_used_pct":110.0}

# log server
WARNING:cost_guard:User teacher at 89% budget          <- cảnh báo ở mốc 80%
INFO:cost_guard:Usage: user=teacher req=6 cost=$0.0001/0.0001
```

**Nhận xét:**
- Chi phí cuối cùng là **$0.00011**, vượt budget **$0.0001** (110 %). Lý do: `check_budget` chỉ so **chi phí đã tiêu** với budget, chưa cộng chi phí **ước tính của request sắp gọi**. Request thứ 6 lọt qua khi đang ở 89 %, rồi đẩy tổng vượt mức.
- Cách sửa: kiểm tra `current + estimated_cost > budget` trước khi gọi LLM, như trong bản Redis bên dưới. Với prompt dài, khoản vượt này có thể đáng kể.
- Budget đang hardcode trong code (`CostGuard(daily_budget_usd=1.0)`, L128), nên phải patch lúc runtime mới demo được. Production nên đọc từ env `DAILY_BUDGET_USD`. Part 6 làm theo cách này.

**Bản dùng Redis** (theo yêu cầu 4.4: $10/tháng mỗi user, reset đầu tháng):

```python
import redis
from datetime import datetime, timezone

r = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
MONTHLY_BUDGET_USD = float(os.getenv("MONTHLY_BUDGET_USD", "10"))

def check_budget(user_id: str, estimated_cost: float) -> bool:
    month_key = datetime.now(timezone.utc).strftime("%Y-%m")
    key = f"budget:{user_id}:{month_key}"            # key mới mỗi tháng → tự "reset đầu tháng"
    current = float(r.get(key) or 0)
    if current + estimated_cost > MONTHLY_BUDGET_USD:
        return False                                 # caller trả 402
    r.incrbyfloat(key, estimated_cost)               # atomic, an toàn khi nhiều instance
    r.expire(key, 32 * 24 * 3600)                    # tự dọn sau ~1 tháng
    return True
```

Lưu trong Redis thì mọi instance dùng chung một bộ đếm, và restart không mất dữ liệu. `INCRBYFLOAT` là thao tác atomic. Bước `GET` rồi so sánh vẫn có race nhỏ khi nhiều request đến cùng lúc. Muốn chặt chẽ tuyệt đối thì dùng Lua script hoặc `INCRBYFLOAT` trước, nếu vượt thì trừ lại.

**Vì sao cần cả rate limit lẫn cost guard:**
- Rate limit giới hạn **tốc độ** (số request/phút): chống spam, DoS và vòng lặp lỗi phía client.
- Cost guard giới hạn **tiền**: số request không phản ánh chi phí, vì một prompt 10 000 token đắt gấp hàng trăm lần một câu "Hello". Kẻ tấn công gửi chậm (9 req/phút, dưới rate limit) nhưng toàn prompt dài vẫn đốt được ngân sách. Ngược lại, chỉ có cost guard thì vẫn bị spam làm quá tải server.

**Lỗi phát hiện khi chạy:**
1. `app.py` L160 trả `"budget_remaining_usd": usage.total_cost_usd`, tức là số **đã tiêu** ($0.000021), không phải số **còn lại**. `/me/usage` mới tính đúng (`0.999809`).
2. `/me/usage` báo `budget_used_pct: 0.0` vì làm tròn 1 chữ số thập phân (0.019 % → 0.0).
3. Middleware `security_headers` xoá header `server`, nhưng response vẫn có `server: uvicorn`, vì uvicorn thêm header này ở tầng server, sau middleware. Muốn ẩn phải chạy uvicorn với `--no-server-header` (hoặc `server_header=False`).
4. `rate_limiter` và `cost_guard` lưu trong RAM, nên không dùng được khi scale ra nhiều instance (xem Part 5 và Part 6).

**Validation:** `{"question": ""}` trả **422**, do Pydantic `Field(min_length=1, max_length=1000)`. Câu hỏi quá dài cũng bị chặn, tránh prompt khổng lồ đốt tiền.

---

## Part 5: Scaling & Reliability

> Chạy trong Ubuntu 24.04 (WSL2). Stack Docker dùng `-p k5-day14-scaling`.

### Exercise 5.1: Health checks (`05-scaling-reliability/develop/app.py`)

| | `GET /health`: **liveness** | `GET /ready`: **readiness** |
|---|---|---|
| Câu hỏi | "Process còn sống không?" | "Có nên gửi traffic vào instance này không?" |
| Ai dùng | Platform hoặc orchestrator (Docker `HEALTHCHECK`, K8s `livenessProbe`, Railway) | Load balancer, K8s `readinessProbe` |
| Fail thì sao | **Restart** container | **Ngừng route** traffic vào, **không** restart |
| Nên kiểm tra | Chỉ bản thân process (uptime, memory) | Dependency (Redis, DB), cờ `_is_ready` (đang startup hoặc shutdown thì 503) |

**Vì sao phải tách hai endpoint:** nếu `/health` kiểm tra luôn cả Redis, thì khi Redis chập chờn, platform sẽ restart **tất cả** instance cùng lúc. Restart không chữa được Redis mà chỉ gây sập dây chuyền. Đúng ra, khi Redis hỏng thì `/health` vẫn 200 (process ổn) còn `/ready` trả 503 (tạm thời đừng gửi traffic). Part 6 cũng yêu cầu đúng như vậy.

```text
$ curl -i localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":0.7,"version":"1.0.0","environment":"development","timestamp":"2026-11-26T08:14:52.225473+00:00","checks":{"memory":{"status":"ok","used_percent":13.7}}}

$ curl -i localhost:8000/ready
HTTP/1.1 200 OK
{"ready":true,"in_flight_requests":1}
```

### Exercise 5.2: Graceful shutdown

**Cách code mẫu làm:**
- **Middleware `track_requests`** (L72–81) đếm `_in_flight_requests`: tăng khi request vào, giảm trong `finally` khi request xong.
- **Khi nhận SIGTERM:**
  1. uvicorn **ngừng nhận kết nối mới**;
  2. chạy phần shutdown của `lifespan`: đặt `_is_ready = False` (`/ready` → 503), rồi **chờ `_in_flight_requests == 0`**, tối đa 30 giây (L54–66);
  3. kết thúc process.
- **Thêm ở uvicorn:** `timeout_graceful_shutdown=30` (L198).
- **`handle_sigterm`** (L175–187) chỉ ghi log. Lưu ý: uvicorn cài handler riêng khi `run()`, đè lên handler này, nên dòng log của nó xuất hiện **sau cùng**.

**Test:** gửi 2 request song song (mock mất 3 giây mỗi request), sau 0.5 giây gửi `kill -SIGTERM <PID>`.

```text
[t=0.5s] kill -SIGTERM 3573
### Request MỚI sau SIGTERM
curl localhost:8000/health -> HTTP 000 (connection refused)     <- không nhận request mới
request 1 -> HTTP 200 sau 6.10s
request 2 -> HTTP 200 sau 6.10s                                  <- request đang chạy vẫn hoàn thành

INFO:     Shutting down
INFO:     127.0.0.1:41866 - "POST /ask?question=hello HTTP/1.1" 200 OK
INFO:     127.0.0.1:41882 - "POST /ask?question=hello HTTP/1.1" 200 OK
INFO:     Waiting for application shutdown.
INFO 🔄 Graceful shutdown initiated...
INFO ✅ Shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [3573]
INFO Received signal 15 — uvicorn will handle graceful shutdown
```

**⚠️ Phát hiện khi chạy:**
- Mỗi request mất **6 giây** chứ không phải 3. Trong **lần chạy đầu tiên**, request 2 còn bị cắt:
  ```text
  request 1 -> HTTP 200 sau 3.02s
  request 2 -> HTTP 000 sau 3.01s     <- mất request!
  ```
- **Nguyên nhân:** `async def ask_agent` (L94) gọi `ask()`, mà `ask()` dùng `time.sleep(3)` là lệnh **chặn**. Lệnh này chặn luôn event loop của uvicorn.
  - Hai request bị **xếp hàng**, chạy lần lượt (3 s + 3 s = 6 s), thay vì song song.
  - Nếu SIGTERM tới khi request 2 mới nằm trong hàng đợi TCP và loop chưa kịp đọc, uvicorn coi kết nối đó là "chưa có request" và đóng luôn.
  - Kết quả là graceful shutdown **không ổn định**: lúc 2/2, lúc 1/2.
- **Cách sửa:** khai báo `def ask_agent(...)` (không có `async`) để FastAPI chạy hàm trong threadpool, hoặc `await asyncio.to_thread(ask, question)`. Bài học: trong `async def` không được gọi I/O chặn (`time.sleep`, `requests`, driver DB đồng bộ).

### Exercise 5.3: Stateless design (`production/app.py`)

**Anti-pattern:** `conversation_history = {}` để trong RAM. Mỗi instance có vùng nhớ riêng:

```text
Instance A: user X turn 1 → lưu history trong RAM của A
Instance B: user X turn 2 → RAM của B trống → mất ngữ cảnh ❌
Restart/deploy/scale-down → RAM mất → mất hết session ❌
```

**Cách code mẫu làm stateless:**
- Toàn bộ session lưu trong **Redis**, với key `session:<id>` và TTL 3600 s (`save_session` dùng `SETEX`, L59–65).
- Instance chỉ giữ `INSTANCE_ID` để minh hoạ, ngoài ra không giữ gì. Request nào vào instance nào cũng đọc được session.
- Nếu không kết nối được Redis, app **tự fallback** về dict trong RAM (L42–45) và chỉ in cảnh báo. Đây chính là anti-pattern. Production nên **fail** (`/ready` → 503) thay vì âm thầm chạy chế độ không scale được.

### Exercise 5.4: Load balancing với Nginx

```bash
cd 05-scaling-reliability/production
touch .env.local
docker compose -p k5-day14-scaling up -d --build --scale agent=3
```

```text
NAME                       STATUS                    PORTS
k5-day14-scaling-agent-1   Up (healthy)              8000/tcp
k5-day14-scaling-agent-2   Up (healthy)              8000/tcp
k5-day14-scaling-agent-3   Up (healthy)              8000/tcp
k5-day14-scaling-nginx-1   Up                        0.0.0.0:8080->80/tcp
k5-day14-scaling-redis-1   Up (healthy)              6379/tcp
```

10 request `POST /chat` qua Nginx (cổng **8080**):

```text
Request 1 -> instance-b60e09 | storage = redis
Request 2 -> instance-734c23 | storage = redis
Request 3 -> instance-4cf156 | storage = redis
Request 4 -> instance-b60e09 | storage = redis
Request 5 -> instance-734c23 | storage = redis
Request 6 -> instance-4cf156 | storage = redis
Request 7 -> instance-b60e09 | storage = redis
Request 8 -> instance-734c23 | storage = redis
Request 9 -> instance-4cf156 | storage = redis
Request 10 -> instance-b60e09 | storage = redis

$ docker compose -p k5-day14-scaling logs agent | grep "POST /chat" | awk '{print $1}' | sort | uniq -c
      3 agent-1
      3 agent-2
      4 agent-3
```

**Nhận xét:**
- Nginx chia request theo **round-robin** đúng thứ tự b60e09 → 734c23 → 4cf156 → lặp lại, phân bổ 4/3/3.
- `upstream agent_cluster { server agent:8000; }`: tên `agent` được DNS nội bộ của Docker phân giải ra **3 IP** của 3 container, nên Nginx có 3 backend.
- `proxy_next_upstream error timeout http_503` + `tries 3`: nếu một instance lỗi hoặc trả 503, Nginx thử tiếp instance khác.
- Agent không mở port ra host, chỉ truy cập được qua Nginx.

### Exercise 5.5: Test stateless

```text
$ python3 test_stateless.py
Session ID: 0a36dee0-2602-4afa-9793-c8a40a641c87

Request 1: [instance-734c23]   Q: What is Docker?
Request 2: [instance-4cf156]   Q: Why do we need containers?
Request 3: [instance-b60e09]   Q: What is Kubernetes?
Request 4: [instance-734c23]   Q: How does load balancing work?
Request 5: [instance-4cf156]   Q: What is Redis used for?
------------------------------------------------------------
Total requests: 5
Instances used: {'instance-734c23', 'instance-4cf156', 'instance-b60e09'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
  [user]: What is Docker?...
  [assistant]: Container là cách đóng gói app để chạy ở mọi nơi. Build once...
  ... (đủ 5 cặp user/assistant)
✅ Session history preserved across all instances via Redis!
```

**Giải thích kết quả:**
- 5 câu hỏi trong **cùng một session** được **cả 3 instance khác nhau** xử lý, xoay vòng theo round-robin.
- Đọc lại history thì vẫn đủ **10 message** (5 câu hỏi + 5 câu trả lời), đúng thứ tự.
- Không instance nào giữ toàn bộ cuộc hội thoại trong RAM. Mỗi instance đọc history từ Redis, thêm message, rồi ghi lại.
- Nếu lưu trong RAM, history sẽ bị chia nhỏ ra 3 nơi: mỗi instance chỉ thấy khoảng 1/3 cuộc hội thoại.

**Thêm: tắt hẳn một instance giữa cuộc hội thoại.** Script mặc định không tự kill instance nào, nên mình tự làm bước này:

```text
session_id = 32801723-...  (turn 1 gửi trước khi kill)
docker stop /k5-day14-scaling-agent-1
turn 2 sau khi kill -> instance-b60e09   (response ghi "turn": 3)
turn 3 sau khi kill -> instance-734c23   (response ghi "turn": 4)
turn 4 sau khi kill -> instance-b60e09   (response ghi "turn": 5)
history count = 8
   user : turn 1 truoc khi kill
   user : turn 2 sau khi kill
   user : turn 3 sau khi kill
   user : turn 4 sau khi kill

$ docker exec <redis> redis-cli TTL session:32801723-...
3599
```

**Giải thích:**
- Mất một instance thì Nginx chuyển traffic sang 2 instance còn lại. Session **không mất gì** vì nằm trong Redis (TTL 1 giờ), không nằm trong container đã chết.
- Đây là điều kiện để scale ngang, rolling deploy và tự động thay instance hỏng.

**Lỗi nhỏ phát hiện:**
1. Trường `turn` trong response bị lệch +1: gửi câu thứ 2 nhưng response ghi `"turn": 3`. Nguyên nhân: `chat()` gọi `append_to_history(user)` **trước**, rồi mới đếm số message user trong history và **+1** thêm lần nữa (L154).
2. `append_to_history` làm theo kiểu đọc, sửa, ghi (`GET` → sửa → `SETEX`), không atomic. Hai request cùng session tới hai instance cùng lúc có thể ghi đè message của nhau. Nên dùng Redis list (`RPUSH` + `LTRIM`) hoặc `WATCH/MULTI`.

**Dọn stack:** `docker compose -p k5-day14-scaling down` để trả cổng 8080.

### Checkpoint 5 — tự trả lời

- **Liveness khác readiness ở đâu?** Xem bảng ở Exercise 5.1. Liveness fail thì platform restart. Readiness fail thì load balancer ngừng gửi traffic vào (không restart). Redis chết: `/health` vẫn 200, `/ready` trả 503.
- **Vì sao state trong RAM hỏng khi chạy nhiều instance?**
  - Mỗi instance có RAM riêng, mà load balancer gửi các request liên tiếp của cùng một user tới các instance khác nhau. Mỗi instance chỉ thấy một phần state.
  - State còn mất khi restart hoặc deploy.
  - Bộ đếm rate limit và chi phí cũng bị nhân N: N instance thì mỗi user được N× giới hạn.
  - Đưa state ra Redis dùng chung thì mọi instance như nhau, thêm hoặc bớt instance tuỳ ý.

---

## Part 6: Final Project — output `check_production_ready.py`

Chạy bản **đầy đủ** (có Docker) trong `06-lab-complete/`, trên Ubuntu 24.04 WSL2 (`os.name == posix`), Docker 29.9.3:

```text
$ python check_production_ready.py
...
================================================================
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
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

Một số dòng chi tiết đáng chú ý trong output:

```text
✅ Docker: image < 500 MB — 238 MB
✅ Khi chạy: Redis mất kết nối → /ready 503 (còn /health vẫn 200)
✅ Khi chạy: vượt giới hạn → 429 — 429 sau 5 request thành công (RATE_LIMIT_PER_MINUTE=5)
✅ Khi chạy: state nằm trong Redis — restart agent vẫn bị 429
✅ Khi chạy: hết budget → 402 — 402 ở request thứ 1
✅ Khi chạy: mọi dòng log của app là JSON hợp lệ — 17 dòng JSON
✅ Khi chạy: SIGTERM lúc đang có request → request vẫn 200, process tự thoát — exit code 0
✅ Docker: `docker stop` tắt êm (exit code 0, < 10s) — exit code 0, 0.5s
```

### Thiết kế từng phần

| File | Cách làm | Lý do |
|---|---|---|
| `app/config.py` | `dataclass` + `os.getenv` cho `PORT`, `ENVIRONMENT`, `AGENT_API_KEY`, `REDIS_URL`, `RATE_LIMIT_PER_MINUTE`, `DAILY_BUDGET_USD`, `LOG_LEVEL` | 12-Factor. `AGENT_API_KEY` mặc định rỗng, nên quên đặt thì **mọi** request đều bị từ chối (fail closed) |
| `app/auth.py` | `APIKeyHeader("X-API-Key")` + `hmac.compare_digest`. Thiếu hoặc sai key đều trả **401** | So sánh thời gian hằng để chống timing attack |
| `app/redis_client.py` | `redis.from_url(..., socket_connect_timeout=2, socket_timeout=2)`, **không ping lúc import** | App khởi động được khi Redis chết, và `/ready` trả 503 nhanh thay vì treo |
| `app/rate_limiter.py` | Sorted set `ratelimit:<sha256(key)[:16]>`. Một transaction gồm `ZREMRANGEBYSCORE` → `ZADD` → `ZCARD` → `EXPIRE`. Vượt giới hạn thì `ZREM` request vừa thêm và trả **429** + `Retry-After` | Dùng chung giữa 2 worker và nhiều instance, còn sau restart. Không lưu API key thô trong Redis |
| `app/cost_guard.py` | Key `cost:<YYYY-MM-DD>` (UTC), `INCRBYFLOAT` + `EXPIRE` 2 ngày. `check_budget(estimated_cost)` so **đã tiêu + ước tính request sắp gọi** với budget, vượt thì trả **402** | Sửa lỗi đã thấy ở Part 4: code mẫu chỉ so số đã tiêu nên request cuối lọt qua và đẩy tổng vượt budget |
| `app/main.py` | `JsonFormatter` (dựng dict rồi `json.dumps` một lần), middleware ghi log mỗi request (`method`, `path`, `status`, `ms`). `/health` luôn 200. `/ready` trả 503 khi Redis chết hoặc đang shutdown. Exception handler trả 500 không kèm stack trace | Sửa lỗi log JSON lồng chuỗi đã thấy ở Part 2 |
| `/ask` | **`def`** (không `async`). Thứ tự: auth → rate limit → budget → LLM → `record_cost` | Áp dụng bài học Part 5: `time.sleep` của LLM chạy trong threadpool, không chặn event loop, nên request đang chạy vẫn xong khi nhận SIGTERM |
| Graceful shutdown | `lifespan`: sau `yield` thì đặt `ready=False`, ghi log, đóng Redis. **Không** tự đăng ký handler SIGTERM | uvicorn đã ngừng nhận request mới và chờ request đang chạy trước khi vào phần sau `yield`. Tự viết handler dễ đè mất handler của uvicorn |
| `Dockerfile` | Builder (`pip install --prefix=/install`) → runtime `python:3.11-slim`, user `app` non-root, `HEALTHCHECK` gọi `/health` theo `$PORT`. `CMD ["sh","-c","exec uvicorn ... --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2}"]` | `exec` làm uvicorn thành **PID 1**, nên nhận SIGTERM và tắt trong 0.5 s. Image 238 MB |
| `.dockerignore` | `.env`, `.env.*` (giữ `.env.example`), `__pycache__/`, `venv/`, `.git/`, `*.md`, `benchmark-observability/`, script chấm | Không đưa secret vào image, build context nhỏ |
| `docker-compose.yml` | `agent` (build) + `redis:7-alpine` (healthcheck, volume AOF). `REDIS_URL=redis://redis:6379/0`, `depends_on: service_healthy`, `env_file: .env` (`required: false`) | Một lệnh chạy được toàn stack. Secret nằm trong `.env` đã gitignore |
| `railway.toml` / `render.yaml` | Railway: `builder = "DOCKERFILE"`, `healthcheckPath = "/health"`. Render: `type: web`, `runtime: docker`, `healthCheckPath: /health`, `AGENT_API_KEY` dùng `generateValue: true`, Redis dùng `keyvalue` + `fromService` | Cả hai đều không ghi secret vào file |

### Test thêm bằng `docker compose` (stack thật: agent + redis)

```text
$ docker compose -p k5-day14-final up -d --build      # .env tạm: AGENT_API_KEY=<ngẫu nhiên>, RATE_LIMIT_PER_MINUTE=5
SERVICE   STATUS                    PORTS
agent     Up (healthy)              0.0.0.0:8000->8000/tcp
redis     Up (healthy)              6379/tcp
k5-day14-final-agent:latest  249MB

GET  /health                         → 200  {"status":"ok","environment":"development"}
GET  /ready                          → 200  {"ready":true}
POST /ask  không có key              → 401  {"detail":"Invalid or missing API key"}
POST /ask  sai key                   → 401  {"detail":"Invalid or missing API key"}
POST /ask  đúng key                  → 200  {"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được."}
POST /ask  {}  (thiếu question)      → 422  {"detail":[{"type":"missing","loc":["body","question"],"msg":"Field required",...}]}
POST /ask  JSON hỏng                 → 422  {"detail":[{"type":"json_invalid",...,"msg":"JSON decode error",...}]}
POST /ask  {"question":""}           → 422  {"detail":[{"type":"string_too_short",...}]}
Rate limit 5/phút: 200 200 200 200 429   (request đúng-key ở trên là request thứ 1)
  HTTP/1.1 429 Too Many Requests
  retry-after: 60
  {"detail":{"error":"Rate limit exceeded","limit":5,"window_seconds":60}}

docker compose stop redis   → /health 200, /ready 503
docker compose start redis  → /ready 200   (tự hồi phục, không cần restart agent)

redis-cli --scan → cost:2026-11-26, ratelimit:d7bb287408e52509   (không lưu API key thô)

docker compose stop agent → tắt trong 0.63 s, exit code 0
  INFO: Waiting for application shutdown. / Application shutdown complete. / Finished server process [8]
```

Input sai đều trả **422** với thông báo rõ ràng, không lộ stack trace. Lỗi không lường trước được exception handler đổi thành `500 {"detail":"Internal server error"}`, còn chi tiết chỉ ghi vào log JSON.

Log agent (mỗi dòng là một JSON object):

```json
{"ts": "2026-11-26T09:01:13.405442+00:00", "lvl": "INFO", "logger": "agent", "msg": "request", "event": "request", "method": "GET", "path": "/ready", "status": 503, "ms": 4003.7}
{"ts": "2026-11-26T09:01:17.763861+00:00", "lvl": "INFO", "logger": "agent", "msg": "request", "event": "request", "method": "GET", "path": "/ready", "status": 200, "ms": 2.9}
```

Khi Redis chết, `/ready` mất khoảng 4 s mới trả 503, vì mỗi lần thử kết nối chờ tối đa `socket_connect_timeout=2`. Vẫn nhanh hơn nhiều so với timeout mặc định, và nằm trong giới hạn 15 s của script chấm.

---

## AI log

| Công cụ | Dùng vào việc gì |
|---|---|
| Claude Code | CP1: chạy bản develop và production, thu output curl, phân tích anti-pattern và lỗi encoding trên Windows, soạn nháp Part 1. Đã tự đọc lại và đối chiếu với code. |
| Claude Code | CP2: build 2 image, đo dung lượng, chạy container develop, đọc Dockerfile và compose để vẽ sơ đồ, soạn nháp Part 2. |
| Claude Code | CP3: viết script test chạy trong WSL (401/403/200, JWT, vòng lặp 429, admin), đọc `auth.py`, `rate_limiter.py`, `cost_guard.py`, soạn nháp Part 4 và phát hiện 3 lỗi nhỏ trong code mẫu. |
| Claude Code | CP4: chạy test graceful shutdown (SIGTERM giữa 2 request), stack scale 3 agent + Nginx + Redis, `test_stateless.py` và thí nghiệm tắt một instance. Phân tích lỗi event loop bị chặn và các lỗi nhỏ, soạn nháp Part 5. |
| Claude Code | CP5: đọc `check_production_ready.py` để hiểu tiêu chí, viết code cho các TODO trong `06-lab-complete/` (config, auth, Redis, rate limiter, cost guard, main, Dockerfile, compose, .dockerignore, .env.example, railway.toml, render.yaml), chạy script chấm (PASS 12/12) và test bằng docker compose. Áp dụng các lỗi đã phát hiện ở Part 2, 4 và 5. Mình đã đọc lại từng file và có thể giải thích từng quyết định thiết kế. |
| Claude Code | CP6: hướng dẫn các bước deploy Railway. Mình **tự** đăng nhập, tạo project, Redis, service, đặt biến (gồm API key) và chạy 4 lệnh tự kiểm. Claude viết phần so sánh `railway.toml`/`render.yaml`, pipeline Cloud Run và soạn `DEPLOYMENT.md`. |
