# Reliability Engineering Report - LLM Agent Gateway (Day 25)

**Tác giả:** Nguyễn Thị Thu Trang
**Ngày:** 27/08/2026
**Môi trường:** Windows 11, Python 3.11, Redis 7 (Docker), `pytest` - 35 passed + 7 xpassed, 0 failed.

---

## 1. Architecture summary

Gateway định tuyến mỗi request qua ba lớp bảo vệ theo thứ tự **cache → circuit breaker (provider chain) → static fallback**. Mỗi provider có một circuit breaker riêng để cô lập sự cố (bulkhead). Cache có thể là in-memory (`ResponseCache`) hoặc chia sẻ qua Redis (`SharedRedisCache`) cho triển khai nhiều instance.

```
                         ┌─────────────────────────────────────────────┐
   User Request ───────► │              ReliabilityGateway             │
   "prompt"              │                 .complete()                 │
                         └───────────────────┬─────────────────────────┘
                                             │
                    (1) CACHE CHECK          ▼
                         ┌───────────────────────────────────────┐
                         │  cache.get(prompt)                     │
                         │   • _is_uncacheable(prompt)? ─► bypass │
                         │   • similarity ≥ threshold?            │
                         │   • _looks_like_false_hit()? ─► reject │
                         └───────┬───────────────────────┬───────┘
                          HIT    │                       │  MISS / rejected
                                 ▼                       ▼
              route="cache_hit:0.98"         (2) PROVIDER FALLBACK CHAIN
              cache_hit=True                  ┌──────────────────────────────┐
              latency=0, cost=0               │ for provider in providers:   │
                                              │                              │
                                              │  breaker[primary].call() ────┼──► Provider "primary"
                                              │    OPEN? ─► CircuitOpenError  │      (FakeLLMProvider)
                                              │    fail? ─► ProviderError     │
                                              │        │ record_failure()    │
                                              │        ▼ next provider        │
                                              │  breaker[backup].call()  ────┼──► Provider "backup"
                                              │    OPEN? ─► CircuitOpenError  │
                                              │        │                     │
                                              │        ▼ all failed          │
                                              └──────────┬───────────────────┘
                                            success      │        all providers failed
                                    route="primary"      │                 │
                                    or  "fallback"       ▼                 ▼
                                    cache.set(prompt, text)      (3) STATIC FALLBACK
                                    return GatewayResponse       route="static_fallback"
                                                                 error=last_error
                                                                 "The service is temporarily
                                                                  degraded. Please try again soon."
```

**Circuit breaker - 3-state machine (mỗi provider một breaker):**

```
                 failure_count ≥ failure_threshold
      ┌────────┐ ────────────────────────────────────► ┌────────┐
      │ CLOSED │                                       │  OPEN  │
      │        │ ◄──────────────────────────────────── │        │
      └────────┘   success_count ≥ success_threshold   └────────┘
           ▲            (reason="probe_success")            │
           │                                                │ reset_timeout_seconds elapsed
           │                                                ▼
           │                                          ┌───────────┐
           └──────────── probe succeeds ───────────── │ HALF_OPEN │
                                                      │  (1 probe)│
              probe fails ─► OPEN                      └───────────┘
              (reason="probe_failure", riêng biệt với failure_threshold)
```

---

## 2. Configuration

Nguồn: `configs/default.yaml`.

| Setting                        |                      Value | Rationale                                                                                                                                                                                                                                                                                                |
| ------------------------------ | -------------------------: | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `providers[primary].fail_rate` |                       0.25 | Mô phỏng provider chính "hơi phập phù" - đủ để circuit breaker thỉnh thoảng mở nhưng không sập hoàn toàn.                                                                                                                                                                                                |
| `providers[backup].fail_rate`  |                       0.05 | Provider dự phòng ổn định hơn, chi phí thấp hơn (0.006 vs 0.01 / 1k tokens) - vai trò lưới an toàn.                                                                                                                                                                                                      |
| `failure_threshold`            |                          3 | Cân bằng giữa nhạy và ổn định: 1 quá nhạy (một lỗi ngẫu nhiên cũng mở circuit → retry storm sang backup); 5+ quá trơ, người dùng phải chịu nhiều lỗi trước khi hệ thống phản ứng. 3 lỗi **liên tiếp** (record_success reset counter) là tín hiệu đủ mạnh về sự cố hệ thống.                              |
| `reset_timeout_seconds`        |                          2 | Thời gian "để provider hồi phục" trước khi thử probe. 2s đủ ngắn để không kéo dài downtime cảm nhận được, đủ dài để tránh probe dồn dập khi provider đang quá tải. Quan sát: `recovery_time_ms ≈ 2270ms` = timeout + latency 1 probe.                                                                    |
| `success_threshold`            |                          1 | Trong HALF_OPEN chỉ cần 1 probe thành công là đóng circuit. Với traffic thấp, chờ nhiều probe làm chậm hồi phục. Nếu provider thật sự chưa ổn, lỗi tiếp theo sẽ mở lại ngay (`probe_failure`).                                                                                                           |
| `cache.ttl_seconds`            |                        300 | Câu trả lời FAQ/chính sách ít thay đổi trong 5 phút; TTL ngắn hơn (60s) làm hit rate tụt mà không tăng độ chính xác đáng kể; TTL dài hơn (1h+) rủi ro trả nội dung lỗi thời (học phí, deadline).                                                                                                         |
| `cache.similarity_threshold`   |                       0.92 | Đã thử 0.85 → false hit trên các câu chỉ khác năm ("2024" vs "2026", cosine ~0.88). Ở 0.92, chỉ các cách diễn đạt gần như tương đương ("Summarize the refund policy" vs "Summarize refund policy", cosine ~0.90–0.95) mới hit - kết hợp với guardrail `_looks_like_false_hit` cho lớp phòng thủ thứ hai. |
| `cache.backend`                |                    `redis` | Chuyển từ `memory` sang `redis` để cache chia sẻ giữa nhiều instance gateway (xem §6).                                                                                                                                                                                                                   |
| `load_test.requests`           | 150 / scenario (× 4 = 600) | Đủ mẫu để phân vị P95/P99 ổn định. Nâng từ 100 → 150 để mỗi scenario chắc chắn trải qua ít nhất một chu kỳ open→half_open→closed hoàn chỉnh (recovery_time_ms không còn `null`).                                                                                                                         |
| `scenarios`                    |                          4 | Thêm `primary_recovers` (primary fail 0.35) - circuit mở rồi đóng lại đều đặn vì probe thành công ~65%, đảm bảo có bằng chứng recovery time trong mọi lần chạy.                                                                                                                                          |

---

## 3. SLO definitions

Số dưới đây từ `reports/metrics.json` (một lần chạy đại diện, backend redis, đã FLUSHDB). _Cập nhật lại nếu bạn chạy lại._

| SLI                   | SLO target | Actual value | Met? |
| --------------------- | ---------- | -----------: | ---- |
| Availability          | ≥ 99%      |       0.9917 | ✅   |
| Latency P95           | < 2500 ms  |     315.3 ms | ✅   |
| Fallback success rate | ≥ 95%      |       0.9615 | ✅   |
| Cache hit rate        | ≥ 10%      |        0.655 | ✅   |
| Recovery time         | < 5000 ms  |      2289 ms | ✅   |

> Ghi chú 1: `FakeLLMProvider` và `run_scenario` dùng `random` không seed, nên số dao động ~1% giữa các lần chạy (availability quan sát 0.977–0.99). Nên chạy 3–5 lần và lấy trung bình, hoặc seed theo tên scenario để tái tạo chính xác.
>
> Ghi chú 2: `run_scenario` gọi `reset_shared_cache()` (flush Redis) trước mỗi scenario để các scenario **độc lập** - cache nóng còn sót từ scenario trước sẽ chặn hầu hết request tới provider, khiến circuit breaker không bao giờ mở và làm mất bằng chứng recovery time. Đây là lý do trước khi thêm bước này, `recovery_time_ms` hay bằng `null`.

---

## 4. Metrics

Nguồn: `reports/metrics.json` - sinh bởi `python scripts/run_chaos.py` (backend Redis, **đã `FLUSHDB` trước khi đo**).

```json
{
  "total_requests": 600,
  "availability": 0.9917,
  "error_rate": 0.0083,
  "latency_p50_ms": 271.9,
  "latency_p95_ms": 315.28,
  "latency_p99_ms": 319.44,
  "fallback_success_rate": 0.9615,
  "cache_hit_rate": 0.655,
  "circuit_open_count": 13,
  "recovery_time_ms": 2289.2566323280334,
  "estimated_cost": 0.08335,
  "estimated_cost_saved": 0.393,
  "scenarios": {
    "primary_timeout_100": "pass",
    "primary_flaky_50": "pass",
    "all_healthy": "pass",
    "primary_recovers": "pass"
  }
}
```

| Metric                |   Value |
| --------------------- | ------: |
| total_requests        |     600 |
| availability          |  0.9917 |
| error_rate            |  0.0083 |
| latency_p50_ms        |   271.9 |
| latency_p95_ms        |  315.28 |
| latency_p99_ms        |  319.44 |
| fallback_success_rate |  0.9615 |
| cache_hit_rate        |   0.655 |
| estimated_cost        | 0.08335 |
| estimated_cost_saved  |   0.393 |
| circuit_open_count    |      13 |
| recovery_time_ms      | 2289.26 |

> _Số trên là một lần chạy. `random` chưa seed nên mỗi lần chạy lệch ~1% (availability 0.987–0.995, recovery 2280–2410 ms). Chạy lại `python scripts/run_chaos.py` (đã tự `FLUSHDB` trước mỗi scenario) và cập nhật bảng nếu cần._

---

## 5. Cache comparison

- **Without cache:** `configs/cache_off.yaml` (`cache.enabled: false`) - trung bình 2 lần chạy, 600 request.
- **With cache:** `configs/default.yaml` (`cache.enabled: true`, backend redis, tự `FLUSHDB` mỗi scenario) - trung bình 2 lần chạy, 600 request.

| Metric                     | Without cache | With cache | Delta              |
| -------------------------- | ------------: | ---------: | ------------------ |
| latency_p50_ms             |         267.7 |      273.6 | +5.9 (trong nhiễu) |
| latency_p95_ms             |         315.3 |      316.2 | ≈ 0                |
| latency_p99_ms             |         320.0 |      320.2 | ≈ 0                |
| availability               |        0.9709 |     0.9909 | **+0.020**         |
| estimated_cost (USD)       |        0.2562 |     0.0876 | **−65.8%**         |
| estimated_cost_saved (USD) |             0 |     0.3905 | +0.39              |
| cache_hit_rate             |             0 |       0.65 | +0.65              |

**Nhận xét:**

- **Cache KHÔNG cải thiện latency percentile** - và đây là kết quả đúng, không phải bug. Cache hit có `latency_ms = 0` và `run_scenario` chỉ append `latency_ms > 0` vào phân phối, nên P50/P95/P99 chỉ phản ánh các lần gọi provider thật. Chênh lệch ±6ms nằm trong nhiễu run-to-run. Lợi ích latency thực tế nằm ở **throughput** (mỗi hit tiết kiệm ~180–320ms tường-thời-gian và một khe kết nối provider), không thể hiện qua percentile của tập đo này.
- **Lợi ích chính là chi phí và độ sẵn sàng:** hit rate 0.65 cắt `estimated_cost` xuống ~1/3 (−65.8%); mỗi hit tiết kiệm ~0.001 USD → **~0.39 USD / 600 request**. Availability tăng 2 điểm vì cache hit không thể "fail" - request được phục vụ ngay cả khi cả hai provider đang lỗi.
- **Đánh đổi:** rủi ro trả nội dung sai (false hit). Được kiểm soát bằng `similarity_threshold = 0.92` + `_looks_like_false_hit` + guardrail privacy (xem §8).

---

## 6. Redis shared cache

**Tại sao in-memory không đủ cho multi-instance:**

- Mỗi instance gateway giữ cache riêng trong RAM. Với N instance sau load balancer, cùng một câu hỏi phải "miss" N lần trước khi mọi instance đều nóng → hit rate thực tế thấp hơn nhiều so với đo trên 1 instance.
- Cache không sống sót qua restart/deploy - mỗi lần rollout là một đợt "cold cache" dội tải lên provider.
- Không có nguồn sự thật chung: hai instance có thể trả lời khác nhau cho cùng câu hỏi tùy cache cục bộ.

**`SharedRedisCache` giải quyết thế nào:**

- Toàn bộ instance đọc/ghi cùng một Redis. Một instance ghi cache, tất cả instance còn lại hit ngay.
- TTL do Redis `EXPIRE` quản lý → dọn dẹp tự động, nhất quán, không cần eviction thủ công.
- Sống sót qua restart/deploy của gateway (Redis là process riêng + volume `redis-data`).
- Giữ nguyên guardrails: `_is_uncacheable` (privacy) và `_looks_like_false_hit` áp dụng ở cả nhánh exact-match lẫn similarity-scan.

### Evidence of shared state

`pytest tests/test_redis_cache.py::test_shared_state_across_instances` - PASSED:

```
c1 = SharedRedisCache(prefix="rl:test:shared:")
c2 = SharedRedisCache(prefix="rl:test:shared:")   # instance độc lập, cùng Redis
c1.set("shared query", "shared response")
cached, _ = c2.get("shared query")
assert cached == "shared response"                # ✔ c2 thấy dữ liệu c1 ghi
```

Toàn bộ 6/6 Redis test:

```
tests/test_redis_cache.py::test_redis_connection PASSED
tests/test_redis_cache.py::test_set_and_exact_get PASSED
tests/test_redis_cache.py::test_ttl_expiry PASSED
tests/test_redis_cache.py::test_shared_state_across_instances PASSED
tests/test_redis_cache.py::test_privacy_query_not_cached PASSED
tests/test_redis_cache.py::test_false_hit_different_years PASSED
============================== 6 passed ==============================
```

### Redis CLI output

```bash
$ docker compose exec redis redis-cli KEYS "rl:cache:*"
rl:cache:734852f3cf4a
rl:cache:dacb2b833659
rl:cache:3936614ac4c2
rl:cache:fff10da1c72c
rl:cache:98332d0d1c9c
rl:cache:844ef0143a5c
rl:cache:9e413fd814eb
rl:cache:4fc3c69b9376
rl:cache:0bc3b1acf73d
rl:cache:8baa2cfa11fa
rl:cache:3dab98c0e49e
rl:cache:095946136fea
rl:cache:d354658dc020
# 13 keys (20 sample query trừ các câu privacy/uncacheable và static fallback không được cache)

$ docker compose exec redis redis-cli TTL rl:cache:734852f3cf4a
257                          # đang đếm ngược từ ttl_seconds = 300

$ docker compose exec redis redis-cli HGETALL rl:cache:734852f3cf4a
query     "What is the tuition fee for the 2025 academic year?"
response  "[backup] reliable answer for: What is the tuition fee for the 2025 academic year?"
```

Data model: `key = "rl:cache:" + md5(query.lower().strip())[:12]`, value là Redis Hash `{query, response}`, TTL tự động.

### In-memory vs Redis latency comparison (optional)

Cùng config, chỉ khác `cache.backend` (`memory` vs `redis`), `FLUSHDB` trước mỗi lần đo.

| Metric               | In-memory cache | Redis cache | Notes                                                                                                                               |
| -------------------- | --------------: | ----------: | ----------------------------------------------------------------------------------------------------------------------------------- |
| latency_p50_ms       |           270.1 |       280.7 | Chênh ~10ms nằm trong nhiễu run-to-run - round-trip Redis localhost ~0.1–0.5ms/hit, không đáng kể so với latency provider 180–320ms |
| latency_p95_ms       |           316.2 |       316.8 | ≈ nhau                                                                                                                              |
| cache_hit_rate       |            0.58 |        0.70 | cùng khoảng; khác biệt do RNG chọn query, không do backend                                                                          |
| estimated_cost_saved |           0.175 |        0.21 | tương đương                                                                                                                         |

→ Redis đánh đổi ~sub-millisecond độ trễ mỗi thao tác cache để lấy **shared state + persistence**. Với hệ nhiều instance, lợi ích shared state lớn hơn nhiều chi phí round-trip.

---

## 7. Chaos scenarios

Nguồn: `configs/default.yaml` → `scenarios`. Số per-scenario sinh bởi `python scripts/scenario_breakdown.py`
(mỗi scenario flush cache trước khi chạy → cô lập hoàn toàn) → `reports/metrics_by_scenario.json`.
150 request / scenario. Đánh giá pass/fail từ `run_scenario` output + `breaker.transition_log`.

| Scenario                                                                   | Expected behavior                                                                                                                                                                   | Observed behavior (1 lần chạy đại diện, 150 req)                                                                                                                                                                                                | Pass/Fail               |
| -------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------- |
| `primary_timeout_100` (primary fail=1.0)                                   | 100% traffic không-cache phải fallback sang backup; primary circuit mở sau 3 lỗi và ở OPEN (probe luôn fail → `probe_failure`, không bao giờ đóng); availability vẫn cao nhờ backup | `circuit_open_count = 8`, `recovery_time_ms = null` - HALF_OPEN không đóng vì primary luôn fail (**đúng kỳ vọng**). `fallback_successes = 53`, `static_fallbacks = 2` (backup fail 5%). availability **0.987**, fallback_success_rate **0.96**. | **PASS**                |
| `primary_flaky_50` (primary fail=0.5)                                      | Circuit dao động: mở khi gặp chuỗi lỗi, half-open sau 2s, đóng lại khi probe thành công; mix primary + fallback                                                                     | `circuit_open_count = 3`, có cặp open→closed → `recovery_time_ms ≈ 2312 ms` (≈ reset_timeout 2s + 1 probe). `fallback_successes = 27`, `static_fallbacks = 2`. availability **0.987**.                                                          | **PASS**                |
| `all_healthy` (không override - **primary vẫn fail 0.25 theo config gốc**) | Đa số qua primary; circuit thỉnh thoảng mở khi gặp 3 lỗi liên tiếp (~1.5%/bộ ba); availability cao nhất, không static fallback                                                      | `circuit_open_count = 0–2` (tùy RNG), `fallback_successes = 11`, `static_fallbacks = 0`. availability **1.0**, fallback_success_rate **1.0**, **P50 thấp nhất ~214 ms** (primary latency 180 < backup 260).                                     | **PASS**                |
| `primary_recovers` (primary fail=0.35 - **scenario tự thêm**)              | Circuit mở rồi **đóng lại** đều đặn: sau 2s vào HALF_OPEN, probe thành công ~65% → CLOSED. Đảm bảo luôn có bằng chứng recovery.                                                     | `circuit_open_count = 4`, **mọi lần chạy đều ghi được `recovery_time_ms ≈ 2280–2370 ms`**. `fallback_successes = 37`, `static_fallbacks = 0`. availability **1.0**.                                                                             | **PASS**                |
| `cache_cold_vs_warm` (scenario đo lường tự thêm)                           | Chạy lần 2 không `FLUSHDB` → cache_hit_rate cao giả tạo do dữ liệu còn nóng, KHÔNG phải do code tốt hơn                                                                             | Cold (sau FLUSHDB): cache_hit_rate ≈ 0.65; Warm (không flush): ≈ 0.77 - chênh ~11 điểm chỉ do Redis persist (TTL 300s + volume). **Bài học: luôn `FLUSHDB` trước mỗi lần đo.**                                                                  | **PASS (đã kiểm soát)** |

**Tổng hợp (`reports/metrics.json`, 600 request):** availability **0.992**, fallback_success_rate **0.96**, circuit_open_count **13**, recovery_time_ms **≈ 2289 ms**, cache_hit_rate **0.66**, cost_saved **0.393 USD**. Cả 4 scenario **pass**.

> `recovery_time_ms` có thể = `null` ở từng scenario riêng lẻ (`primary_timeout_100` luôn null theo thiết kế; `all_healthy` null khi không có chu kỳ open→closed nào trong 150 req). Ở mức tổng hợp, `run_simulation` lấy trung bình các scenario có recovery ≠ null → luôn có giá trị nhờ `primary_flaky_50` + `primary_recovers`. Đây là hành vi đo lường bình thường, không phải lỗi logic.

---

## 8. Failure analysis

### Điểm yếu còn lại: guardrail false-hit không bắt được khác biệt ngữ nghĩa phủ định

`_looks_like_false_hit()` hiện chỉ so sánh các cụm **4 chữ số** (năm, ID) giữa query và cached key. Nó **không** phát hiện khi hai câu hỏi chỉ khác nhau một từ mang nghĩa phủ định hoặc đảo chiều:

| Query mới                                                 | Cached key                                                       |         Cosine similarity | Kết quả hiện tại                | Đúng ra phải |
| --------------------------------------------------------- | ---------------------------------------------------------------- | ------------------------: | ------------------------------- | ------------ |
| "Can I get a refund if I **cancel** before the deadline?" | "Can I get a refund if I **do not cancel** before the deadline?" | ~0.93 (chỉ khác "do not") | **HIT** - trả lời sai hoàn toàn | MISS         |
| "Is the library **open** on Sunday?"                      | "Is the library **closed** on Sunday?"                           |                     ~0.88 | có thể HIT tùy threshold        | MISS         |
| "How do I **enable** two-factor auth?"                    | "How do I **disable** two-factor auth?"                          |                     ~0.90 | **HIT** - hướng dẫn ngược       | MISS         |

**Nguyên nhân gốc:** cosine trên n-gram/word token đo _độ trùng bề mặt_, không đo _ý định_. Các từ `not / no / never / cancel / disable / closed` chiếm tỉ trọng vector rất nhỏ nhưng đảo ngược toàn bộ ngữ nghĩa. Threshold cao hơn cũng không cứu được vì phần còn lại của câu gần như giống hệt.

**Đề xuất khắc phục (theo thứ tự ưu tiên):**

1. **Negation-aware guardrail (rẻ, làm ngay):** mở rộng `_looks_like_false_hit()` - trích tập từ phủ định/đảo nghĩa (`not, no, never, without, cancel, disable, deny, closed, exclude, ...`) từ cả hai chuỗi; nếu tập này **khác nhau** thì coi là false hit, log `reason="polarity_mismatch"`. Chi phí O(n), không cần model.
2. **Embedding similarity thay cho n-gram cosine:** dùng sentence-transformers (`all-MiniLM-L6-v2`) để so khớp ngữ nghĩa thật; negation vẫn là điểm yếu cố hữu của embedding nên vẫn giữ guardrail (1) làm lớp chặn.
3. **Semantic key normalization:** chuẩn hóa câu hỏi qua một LLM nhỏ ("rewrite to canonical intent") trước khi hash/so khớp - đắt, chỉ nên dùng cho tier trả phí.
4. **Confidence-gated cache:** chỉ phục vụ từ cache khi `score ≥ 0.97` **và** guardrail sạch; khoảng `0.92–0.97` thì vẫn gọi provider nhưng ghi nhận "cache candidate" để phân tích offline, tinh chỉnh threshold từ dữ liệu thật.

### Rủi ro phụ

- **Redis là single point of failure:** nếu Redis sập, `SharedRedisCache.get/set` ném exception → gateway lỗi. Khắc phục: bọc try/except, degrade về `ResponseCache` in-memory (stretch goal).
- **Circuit breaker state không chia sẻ:** mỗi instance có breaker riêng → instance A đã biết provider hỏng nhưng instance B vẫn dội request vào. Khắc phục: lưu counter breaker trong Redis (`INCR`/`EXPIRE`).
- **Không có per-user rate limiting:** một user spam có thể làm mở circuit ảnh hưởng toàn bộ user.

---

## 9. Next steps

1. **Negation/polarity guardrail** trong `_looks_like_false_hit()` - vá lỗ hổng false-hit nguy hiểm nhất với chi phí gần bằng 0.
2. **Redis-backed circuit state** (`INCR`/`EXPIRE`) để breaker chia sẻ trạng thái giữa các instance, tránh retry storm phân tán.
3. **Graceful degradation khi Redis down** - fallback `SharedRedisCache` → `ResponseCache` in-memory, log cảnh báo, không để lỗi hạ tầng cache thành lỗi người dùng.
4. **Seed RNG theo tên scenario** trong `run_scenario` để `metrics.json` tái tạo chính xác cho grader.
5. **Cost-aware routing:** khi ngân sách chạm 80%, route sang model rẻ; 100% thì chỉ phục vụ cache hoặc static fallback.
