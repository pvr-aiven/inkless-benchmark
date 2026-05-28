"""
Report generator.

Reads all CSV files produced by the benchmark runner from the results/
directory and generates:
  - A summary Markdown table (migration durations across directions and throughputs)
  - PNG plots:
      * Producer throughput timeline with migration event markers
      * Consumer lag timeline with migration event markers
      * Bar chart comparing migration durations

Usage (from the project root):
    python -m benchmark.report [--results-dir results/] [--output-dir results/]
"""

from __future__ import annotations

import argparse
import csv
import logging
from pathlib import Path
from typing import Dict, List, Tuple

import matplotlib
matplotlib.use("Agg")  # non-interactive backend — safe for headless environments
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")

COLORS = {
    "upgrade":   "#E05A2B",   # Aiven orange-ish
    "downgrade": "#2B6CE0",   # blue
    "gap":       "#FF0000",   # red gap markers
}


# ─── CSV loaders ─────────────────────────────────────────────────────────────

def _load_csv(path: Path) -> List[dict]:
    with path.open() as f:
        return list(csv.DictReader(f))


def load_producer_csv(path: Path) -> List[dict]:
    rows = _load_csv(path)
    for r in rows:
        r["timestamp"]       = float(r["timestamp"])
        r["throughput_mbps"] = float(r["throughput_mbps"])
        r["latency_ms"]      = float(r["latency_ms"])
        r["errors"]          = int(r["errors"])
    return rows


def load_consumer_csv(path: Path) -> List[dict]:
    rows = _load_csv(path)
    for r in rows:
        r["timestamp"]      = float(r["timestamp"])
        r["lag_total"]      = int(r["lag_total"])
        r["gap_detected"]   = bool(int(r["gap_detected"]))
        r["e2e_latency_ms"] = float(r["e2e_latency_ms"])
    return rows


def load_migration_events(path: Path) -> List[dict]:
    rows = _load_csv(path)
    for r in rows:
        r["trigger_ts"]                      = float(r["trigger_ts"])
        r["duration_sec"]                    = float(r["duration_sec"])
        r["throughput_mbps"]                 = float(r["throughput_mbps"])
        r["data_ingested_during_migration_mb"] = float(r.get("data_ingested_during_migration_mb", 0))
        r["cloud_name"]                      = r.get("cloud_name", "unknown")
    return rows


# ─── Plot helpers ─────────────────────────────────────────────────────────────

def _relative_times(timestamps: List[float]) -> List[float]:
    """Convert absolute Unix timestamps to seconds from the first sample."""
    t0 = timestamps[0]
    return [t - t0 for t in timestamps]


def plot_producer_throughput(
    runs: Dict[str, List[dict]],
    migration_events: List[dict],
    output_path: Path,
) -> None:
    """One subplot per run: producer throughput (MB/s) vs time."""
    n = len(runs)
    fig, axes = plt.subplots(n, 1, figsize=(14, 4 * n), sharex=False)
    if n == 1:
        axes = [axes]

    for ax, (label, rows) in zip(axes, runs.items()):
        times  = _relative_times([r["timestamp"] for r in rows])
        values = [r["throughput_mbps"] for r in rows]
        color  = COLORS.get("upgrade" if "upgrade" in label else "downgrade", "#555")

        ax.plot(times, values, color=color, linewidth=1.5, label="Actual throughput")
        ax.set_title(label, fontsize=10)
        ax.set_ylabel("MB/s")
        ax.set_xlabel("Time (s)")
        ax.legend(fontsize=8)
        ax.grid(True, linestyle="--", alpha=0.5)

    fig.suptitle("Producer Throughput During Plan Migration", fontsize=13, fontweight="bold")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info("Saved → %s", output_path)


def plot_consumer_lag(
    runs: Dict[str, List[dict]],
    output_path: Path,
) -> None:
    """Consumer lag (total across partitions) and gap markers vs time."""
    n = len(runs)
    fig, axes = plt.subplots(n, 1, figsize=(14, 4 * n), sharex=False)
    if n == 1:
        axes = [axes]

    for ax, (label, rows) in zip(axes, runs.items()):
        times = _relative_times([r["timestamp"] for r in rows])
        lags  = [r["lag_total"] for r in rows]
        gaps  = [r["gap_detected"] for r in rows]

        ax.fill_between(times, lags, alpha=0.3, color="#2B6CE0")
        ax.plot(times, lags, color="#2B6CE0", linewidth=1.5, label="Total lag")

        # Mark gap windows with a red vertical band
        for t, g in zip(times, gaps):
            if g:
                ax.axvline(x=t, color=COLORS["gap"], alpha=0.6, linewidth=1, linestyle=":")

        gap_patch = mpatches.Patch(color=COLORS["gap"], alpha=0.6, label="Gap (no messages)")
        ax.legend(handles=[ax.lines[0], gap_patch], fontsize=8)
        ax.set_title(label, fontsize=10)
        ax.set_ylabel("Total lag (messages)")
        ax.set_xlabel("Time (s)")
        ax.grid(True, linestyle="--", alpha=0.5)

    fig.suptitle("Consumer Lag During Plan Migration", fontsize=13, fontweight="bold")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info("Saved → %s", output_path)


def plot_migration_duration_bars(events: List[dict], output_path: Path) -> None:
    """Bar chart comparing migration durations across runs."""
    labels    = [f"{e['direction']}\n{e['throughput_mbps']:.0f} MB/s" for e in events]
    durations = [e["duration_sec"] for e in events]
    colors    = [COLORS.get(e["direction"], "#888") for e in events]

    fig, ax = plt.subplots(figsize=(max(8, len(events) * 2), 5))
    bars = ax.bar(labels, durations, color=colors, edgecolor="white", width=0.6)

    for bar, dur in zip(bars, durations):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 2,
            f"{dur:.1f}s",
            ha="center", va="bottom", fontsize=9,
        )

    up_patch   = mpatches.Patch(color=COLORS["upgrade"],   label="Upgrade")
    down_patch = mpatches.Patch(color=COLORS["downgrade"], label="Downgrade")
    ax.legend(handles=[up_patch, down_patch])

    ax.set_ylabel("Migration duration (s)")
    ax.set_title(
        "Plan Migration Duration: business-8-inkless ↔ business-16-inkless",
        fontsize=12, fontweight="bold",
    )
    ax.grid(axis="y", linestyle="--", alpha=0.5)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info("Saved → %s", output_path)


# ─── Markdown summary ─────────────────────────────────────────────────────────

def write_markdown_summary(events: List[dict], output_path: Path) -> None:
    import datetime

    # Derive shared context from the first event
    cloud = events[0]["cloud_name"] if events else "unknown"
    # Parse hyperscaler and region from cloud_name (e.g. "google-europe-west1")
    parts = cloud.split("-", 1)
    hyperscaler = parts[0].capitalize() if parts else cloud
    region      = parts[1] if len(parts) > 1 else cloud

    run_date = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# Inkless Plan Migration Benchmark — Summary",
        "",
        "## Run context",
        "",
        f"| Parameter    | Value |",
        f"|--------------|-------|",
        f"| Date         | {run_date} |",
        f"| Hyperscaler  | {hyperscaler} |",
        f"| Region       | {region} |",
        f"| Cloud name   | `{cloud}` |",
        f"| From plan    | `{events[0]['from_plan']}` |" if events else "",
        f"| To plan      | `{events[0]['to_plan']}` |" if events else "",
        "",
        "## Migration results",
        "",
        "> **Migration duration** = time from the Aiven API request to the service returning to `RUNNING` state.",
        "> **Data ingested during migration** = `duration × ingress throughput` — the volume of data",
        "> written to the cluster while it was migrating. Higher values indicate a more demanding migration.",
        "",
        "| Direction  | From plan                     | To plan                       |"
        " Ingress (MB/s) | Duration (s) | Data ingested during migration (MB) |",
        "|------------|-------------------------------|-------------------------------|"
        "----------------|--------------|-------------------------------------|",
    ]

    for e in events:
        lines.append(
            f"| {e['direction']:<10} "
            f"| `{e['from_plan']}`{' ' * max(0, 29 - len(e['from_plan']))} "
            f"| `{e['to_plan']}`{' ' * max(0, 29 - len(e['to_plan']))} "
            f"| {e['throughput_mbps']:>14.1f} "
            f"| {e['duration_sec']:>12.1f} "
            f"| {e['data_ingested_during_migration_mb']:>35.1f} |"
        )

    lines += [
        "",
        "## Notes",
        "",
        "- Duration is measured server-side: from the API `PUT /service/{name}` request",
        "  to the service state returning to `RUNNING` (polled every 15 s).",
        "- Producer throughput, consumer lag, and end-to-end latency details are in the",
        "  accompanying CSV files and PNG plots.",
        "",
    ]

    output_path.write_text("\n".join(lines))
    logger.info("Markdown summary saved → %s", output_path)


# ─── Entry point ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate benchmark reports from results CSVs")
    parser.add_argument("--results-dir", type=Path, default=Path("results"),
                        help="Directory containing benchmark CSV output files")
    parser.add_argument("--output-dir",  type=Path, default=Path("results"),
                        help="Directory where plots and summary will be written")
    args = parser.parse_args()

    results_dir: Path = args.results_dir
    output_dir:  Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Locate migration events file ──────────────────────────────────────────
    event_files = sorted(results_dir.glob("*_migration_events.csv"))
    if not event_files:
        logger.error("No migration_events.csv found in %s — run the benchmark first.", results_dir)
        return
    events = load_migration_events(event_files[-1])  # latest run
    logger.info("Loaded %d migration events from %s", len(events), event_files[-1])

    # ── Load producer and consumer CSVs ───────────────────────────────────────
    producer_runs: Dict[str, List[dict]] = {}
    consumer_runs: Dict[str, List[dict]] = {}

    for prod_file in sorted(results_dir.glob("*_producer.csv")):
        label = prod_file.stem.split("_", 1)[1].replace("_producer", "")
        producer_runs[label] = load_producer_csv(prod_file)

    for cons_file in sorted(results_dir.glob("*_consumer.csv")):
        label = cons_file.stem.split("_", 1)[1].replace("_consumer", "")
        consumer_runs[label] = load_consumer_csv(cons_file)

    # ── Generate plots ────────────────────────────────────────────────────────
    if producer_runs:
        plot_producer_throughput(
            producer_runs, events,
            output_dir / "producer_throughput.png",
        )

    if consumer_runs:
        plot_consumer_lag(
            consumer_runs,
            output_dir / "consumer_lag.png",
        )

    plot_migration_duration_bars(events, output_dir / "migration_durations.png")

    # ── Write Markdown summary ────────────────────────────────────────────────
    write_markdown_summary(events, output_dir / "summary.md")

    logger.info("Report generation complete. Output in: %s", output_dir)


if __name__ == "__main__":
    main()
