# Benchmark & Observability Report — Day 14

> Mọi con số lấy từ report JSON / CSV / screenshot thật, không ước lượng.
> JSON của Part 7 Bước 1 nằm trong `06-lab-complete/benchmark-observability/develop/reports/` (bản copy khi nộp: `reports/`).

**Họ tên:** Nguyễn Công Tú (2A202604008)
**Ngày chạy:** 2026-11-26
**Môi trường:** Laptop Intel Core i5-1135G7 (4 nhân / 8 luồng), Windows 11 + WSL2 Ubuntu 24.04 (8 vCPU, 5.7 GB RAM cấp cho WSL), Python 3.12.3, Docker Desktop 29.9.3. Agent và load generator chạy **trên cùng một máy**.

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < **500** ms | LLM mock mất khoảng 120 ms cộng jitter tối đa 60 ms, p50 đo được khoảng 190 ms. 500 ms để dư khoảng 2,5 lần cho jitter, retrieval và hàng đợi. Request "slow" (+1500 ms) chỉ được phép chiếm < 5 %: vượt 5 % thì p95 nhảy lên khoảng 1,7 s và SLO fail (đúng như mục 2.2 với `MOCK_SLOW_RATE=0.1`). Đặt theo **p95** chứ không theo average, vì average che mất tail latency (xem 2.2) |
| Error rate `/ask` | < **2** % | Upstream LLM có tỉ lệ lỗi nền khoảng 1 % (`MOCK_ERROR_RATE=0.01`), nên 2 % là ngưỡng "bình thường × 2". Vượt mức này nghĩa là có sự cố thật (provider sập, quá tải), không còn là nhiễu |
| Accuracy trên golden dataset | ≥ **85** % **và không regression** | Quality gate trong CI (`--min-accuracy 0.85`). Ngoài ngưỡng tuyệt đối, **không câu nào đang đúng ở baseline được phép sai** ở phiên bản mới. Mục tiêu nội bộ là 100 % (bản v2 sau khi sửa KB đạt 23/23) |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

Lệnh: `python load_test.py --url http://localhost:8002 --requests 300 --concurrency <c> --out reports/load_v2_c<c>.json` (agent v2, cấu hình mặc định: `MOCK_SLOW_RATE=0.03`, `MOCK_ERROR_RATE=0.01`). SLO kiểm tra: p95 ≤ 500 ms, error rate ≤ 2 %.

### 2.1 Concurrency vs latency (agent v2)

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.86 | 189.8 | 220.1 | 308.6 | 0.33 % (1 × 503) | ✅ PASS |
| 10 | 35.23 | 195.1 | 223.2 | 1704.8 | 0.67 % (2 × 503) | ✅ PASS |
| 50 | 110.13 | 193.6 | 253.3 | 1713.6 | 1.67 % (5 × 503) | ✅ PASS |
| 100 | 64.86 | 468.1 | 2989.7 | 3223.7 | 0.67 % (2 × 503) | ❌ FAIL (p95) |

Nguồn: `load_v2_c1.json`, `load_v2_c10.json`, `load_v2_c50.json`, `load_v2_c100.json` (mỗi file 300 request).

**Nhận xét: RPS có tăng tuyến tính theo concurrency không? Vì sao?**

- **Từ 1 lên 10: gần tuyến tính** (4.86 lên 35.23 RPS, khoảng ×7,2), p50 gần như không đổi (189.8 → 195.1 ms).
  - Theo định luật Little: RPS ≈ concurrency / latency. Với c=10 và latency khoảng 0,2 s, lý thuyết là khoảng 50 RPS.
  - Thực tế thấp hơn một chút, vì vài request "slow" (1,7 s) giữ chỗ của worker.
  - Agent dùng `async` (`asyncio.sleep` trong mock), nên 10 request chờ LLM song song mà không chặn nhau.
- **Từ 10 lên 50: tăng chậm lại** (35.23 → 110.13, chỉ ×3,1 cho ×5 concurrency). p50 vẫn khoảng 194 ms, nhưng p95 bắt đầu nhích lên (223 → 253 ms). Agent bắt đầu tốn CPU cho việc parse JSON, Pydantic, ghi log và cập nhật metrics.
- **Từ 50 lên 100: RPS *giảm* (110.13 → 64.86), p50 tăng gấp 2,4 lần (468 ms), p95 lên 2989.7 ms, SLO fail.** Hệ thống đã **bão hoà**:
  - Agent chạy **1 process uvicorn = 1 event loop = 1 nhân CPU**. Phần việc tốn CPU của mỗi request cộng dồn lại, nên event loop xử lý không kịp. Request phải **xếp hàng** chờ loop, chứ không phải chờ LLM.
  - Load generator (httpx, 100 kết nối) chạy trên **cùng máy** nên tranh CPU với agent.
  - Vượt điểm bão hoà, tăng tải không làm tăng throughput mà chỉ làm tăng latency, và thậm chí làm giảm throughput (thrashing, tranh chấp tài nguyên).
  - Muốn tăng tiếp phải **scale ngang**: thêm worker hoặc instance sau load balancer (Part 5), hoặc giới hạn concurrency, ví dụ trả 429/503 sớm thay vì để hàng đợi phình to.
- **p99 khoảng 1,7 s ở c=10 và c=50** là do 3 % request "slow" của mock (+1500 ms). Lần chạy c=1 có p99 = 308.6 ms (max 1693.8 ms) vì ngẫu nhiên chỉ có rất ít request slow rơi vào 300 mẫu. Với 300 mẫu, p99 chỉ dựa trên khoảng 3 request cuối, nên dao động mạnh giữa các lần chạy.

### 2.2 Tail latency

Lệnh: khởi động lại agent v2 với `MOCK_SLOW_RATE=<x>`, rồi `python load_test.py --url http://localhost:8002 --requests 300 --concurrency 20 --out reports/load_v2_slow_<x>.json`.

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 188.9 | 216.2 | 225.0 |
| 0.03 (mặc định) | 246.8 | 229.9 | 1703.2 |
| 0.1 | 356.7 | 1690.0 | 1720.0 |

Nguồn: `load_v2_slow_0.json`, `load_v2_slow_0.03.json`, `load_v2_slow_0.1.json`.

**Nhận xét: chỉ số nào nhạy nhất với request chậm? Vì sao SLO không nên đặt theo average?**

- **Từ 0 lên 0.03:**
  - **p99 nhạy nhất**: tăng gấp 7,6 lần (225.0 → 1703.2 ms). Chỉ cần ≥ 1 % request chậm là p99 "rơi" vào nhóm request slow.
  - **p95 gần như không đổi** (216.2 → 229.9 ms), vì 3 % slow < 5 %.
  - **avg chỉ tăng 31 %** (188.9 → 246.8 ms). Nhìn avg thì trông vẫn "ổn", trong khi 3 % người dùng phải chờ khoảng 1,7 s.
- **Từ 0.03 lên 0.1:** khi tỉ lệ slow vượt 5 %, **p95 nhảy vọt** (229.9 → 1690.0 ms, gấp 7,4 lần), còn avg chỉ lên 356.7 ms.
- **Kết luận:**
  - Average trộn 97 % request nhanh với 3 % request rất chậm thành một con số "trung bình" mà **không người dùng nào thật sự trải nghiệm**. Nó che mất tail latency, đúng phần làm người dùng khó chịu và bỏ đi.
  - Percentile trả lời đúng câu hỏi SLO cần: "X % người dùng được phục vụ nhanh hơn ngưỡng nào?".
  - Vì vậy SLO đặt theo **p95** (cảnh báo chính) và theo dõi thêm **p99** (sớm phát hiện tail).
  - p50 hầu như không đổi trong cả 3 lần (187.4 / 193.7 / 193.1 ms), nên p50 cũng không phát hiện được vấn đề này.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

Golden dataset: 23 câu (`eval/golden_dataset.jsonl`). Một câu tính "pass" khi trả lời chứa ≥ 60 % keyword mong đợi.

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.35 % (1/23) | 91.3 % (21/23) | **100 % (23/23)** |
| Avg tokens / câu | 57.0 | 109.0 | 113.7 |
| Tổng cost (USD) | 0.000403 | 0.00076 | 0.00079 |
| Cost / câu đúng (USD) | 0.00040335 | 0.00003619 | 0.00003436 |
| Quality gate | ❌ FAIL (≥ 80 %) | ✅ PASS (≥ 80 %, 0 regression so với v1) | ✅ PASS (≥ 100 %, 0 regression so với v2) |

Nguồn: `eval_v1.json`, `eval_v2.json` (`--baseline reports/eval_v1.json`), `eval_v2_fixed.json` (`--min-accuracy 1.0 --baseline reports/eval_v2.json`). Không có lỗi HTTP nào trong 3 lần eval (`errors: 0`).

**Những câu v2 trả lời sai ban đầu và cách sửa:**

| ID | Keyword bị thiếu | Đã sửa gì |
|---|---|---|
| `security-02`: "JWT khác gì API key trong xác thực?" | `token`, `hết hạn`, `chữ ký` | KB chưa có tài liệu nào chứa từ khoá của câu này, nên retrieval trả rỗng và agent rơi về câu trả lời chung chung. Đã thêm entry `(["jwt"], "API key là chuỗi bí mật cố định, không tự hết hạn. JWT là token có chữ ký số, chứa thông tin user và thời điểm hết hạn; server xác minh chữ ký mà không cần tra database.")` |
| `scaling-03`: "Kubernetes HPA scale dựa trên những chỉ số nào?" | `cpu`, `memory`, `custom metrics` | Tương tự: KB không có tài liệu về HPA. Đã thêm entry `(["hpa", "kubernetes", "autoscal"], "Kubernetes HPA (Horizontal Pod Autoscaler) scale số pod dựa trên CPU, memory hoặc custom metrics như RPS hay độ dài hàng đợi.")` |

- Sửa trong `develop/utils/mock_llm.py` và chép sang `production/utils/mock_llm.py` để stack Docker ở Bước 2 cũng đạt 100 %.
- Từ khoá mới (`jwt`, `hpa`, `kubernetes`, `autoscal`) **không xuất hiện trong 21 câu còn lại**, nên không "cướp" retrieval của câu khác. Kết quả: **0 regression**, 2 câu được cải thiện.

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:**

- **Chất lượng:**
  - v1 không có retrieval, chỉ biết vài câu chung chung, nên đúng 1/23.
  - v2 có RAG nên đúng 21/23, sửa KB xong thì 23/23.
  - Với agent hỏi đáp kỹ thuật, khác biệt này mang tính quyết định.
- **Chi phí:**
  - v2 tốn khoảng **1,9 lần token mỗi câu** (57.0 → 109.0), vì prompt có thêm tài liệu được retrieve, nên tổng cost tăng 1,9 lần (0.000403 → 0.00076 USD).
  - Nhưng **cost cho mỗi câu ĐÚNG** của v2 chỉ bằng **khoảng 1/11** v1 (0.00003619 so với 0.00040335 USD).
  - "Cost / câu" khiến v1 trông rẻ hơn, nhưng tiền v1 bỏ ra chủ yếu để mua câu trả lời sai, tức là lãng phí.
  - Vì vậy **cost / câu đúng** mới là chỉ số công bằng để so 2 phiên bản.
- **Latency:**
  - v2 thêm bước retrieval (khoảng 20 ms) và prompt dài hơn (+0,2 ms/token), nên p50 tăng khoảng 30 ms (154.8 → 186.3 ms).
  - p95 của `eval_v2.json` là 1560.8 ms, còn `eval_v2_fixed.json` là 220.3 ms. Chênh lệch này **không do sửa KB** mà do request slow ngẫu nhiên (3 %) của mock rơi vào lần chạy eval v2. Với chỉ 23 mẫu, p95 rất nhạy với 1–2 request chậm.
- **Kết luận:** v2 đáng giá. Chi phí và latency tăng nhẹ, đổi lại chất lượng tăng mạnh và cost / câu đúng giảm 11 lần.

**Quality gate trong CI (Exercise 7.3):**
- `run_eval.py` trả **exit code 1** khi `accuracy < --min-accuracy` hoặc có regression so với `--baseline`, và **exit code 0** khi đạt. Ví dụ: v1 với ngưỡng 0.8 → ❌ FAIL (exit 1); v2 đã sửa với ngưỡng 0.85 → exit 0.
- Job CI: [`.github/workflows/eval.yml`](.github/workflows/eval.yml).
  - Chạy khi có pull request đụng vào `develop/`.
  - Cài requirements, bật agent v2 (`MOCK_ERROR_RATE=0` để gate không fail vì lỗi ngẫu nhiên), rồi chạy `python eval/run_eval.py --min-accuracy 0.85`.
  - Exit code 1 làm step fail, nên job fail. Bật **branch protection → Require status checks to pass** cho job `eval` thì PR **không merge được** khi chất lượng tụt.

---

## 4. Observability stack (production)

Stack: `docker compose -p k5-day14-observability up -d --build` trong `06-lab-complete/benchmark-observability/production/` (agent ×2 + Nginx + Prometheus + Grafana + Jaeger + Locust). Unit test alert rules: `promtool test rules /p/alert_rules_test.yml` → **SUCCESS**.

> **Sửa lỗi Dockerfile mẫu:** lần đầu bật stack, agent restart liên tục với `ModuleNotFoundError: No module named 'uvicorn'`.
> - Nguyên nhân: packages được cài `pip install --user` rồi copy vào `/home/agent/.local`, nhưng user `agent` được tạo với home `/app` (`useradd -d /app`). Python vì thế tìm user site ở `/app/.local`.
> - Đã thêm `ENV PYTHONUSERBASE=/home/agent/.local` vào `production/Dockerfile` (giống Dockerfile của Part 5).

### 4.0 Exercise 7.4: đọc dashboard khi có tải

Tải nền: Locust headless 30 users (spawn rate 5), 2 instance agent, cấu hình mặc định. Số đọc từ Prometheus (cửa sổ `rate(...[1m])`), cùng các query mà dashboard dùng:

| Chỉ số | Giá trị |
|---|---|
| p95 latency `/ask` | 0.296 s (295.9 ms) |
| avg latency `/ask` | 0.244 s (243.7 ms) |
| p99 latency `/ask` | 1.857 s |
| RPS theo instance | 10.35 req/s và 10.35 req/s |
| Tokens/s | input 1440.2, output 757.3 |
| Error rate `/ask` | 1.26 % |
| Cost | $4.31 / giờ |

1. **p95 và avg:**
   - p95 khoảng 296 ms, avg khoảng 244 ms, chênh nhau ít, nhưng **p99 = 1857 ms** (gấp khoảng 7,6 lần avg).
   - Tức là phần lớn request nhanh và đều, nhưng có một "đuôi" nhỏ (3 % request slow của mock) rất chậm.
   - Avg bị kéo lên một chút mà không phản ánh trải nghiệm của nhóm chậm. Chỉ percentile cao mới lộ ra nhóm này.
2. **Nginx chia đều traffic:** 10.35 / 10.35 req/s, chính xác 50/50, đúng kiểu round-robin.
3. **Input tokens/s > output tokens/s** (1440 so với 757): prompt gửi cho LLM gồm **system prompt + tài liệu được retrieve (RAG) + câu hỏi**, dài hơn nhiều so với câu trả lời ngắn. Agent v2 càng retrieve nhiều thì input càng tăng. Vì vậy dù giá output đắt hơn (gấp 4 lần), input vẫn chiếm một phần đáng kể chi phí.
4. **`curl http://localhost:8080/metrics` → `HTTP/1.1 404 Not Found`** (trang lỗi của nginx).
   - `nginx.conf` có `location = /metrics { return 404; }`: metrics chỉ dành cho Prometheus scrape **trực tiếp từng container trong mạng nội bộ** (`dns_sd_configs`), không public ra ngoài, vì metric lộ route, lưu lượng và cấu trúc hệ thống.
   - Ngoài ra, nếu đi qua load balancer thì mỗi lần scrape sẽ rơi vào một instance khác nhau, nên số liệu nhảy lung tung.

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải: [screenshots/grafana-dashboard.png](screenshots/grafana-dashboard.png). Khung 30 phút thấy đủ các giai đoạn:
  - benchmark scaling (Traffic khoảng 60 req/s);
  - tải nền 30 users;
  - thí nghiệm A: Error rate **20.35 %**, panel đỏ;
  - thí nghiệm C: LLM latency p95 tăng từ khoảng 200 ms lên khoảng 500 ms với `mock-llm-large`, Cost burn rate từ khoảng $4 lên khoảng $27/giờ, LLM cost (1h) $11.47;
  - thí nghiệm D: Instances up tụt 2 → 1 một lúc.

  Latency p95 /ask 670 ms, p99 khoảng 1.8 s.

  > **Sự cố Grafana:** image `grafana/grafana:13.2.4` được pull đúng lúc ổ C: hết chỗ nên bị hỏng: 1938/11864 file trong `public/` rỗng (kể cả `index.html`), trang trắng.
  > - Pull lại cùng tag vẫn dùng lại blob hỏng trong cache của Docker.
  > - Bản `13.2.7-slim` thì không kèm plugin datasource Prometheus ("Could not find plugin definition").
  > - Cuối cùng chạy `grafana/grafana:13.2.7` qua một file compose override tạm thời (`-f docker-compose.yml -f override.yml`), **không sửa `docker-compose.yml` của đề**. Dashboard và datasource provision giữ nguyên.
- [x] Một trace chậm trong Jaeger: [screenshots/jaeger-slow-trace.png](screenshots/jaeger-slow-trace.png)
- [x] Một trace lỗi trong Jaeger (có event `exception`): [screenshots/jaeger-error-trace.png](screenshots/jaeger-error-trace.png)
- [x] Trang Alerts của Prometheus với **2 alert cùng firing** (`AgentHighErrorRate` + `LLMCostBurnRateHigh`): [screenshots/prometheus-alert-firing.png](screenshots/prometheus-alert-firing.png)

### 4.2 Thí nghiệm "phá hệ thống"

Giữ Locust 30 users chạy suốt thí nghiệm. Đổi cấu hình bằng biến môi trường rồi `docker compose -p k5-day14-observability up -d --scale agent=2 agent` (tạo lại container agent). Một script hỏi `GET /api/v1/alerts` mỗi 5 giây để đo thời gian.

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` (error rate 20.28 %) | pending sau **29 s**, firing sau **92 s** | Error rate `/ask` (vọt từ khoảng 1 % lên khoảng 20 %) |
| B | `MOCK_SLOW_RATE=0.3` | _không chạy_ | _không chạy_ | _không chạy_ |
| C | `LLM_MODEL=mock-llm-large` (giữ `MOCK_ERROR_RATE=0.2`) | `LLMCostBurnRateHigh` ($13.42/giờ, sau đó $13.77/giờ) | pending sau **18 s**, firing sau **138 s** | Cost USD/giờ, LLM latency |
| D | `docker pause k5-day14-observability-agent-1` | `AgentInstanceDown` | pending sau **11 s**, firing sau **42 s** | Instances up (2 → 1) |
| E | `AGENT_VERSION=v1` | _không chạy_ (dự kiến: không alert nào) | — | — |

**Phân tích:**
- **A:**
  - Thời gian tới firing (92 s) = cửa sổ `rate(...[1m])` cần khoảng 15–30 s để tỉ lệ lỗi mới "lấn" số liệu cũ và vượt 5 % (pending sau 29 s), cộng `for: 1m` để tránh báo động giả, cộng chu kỳ đánh giá 5 s.
  - Trace lỗi trong Jaeger có event `exception` ở span `chat ...`.
- **D:**
  - Nhanh nhất (42 s) vì `up == 0` có hiệu lực ngay ở lần scrape thất bại đầu tiên (scrape 5 s, pending sau 11 s), cộng `for: 30s`.
  - Dùng `docker pause` chứ không dùng `stop`: container bị pause vẫn còn trong Docker DNS nhưng không trả lời, nên `up == 0`. Container bị stop biến mất khỏi DNS, Prometheus coi như target không tồn tại, nên không có alert.
- **C:**
  - Model lớn đắt hơn khoảng 16 lần mỗi token và chậm hơn 2,5 lần. Cost/giờ tăng từ $4.31 (mục 4.0) lên $13.42, tức **khoảng 3,1 lần**, không phải 16 lần, vì latency tăng làm mỗi user gửi ít request hơn (vòng lặp kín), và 20 % request lỗi (từ A) không tốn token output.
  - `for: 2m` cộng cửa sổ `rate(...[5m])` nên mất 138 s.
- **⚠️ Phát hiện về ngưỡng cost:**
  - Ngay ở **trạng thái bình thường** (model nhỏ, 30 users), `LLMCostBurnRateHigh` **đã firing** ($4.31/giờ > ngưỡng $2/giờ).
  - Lúc tạo lại container cho A và D, counter bị reset nên alert tạm hết, rồi firing lại ở C.
  - Ngưỡng $2/giờ quá thấp so với tải 30 users. Một alert lúc nào cũng đỏ thì sẽ bị mọi người phớt lờ (alert fatigue).
  - Production nên đặt ngưỡng theo **budget thật** (ví dụ budget ngày / 24 × 1,5) hoặc theo **mức tăng so với baseline** (ví dụ cost/giờ > 2 lần trung bình 7 ngày).

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

- Lỗi **chất lượng** (trả lời sai nhưng vẫn HTTP 200, nhanh, rẻ) không làm metric hạ tầng (latency, error, saturation) đỏ. Ngược lại, v1 còn rẻ và nhanh hơn. Phải đo **chất lượng** trực tiếp:
  1. **Quality gate trước deploy:** chạy `run_eval.py` trên golden dataset trong CI (`.github/workflows/eval.yml`), chặn merge khi accuracy < 85 % hoặc có regression. Đổi sang v1 sẽ bị chặn ngay (accuracy 4.35 %, mục 3).
  2. **Online eval / canary sau deploy:** định kỳ (ví dụ 5 phút) chạy một tập câu hỏi "synthetic" vào production, export accuracy thành metric (`agent_eval_accuracy`) và alert khi giảm.
  3. **Metric thay thế (proxy):** retrieval hit ratio (`retrieval.documents = 0`), tỉ lệ câu trả lời "chung chung", độ dài câu trả lời, `agent_info{version}` thay đổi bất thường, và tín hiệu người dùng (thumbs-down, hỏi lại cùng câu).
  4. **LLM-as-judge** chấm mẫu ngẫu nhiên traffic thật.

### 4.3 Debug bằng trace

Trace chậm nhất trong khoảng 1000 trace `/ask` (tìm bằng Jaeger API v3, sắp theo thời lượng):

- `trace_id`: `466d26f5e746a750ca15ae429037010c` (instance `ac0ce6187efb`)
- Tổng thời gian: **1714.9 ms**
- Các span:

  | Span | Thời gian | % |
  |---|---|---|
  | `POST /ask` | 1714.9 ms | 100 % |
  | └ `agent.run` | 1711.7 ms | 99.8 % |
  | &nbsp;&nbsp;├ `agent.retrieve_context` (`retrieval.documents=1`) | 22.4 ms | 1.3 % |
  | &nbsp;&nbsp;└ **`chat mock-llm-small`** (`gen_ai.usage.input_tokens=83`, `output_tokens=44`) | **1689.0 ms** | **98.5 %** |

- Span chiếm nhiều thời gian nhất: **`chat mock-llm-small` (1689.0 ms, 98.5 %)**. Đây là lời gọi LLM rơi vào nhóm "slow" (+1500 ms) của mock, tức tail latency đến từ **upstream LLM**, không phải retrieval hay code của agent.
- Dòng log tương ứng (`docker compose -p k5-day14-observability logs agent | grep 466d26f5e746a750ca15ae429037010c`):

```json
{"ts": "2026-11-26T12:50:52.654879+00:00", "level": "INFO", "service": "observable-agent", "instance": "ac0ce6187efb", "event": "llm_call", "trace_id": "466d26f5e746a750ca15ae429037010c", "span_id": "4cb3ab76bfd9d8d9", "request_id": "5848503092bd", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 83, "output_tokens": 44, "cost_usd": 3.9e-05, "llm_ms": 1689.1}
{"ts": "2026-11-26T12:50:52.655769+00:00", "level": "INFO", "service": "observable-agent", "instance": "ac0ce6187efb", "event": "http_request", "trace_id": "466d26f5e746a750ca15ae429037010c", "span_id": "4cb3ab76bfd9d8d9", "request_id": "5848503092bd", "method": "POST", "route": "/ask", "status": 200, "ms": 1713.7}
```

`llm_ms: 1689.1` trong log khớp với span `chat mock-llm-small` 1689.0 ms trong trace.

**Trace lỗi:** `trace_id` `5c0c07b9e5b01135df99febefde15794`, tổng 138.9 ms.
- Span `chat mock-llm-small` (93.3 ms) có status ERROR, attribute `error.type=upstream_unavailable` và event **`exception`**: `exception.type=utils.mock_llm.LLMError`, `exception.message="Upstream LLM error: 503 Service Unavailable (mock)"`.
- Lỗi lan lên `agent.run` và `POST /ask`.
- Log tương ứng:

```json
{"ts": "2026-11-26T12:51:08.332086+00:00", "level": "INFO", "service": "observable-agent", "instance": "187ad3a51a28", "event": "llm_error", "trace_id": "5c0c07b9e5b01135df99febefde15794", "span_id": "f827c99b09ab3ede", "request_id": "b885c9441cec", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
{"ts": "2026-11-26T12:51:08.332629+00:00", "level": "INFO", "service": "observable-agent", "instance": "187ad3a51a28", "event": "http_request", "trace_id": "5c0c07b9e5b01135df99febefde15794", "span_id": "f827c99b09ab3ede", "request_id": "b885c9441cec", "method": "POST", "route": "/ask", "status": 503, "ms": 137.8}
```

(Exercise 7.5 bước 4: `docker compose logs agent | grep llm_error | head -1` → `trace_id` `d44b0f444621d0bf0195cc875e05fd7a`, tìm lại được trong Jaeger bằng ô Lookup by Trace ID.)

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

Lệnh: `docker compose -p k5-day14-observability run --rm locust -f /mnt/locust/locustfile.py --host http://nginx --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_<n>`. Trước lần 3 instance, Prometheus thấy đủ 3 target `up=1`. Số liệu lấy từ dòng **Aggregated** của `benchmark-results/scale_1_stats.csv` và `scale_3_stats.csv`:

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 65.96 | 190 | 270 | 1700 | 64 / 7855 (0.81 %) |
| 3 | 65.36 | 190 | 280 | 1700 | 75 / 7787 (0.96 %) |

Riêng `POST /ask`: 1 instance 59.86 RPS, p95 280 ms, 64 failures / 7128; 3 instance 59.29 RPS, p95 290 ms, 75 failures / 7063. Failures là HTTP 503 từ lỗi upstream ngẫu nhiên khoảng 1 % của mock (`MOCK_ERROR_RATE=0.01`), không phải do quá tải.

**Giải thích kết quả: scale có cải thiện p95 không? Nút thắt nằm ở đâu?**

- **Không.** RPS, p50, p95, p99 gần như y hệt nhau (chênh lệch nằm trong nhiễu ngẫu nhiên). Lý do: **1 instance chưa hề bão hoà**, nên thêm instance không có gì để cải thiện.
- **Throughput bị giới hạn bởi chính bộ tạo tải (vòng lặp kín), không phải bởi server.**
  - Mỗi user Locust gửi request, chờ response (khoảng 0,22 s), rồi **nghỉ 0,5–2 s** (`wait_time = between(0.5, 2)`, trung bình 1,25 s).
  - Thông lượng tối đa ≈ 100 users / (1,25 + 0,22) s ≈ **68 req/s**, khớp với khoảng 66 RPS đo được ở cả 2 lần.
  - Muốn thấy lợi ích của scale phải tăng tải tới mức 1 instance bão hoà (nhiều user hơn, hoặc `wait_time` = 0), như ở mục 2.1, nơi 1 process bão hoà ở khoảng 110 RPS.
- **Latency mỗi request bị chặn dưới bởi LLM:**
  - Trace cho thấy span `chat ...` chiếm khoảng 98 % thời gian. LLM mất khoảng 120–180 ms mỗi call bất kể có bao nhiêu instance.
  - p99 khoảng 1700 ms là các call "slow" của upstream LLM.
  - Scale ngang agent **không làm LLM nhanh hơn**: p50/p95/p99 do LLM quyết định.
  - Nút thắt thật nằm ở **latency của LLM upstream** (và ở production thật là rate limit / quota của provider). Muốn giảm latency phải dùng cache câu trả lời, model nhỏ hoặc nhanh hơn, streaming, hoặc gọi song song. Thêm instance chỉ giúp **tăng throughput khi CPU hoặc event loop bão hoà**.

---

## 6. Kết luận

- 3 điều học được về benchmark & observability:
  1. **Average nói dối, percentile thì không.** Với 3 % request chậm, avg chỉ tăng 31 % trong khi p99 tăng 7,6 lần (mục 2.2). SLO phải đặt theo p95/p99. Và với ít mẫu (23 câu eval, 300 request) thì p95/p99 dao động mạnh, nên cần đủ mẫu trước khi kết luận.
  2. **"Nhanh" và "đúng" là hai trục độc lập, cần hai loại benchmark.** v1 nhanh và rẻ hơn nhưng chỉ đúng 4.35 %. Không metric hạ tầng nào đỏ khi đổi sang v1 (thí nghiệm E). Phải có eval + quality gate trong CI, và so phiên bản bằng **cost / câu đúng** thay vì cost / câu (v2 rẻ hơn khoảng 11 lần trên mỗi câu đúng).
  3. **Metrics → traces → logs là một chuỗi điều tra.** Dashboard/alert báo *có* vấn đề (p99 cao, error rate 20 %). Trace chỉ ra *ở đâu* (span `chat ...` chiếm 98,5 %). Log có cùng `trace_id` cho *chi tiết* (token, cost, thông điệp lỗi). Ngoài ra, scale chỉ có ích khi đúng chỗ bão hoà: benchmark cho thấy nút thắt là LLM latency và bộ tạo tải, không phải số instance.
- Nếu đưa agent này lên production thật, sẽ thêm:
  - **Alert theo burn rate của error budget** (multi-window, ví dụ 1h và 6h) thay vì ngưỡng tức thời 5 %, để báo sớm mà ít báo giả.
  - **Ngưỡng cost theo budget thật** (hoặc so với baseline 7 ngày) thay vì $2/giờ cố định, vốn đã đỏ ngay ở tải bình thường.
  - **Metric và alert chất lượng:** `agent_eval_accuracy` từ online eval định kỳ, `agent_retrieval_hit_ratio` (tỉ lệ có tài liệu retrieve), tỉ lệ câu trả lời fallback, feedback người dùng.
  - **Giám sát ở tầng load balancer / synthetic check từ ngoài:** đếm 502/504 ở Nginx (thí nghiệm D cho thấy lỗi ở Nginx không hiện trong metric của agent), uptime check gọi `/health` và `/ask` từ bên ngoài.
  - **Saturation:** CPU/memory từng container, độ dài hàng đợi hoặc `agent_in_flight_requests`, và rate limit / quota còn lại của LLM provider (`429` từ upstream).
  - **Latency theo model và phiên bản** (`histogram_quantile` theo `model`, `version`) để so sánh khi canary deploy.

---

## Phụ lục: Exercise 7.1 (đọc metrics của agent)

Gọi `POST /ask` một lần trên agent v2 (cổng 8002), rồi `curl -s http://localhost:8002/metrics | grep -v "^#"` (trích):

```text
http_requests_total{method="POST",route="/ask",status="200"} 1.0
http_request_duration_seconds_bucket{le="0.3",method="POST",route="/ask"} 1.0
http_request_duration_seconds_count{method="POST",route="/ask"} 1.0
http_request_duration_seconds_sum{method="POST",route="/ask"} 0.19857385300019814
agent_in_flight_requests 0.0
llm_requests_total{model="mock-llm-small",status="ok"} 1.0
llm_tokens_total{model="mock-llm-small",type="input"} 91.0
llm_tokens_total{model="mock-llm-small",type="output"} 55.0
llm_cost_usd_total{model="mock-llm-small"} 4.6649999999999996e-05
agent_info{model="mock-llm-small",version="v2"} 1.0
agent_question_length_chars_bucket{le="50.0"} 1.0
agent_question_length_chars_count 1.0
agent_question_length_chars_sum 40.0
```

**1. Ví dụ mỗi loại metric:**

| Loại | Ví dụ | Đặc điểm |
|---|---|---|
| **Counter** | `http_requests_total`, `llm_tokens_total`, `llm_cost_usd_total` | Chỉ tăng (reset về 0 khi restart). Dùng `rate()` để ra tốc độ, ví dụ RPS, token/giây |
| **Gauge** | `agent_in_flight_requests`, `agent_info` | Lên xuống tuỳ ý: số request đang xử lý tại thời điểm scrape |
| **Histogram** | `http_request_duration_seconds` (`_bucket`, `_sum`, `_count`), `agent_question_length_chars` | Đếm số quan sát rơi vào từng bucket `le=...`, nên tính được percentile bằng `histogram_quantile(0.95, ...)` |

**2. Dùng URL thật (`request.url.path`) làm label `route` thì sao?**
- Mỗi URL lạ (`/wp-admin`, `/.env`, `/a1b2c3`...) sẽ tạo **một time series mới** cho *mỗi* tổ hợp `method × route × status`, và cho *mỗi* bucket của histogram latency (11 bucket + `_sum` + `_count`).
- Bot quét hàng nghìn URL thì số series **bùng nổ** (cardinality cao): Prometheus tốn RAM và disk, query chậm, có thể OOM. Dashboard và alert trở nên vô dụng.
- Dùng route template, mọi URL không khớp route chỉ gom vào **một** label `unmatched`. Đã kiểm chứng: gọi `/wp-admin`, `/.env`, `/a1b2c3` thì chỉ ra một series:
  ```text
  http_requests_total{method="GET",route="unmatched",status="404"} 3.0
  ```

**3. Nhiệm vụ: thêm metric `agent_question_length_chars`.** Đã thêm vào `develop/app.py`:

```python
QUESTION_LENGTH = Histogram(
    "agent_question_length_chars", "Độ dài câu hỏi (ký tự)",
    buckets=(20, 50, 100, 200, 500, 1000, 2000),
)
# trong endpoint ask(), trước khi gọi LLM:
QUESTION_LENGTH.observe(len(body.question))
```

Dùng **Histogram** chứ không dùng label `length=<số>`, vì độ dài có vô số giá trị, làm label sẽ bùng nổ cardinality. Kết quả ở trên: câu "Prometheus thu thập metrics như thế nào?" dài 40 ký tự, rơi vào bucket `le="50.0"`.
