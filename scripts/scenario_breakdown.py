"""Run each chaos scenario in isolation and report its metrics separately.

`run_simulation` aggregates every scenario into one RunMetrics, which hides
per-scenario behaviour. This script runs them one at a time so the report can
show expected-vs-observed for each named scenario.

Usage:
    python scripts/scenario_breakdown.py [--config configs/default.yaml]
                                         [--out reports/metrics_by_scenario.json]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from reliability_lab.chaos import load_queries, run_scenario
from reliability_lab.config import ScenarioConfig, load_config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--out", default="reports/metrics_by_scenario.json")
    args = parser.parse_args()

    config = load_config(args.config)
    queries = load_queries()
    scenarios = config.scenarios or [ScenarioConfig(name="default", description="baseline run")]

    breakdown: dict[str, dict[str, object]] = {}
    for scenario in scenarios:
        # run_scenario() flushes the shared cache first, so scenarios stay isolated.
        metrics = run_scenario(config, queries, scenario)
        report = metrics.to_report_dict()
        report.pop("scenarios", None)
        report["description"] = scenario.description
        report["static_fallbacks"] = metrics.static_fallbacks
        report["fallback_successes"] = metrics.fallback_successes
        report["cache_hits"] = metrics.cache_hits
        breakdown[scenario.name] = report

        print(f"\n=== {scenario.name} ===  {scenario.description}")
        for key, value in report.items():
            if key == "description":
                continue
            print(f"  {key:24} {value}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(breakdown, indent=2, ensure_ascii=False))
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
