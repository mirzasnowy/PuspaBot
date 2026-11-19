"""Time-series store for the nightly smoke lane (E-04).

One JSON object per line (JSONL), one line per probe per run.
Each record carries the probe name, status, latency, cost, and
the provider's observed response shape — never just green/red,
and never a secret (see ``ProbeResult.to_record`` for the
whitelisted fields).

``summarize`` turns the series into per-probe behaviour: pass
rate, latency p50/p95, total cost, and whether the response
shape changed since the previous run, so a silent provider
change is visible. ``write_summary_markdown`` renders that
summary for the nightly report and the CI artifact.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "append_record",
    "append_records",
    "load_records",
    "summarize",
    "write_summary_markdown",
]


def append_record(path: str | Path, record: dict[str, Any]) -> None:
    """Append one probe record (one JSON line) to the store."""
    append_records(path, [record])


def append_records(path: str | Path, records: list[dict[str, Any]]) -> None:
    """Append several probe records to the store (one JSONL file)."""
    store_path = Path(path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    lines = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records
    )
    with store_path.open("a", encoding="utf-8") as handle:
        handle.write(lines)


def load_records(path: str | Path) -> list[dict[str, Any]]:
    """Load every record from the store, oldest first.

    A malformed line is skipped, not fatal: one bad line must
    not hide the whole series. An absent store loads as empty.
    """
    store_path = Path(path)
    if not store_path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in store_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            records.append(parsed)
    return records


def _percentile(sorted_values: list[int | float], pct: float) -> float | None:
    """Nearest-rank percentile (p50 is the median of the series)."""
    if not sorted_values:
        return None
    rank = math.ceil(pct / 100 * len(sorted_values))
    return float(sorted_values[max(0, min(rank, len(sorted_values)) - 1)])


def _shape_key(shape: dict[str, Any]) -> tuple:
    """A hashable fingerprint of a response shape.

    The fingerprint is the sorted key list plus the scalar
    values that describe behaviour (finish reason, token
    counts); a provider that starts returning a different
    finish reason or an extra tool call changes it.
    """
    return tuple(sorted((key, json.dumps(value, sort_keys=True)) for key, value in shape.items()))


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-probe behaviour over the whole series.

    For each probe: run count, pass/fail/skip counts, latency
    p50/p95/max in milliseconds, total measured cost in
    Rupiah, the latest response shape, and whether that shape
    differs from the previous run's (``shape_changed``). The
    summary also carries the run ids in order, so a report can
    say how many nights the series covers.
    """
    per_probe: dict[str, dict[str, Any]] = {}
    run_ids: list[str] = []
    for record in records:
        run_id = record.get("run_id")
        if run_id is not None and run_id not in run_ids:
            run_ids.append(str(run_id))
        name = str(record.get("probe", "unknown"))
        entry = per_probe.setdefault(
            name,
            {
                "runs": 0,
                "passes": 0,
                "fails": 0,
                "skips": 0,
                "latencies_ms": [],
                "total_cost_rp": 0,
                "shapes": [],
                "last_error": "",
                "last_timestamp": "",
            },
        )
        entry["runs"] += 1
        status = record.get("status")
        if status == "pass":
            entry["passes"] += 1
        elif status == "fail":
            entry["fails"] += 1
        elif status == "skip":
            entry["skips"] += 1
        latency = record.get("latency_ms")
        if isinstance(latency, (int, float)):
            entry["latencies_ms"].append(latency)
        cost = record.get("cost_rp")
        if isinstance(cost, (int, float)):
            entry["total_cost_rp"] += cost
        shape = record.get("response_shape")
        if isinstance(shape, dict):
            entry["shapes"].append(_shape_key(shape))
        if record.get("error"):
            entry["last_error"] = str(record["error"])
        if record.get("timestamp"):
            entry["last_timestamp"] = str(record["timestamp"])
    probes: dict[str, Any] = {}
    for name, entry in sorted(per_probe.items()):
        latencies = sorted(entry["latencies_ms"])
        shapes = entry["shapes"]
        probes[name] = {
            "runs": entry["runs"],
            "passes": entry["passes"],
            "fails": entry["fails"],
            "skips": entry["skips"],
            "latency_p50_ms": _percentile(latencies, 50),
            "latency_p95_ms": _percentile(latencies, 95),
            "latency_max_ms": latencies[-1] if latencies else None,
            "total_cost_rp": entry["total_cost_rp"],
            "shape_changed": len(shapes) >= 2 and shapes[-1] != shapes[-2],
            "last_error": entry["last_error"],
            "last_timestamp": entry["last_timestamp"],
        }
    return {
        "runs": len(run_ids),
        "run_ids": run_ids,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "probes": probes,
    }


def write_summary_markdown(path: str | Path, summary: dict[str, Any], run_id: str) -> None:
    """Render the summary as a Markdown report (CI artifact)."""
    lines = [
        "# Nightly smoke summary",
        "",
        (
            f"Run `{run_id}` — {summary['runs']} run(s) in the series, "
            f"generated {summary['generated_at']}."
        ),
        "",
        (
            "| Probe | Runs | Pass | Fail | Skip | p50 (ms) | p95 (ms) | "
            "Total cost (Rp) | Shape changed |"
        ),
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, probe in summary["probes"].items():
        p50 = probe["latency_p50_ms"]
        p95 = probe["latency_p95_ms"]
        lines.append(
            f"| {name} | {probe['runs']} | {probe['passes']} | {probe['fails']} | "
            f"{probe['skips']} | "
            f"{f'{p50:.0f}' if p50 is not None else '-'} | "
            f"{f'{p95:.0f}' if p95 is not None else '-'} | "
            f"{probe['total_cost_rp']} | "
            f"{'yes' if probe['shape_changed'] else 'no'} |"
        )
    lines.append("")
    failures = {
        name: probe["last_error"]
        for name, probe in summary["probes"].items()
        if probe["last_error"]
    }
    if failures:
        lines.append("## Last errors")
        lines.append("")
        for name, error in failures.items():
            lines.append(f"- **{name}**: {error}")
        lines.append("")
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
