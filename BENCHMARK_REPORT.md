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

_(CP8 — làm sau giờ lab)_

### 4.1 Screenshot

- [ ] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals)
- [ ] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian)
- [ ] Một trace lỗi trong Jaeger (có event `exception`)
- [ ] Trang Alerts của Prometheus với ít nhất 1 alert đang **firing**

### 4.2 Thí nghiệm "phá hệ thống"

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | | | |
| B | `MOCK_SLOW_RATE=0.3` | | | |
| C | `LLM_MODEL=mock-llm-large` | | | |
| D | `docker pause` 1 instance | | | |
| E | `AGENT_VERSION=v1` | | | |

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

...

### 4.3 Debug bằng trace

Chọn 1 trace chậm nhất bạn tìm được:

- `trace_id`: ...
- Tổng thời gian: ... ms
- Span chiếm nhiều thời gian nhất: ... (... ms, ...%)
- Dòng log tương ứng (grep theo `trace_id`):

```json

```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | | | | | |
| 3 | | | | | |

**Giải thích kết quả:** Scale có cải thiện p95 không? Nút thắt nằm ở đâu?

...

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. ...
  2. ...
  3. ...
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?

...

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
