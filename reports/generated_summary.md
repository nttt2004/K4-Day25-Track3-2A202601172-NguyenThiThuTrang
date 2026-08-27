# Day 10 Reliability Final Report

## Metrics Summary

| Metric | Value |
|---|---:|
| total_requests | 600 |
| availability | 0.9917 |
| error_rate | 0.0083 |
| latency_p50_ms | 271.9 |
| latency_p95_ms | 315.28 |
| latency_p99_ms | 319.44 |
| fallback_success_rate | 0.9615 |
| cache_hit_rate | 0.655 |
| circuit_open_count | 13 |
| recovery_time_ms | 2289.2566323280334 |
| estimated_cost | 0.08335 |
| estimated_cost_saved | 0.393 |

## Chaos Scenarios

| Scenario | Status |
|---|---|
| primary_timeout_100 | pass |
| primary_flaky_50 | pass |
| all_healthy | pass |
| primary_recovers | pass |

## Per-scenario metrics

| Scenario | availability | fallback_success_rate | cache_hit_rate | circuit_open_count | recovery_time_ms |
|---|---:|---:|---:|---:|---:|
| primary_timeout_100 | 0.9867 | 0.9636 | 0.6333 | 8 | None |
| primary_flaky_50 | 0.9867 | 0.931 | 0.7 | 3 | 2311.520576477051 |
| all_healthy | 1.0 | 1.0 | 0.66 | 0 | None |
| primary_recovers | 1.0 | 1.0 | 0.6667 | 4 | 2366.6646480560303 |

## Analysis TODO(student)

Explain what failed, why the fallback path worked or did not work, and what you would change before production.