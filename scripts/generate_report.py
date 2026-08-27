from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics", default="reports/metrics.json")
    parser.add_argument("--out", default="reports/generated_summary.md")
    args = parser.parse_args()
    metrics = json.loads(Path(args.metrics).read_text())
    lines = [
        "# Day 10 Reliability Final Report",
        "",
        "## Metrics Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for key, value in metrics.items():
        if key == "scenarios":
            continue
        lines.append(f"| {key} | {value} |")
    lines += ["", "## Chaos Scenarios", "", "| Scenario | Status |", "|---|---|"]
    for key, value in metrics.get("scenarios", {}).items():
        lines.append(f"| {key} | {value} |")

    by_scenario_path = Path("reports/metrics_by_scenario.json")
    if by_scenario_path.exists():
        by_scenario = json.loads(by_scenario_path.read_text())
        cols = ["availability", "fallback_success_rate", "cache_hit_rate", "circuit_open_count", "recovery_time_ms"]
        lines += ["", "## Per-scenario metrics", "", "| Scenario | " + " | ".join(cols) + " |",
                  "|---|" + "|".join(["---:"] * len(cols)) + "|"]
        for name, report in by_scenario.items():
            lines.append("| " + name + " | " + " | ".join(str(report.get(c)) for c in cols) + " |")
    lines += [
        "",
        "## Analysis TODO(student)",
        "",
        "Explain what failed, why the fallback path worked or did not work, and what you would change before production.",
    ]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(lines))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
