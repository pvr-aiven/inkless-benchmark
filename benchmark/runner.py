"""
Benchmark runner — main orchestrator.

Workflow for each (throughput, direction) combination:
  1. Read Terraform outputs to get connection parameters.
  2. Fetch CA certificate from the Aiven API.
  3. Start producer and consumer threads.
  4. Wait for a stabilization period.
  5. Trigger the plan change (upgrade or downgrade) via the Aiven API.
  6. Poll until the service is RUNNING again; record migration duration.
  7. Wait for another stabilization period, then stop producer and consumer.
  8. Persist raw metrics to CSV in the results/ directory.
  9. Repeat for the reverse direction.

Usage (from the project root):
    python -m benchmark.runner [--throughput 1 5] [--stabilization 30]
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import List

from dotenv import load_dotenv

from benchmark.aiven_api import AivenClient
from benchmark.consumer import BenchmarkConsumer, ConsumerMetric
from benchmark.producer import BenchmarkProducer, ProducerMetric

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─── Plan configuration ───────────────────────────────────────────────────────
INITIAL_PLAN  = "business-8-inkless"
UPGRADED_PLAN = "business-16-inkless"

# ─── Paths ───────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
TERRAFORM_DIR = PROJECT_ROOT / "terraform"
RESULTS_DIR = PROJECT_ROOT / "results"
CA_CERT_PATH     = PROJECT_ROOT / "results" / "ca.pem"      # written by write_ssl_files


# ─── Helpers ─────────────────────────────────────────────────────────────────

def load_env() -> dict:
    """Load .env from the project root and return the relevant variables."""
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        logger.error(".env file not found at %s", env_file)
        sys.exit(1)
    load_dotenv(dotenv_path=env_file)

    token = os.environ.get("AIVEN_TOKEN")
    if not token:
        logger.error("AIVEN_TOKEN not set in .env")
        sys.exit(1)

    return {"token": token}


def get_terraform_outputs() -> dict:
    """
    Run `terraform output -json` inside the terraform/ directory and
    parse the result into a plain dict.

    Returns a dict with keys matching the output names in outputs.tf.
    """
    logger.info("Reading Terraform outputs …")
    result = subprocess.run(
        ["terraform", "output", "-json"],
        cwd=str(TERRAFORM_DIR),
        capture_output=True,
        text=True,
        check=True,
    )
    raw = json.loads(result.stdout)
    # Unwrap the Terraform output envelope: {"key": {"value": ..., "type": ...}}
    return {k: v["value"] for k, v in raw.items()}


CLIENT_CERT_PATH = PROJECT_ROOT / "results" / "client.cert"
CLIENT_KEY_PATH  = PROJECT_ROOT / "results" / "client.key"


def write_ssl_files(outputs: dict) -> tuple[str, str, str]:
    """
    Write the CA cert, client cert, and client key PEM files to results/.

    Aiven Kafka uses mutual TLS (mTLS): the server validates the client
    certificate in addition to the CA. All three files come from Terraform
    outputs so no manual download is needed.

    Returns (ca_cert_path, client_cert_path, client_key_path).
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    CA_CERT_PATH.write_text(outputs["ca_cert"])
    logger.info("CA cert written to %s", CA_CERT_PATH)

    CLIENT_CERT_PATH.write_text(outputs["kafka_access_cert"])
    logger.info("Client cert written to %s", CLIENT_CERT_PATH)

    CLIENT_KEY_PATH.write_text(outputs["kafka_access_key"])
    logger.info("Client key written to %s", CLIENT_KEY_PATH)

    return str(CA_CERT_PATH), str(CLIENT_CERT_PATH), str(CLIENT_KEY_PATH)


def _base_ssl_conf(outputs: dict, ca_cert_path: str, client_cert_path: str, client_key_path: str) -> dict:
    """Shared mTLS settings used by both producer and consumer."""
    return {
        "bootstrap.servers": outputs["bootstrap_servers"],
        "security.protocol": "SSL",
        "ssl.ca.location": ca_cert_path,
        "ssl.certificate.location": client_cert_path,
        "ssl.key.location": client_key_path,
    }


def build_producer_conf(outputs: dict, ca_cert_path: str, client_cert_path: str, client_key_path: str) -> dict:
    """
    Kafka producer configuration.

    Aiven Kafka requires mutual TLS (mTLS): security.protocol=SSL with
    the project CA cert plus the per-user client certificate and key.
    Producer-specific reliability settings are added on top of the base SSL conf.
    """
    return {
        **_base_ssl_conf(outputs, ca_cert_path, client_cert_path, client_key_path),
        "acks": "all",
        "retries": 10,
        "retry.backoff.ms": 500,
    }


def build_consumer_conf(outputs: dict, ca_cert_path: str, client_cert_path: str, client_key_path: str) -> dict:
    """Kafka consumer configuration (mTLS only — no producer-specific keys)."""
    return _base_ssl_conf(outputs, ca_cert_path, client_cert_path, client_key_path)


def save_producer_metrics(metrics: List[ProducerMetric], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "timestamp", "messages_sent", "bytes_sent", "errors",
            "latency_ms", "throughput_mbps",
        ])
        for m in metrics:
            writer.writerow([
                m.timestamp, m.messages_sent, m.bytes_sent,
                m.errors, m.latency_ms, m.throughput_mbps,
            ])
    logger.info("Producer metrics saved → %s", path)


def save_consumer_metrics(metrics: List[ConsumerMetric], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "timestamp", "messages_received", "lag_total",
            "gap_detected", "e2e_latency_ms",
        ])
        for m in metrics:
            writer.writerow([
                m.timestamp, m.messages_received, m.lag_total,
                int(m.gap_detected), m.e2e_latency_ms,
            ])
    logger.info("Consumer metrics saved → %s", path)


def save_migration_event(events: list, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "direction", "from_plan", "to_plan",
            "trigger_ts", "duration_sec", "throughput_mbps",
        ])
        writer.writerows(events)
    logger.info("Migration events saved → %s", path)


# ─── Core benchmark logic ─────────────────────────────────────────────────────

def run_single_benchmark(
    *,
    throughput_mbps: float,
    from_plan: str,
    to_plan: str,
    direction: str,           # "upgrade" or "downgrade"
    producer_conf: dict,
    consumer_conf: dict,
    topic: str,
    aiven_client: AivenClient,
    service_name: str,
    stabilization_sec: int,
    run_label: str,
) -> dict:
    """
    Execute one benchmark run (single throughput + single direction).

    Returns a summary dict with migration duration and aggregate metrics.
    """
    logger.info(
        "=== Starting run: %s | %.1f MB/s | %s → %s ===",
        run_label, throughput_mbps, from_plan, to_plan,
    )

    producer_metrics: List[ProducerMetric] = []
    consumer_metrics: List[ConsumerMetric] = []

    producer = BenchmarkProducer(producer_conf, topic, throughput_mbps, metrics=producer_metrics)
    consumer = BenchmarkConsumer(consumer_conf, topic, metrics=consumer_metrics)

    try:
        # 1. Start producer and consumer
        producer.start()
        consumer.start()

        # 2. Stabilization before migration
        logger.info("Pre-migration stabilization: %d s …", stabilization_sec)
        time.sleep(stabilization_sec)

        # 3. Trigger plan change
        trigger_ts = aiven_client.change_plan(service_name, to_plan)
        logger.info("Plan change triggered at %.3f (Unix)", trigger_ts)

        # 4. Poll until RUNNING
        duration_sec = aiven_client.poll_until_running(
            service_name, start_time=trigger_ts
        )
        logger.info("Migration complete: %.1f s", duration_sec)

        # 5. Post-migration stabilization
        logger.info("Post-migration stabilization: %d s …", stabilization_sec)
        time.sleep(stabilization_sec)

    finally:
        producer.stop()
        consumer.stop()

    # 6. Persist raw metrics
    ts_label = time.strftime("%Y%m%dT%H%M%S")
    base = RESULTS_DIR / f"{ts_label}_{run_label}"
    save_producer_metrics(producer_metrics, base.with_name(base.name + "_producer.csv"))
    save_consumer_metrics(consumer_metrics, base.with_name(base.name + "_consumer.csv"))

    # 7. Compute summary
    gap_count = sum(1 for m in consumer_metrics if m.gap_detected)
    avg_producer_mbps = (
        sum(m.throughput_mbps for m in producer_metrics) / len(producer_metrics)
        if producer_metrics else 0.0
    )
    max_lag = max((m.lag_total for m in consumer_metrics), default=0)

    summary = {
        "run_label": run_label,
        "direction": direction,
        "from_plan": from_plan,
        "to_plan": to_plan,
        "throughput_mbps": throughput_mbps,
        "trigger_ts": trigger_ts,
        "migration_duration_sec": duration_sec,
        "consumer_gaps": gap_count,
        "avg_producer_mbps": avg_producer_mbps,
        "max_consumer_lag": max_lag,
    }
    logger.info("Run summary: %s", summary)
    return summary


# ─── Entry point ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Inkless plan migration benchmark runner")
    parser.add_argument(
        "--throughput", nargs="+", type=float, default=[1.0, 5.0],
        help="Target producer throughput in MB/s (default: 1 5)",
    )
    parser.add_argument(
        "--stabilization", type=int, default=30,
        help="Seconds to wait before and after each plan change (default: 30)",
    )
    args = parser.parse_args()

    # ── Load environment ──────────────────────────────────────────────────────
    env = load_env()
    outputs = get_terraform_outputs()

    service_name = outputs["service_name"]
    topic = outputs["topic_name"]
    aiven_project = outputs["aiven_project"]

    logger.info("Service: %s | Topic: %s | Project: %s", service_name, topic, aiven_project)

    # ── Aiven API client ──────────────────────────────────────────────────────
    client = AivenClient(token=env["token"], project=aiven_project)

    # ── Write SSL files (CA cert + client cert/key from Terraform outputs) ────
    ca_cert_path, client_cert_path, client_key_path = write_ssl_files(outputs)

    # ── Build Kafka configs (separate to avoid rdkafka producer-key warnings) ──
    producer_conf = build_producer_conf(outputs, ca_cert_path, client_cert_path, client_key_path)
    consumer_conf = build_consumer_conf(outputs, ca_cert_path, client_cert_path, client_key_path)

    # ── Verify initial plan ───────────────────────────────────────────────────
    current_plan = client.get_current_plan(service_name)
    logger.info("Current service plan: %s", current_plan)
    if current_plan != INITIAL_PLAN:
        logger.warning(
            "Expected initial plan %s but got %s. "
            "Ensure the service is at the initial plan before running.",
            INITIAL_PLAN, current_plan,
        )

    # ── Run all combinations ──────────────────────────────────────────────────
    all_summaries = []
    migration_events = []

    for mbps in args.throughput:
        # Round-trip: upgrade then downgrade
        for direction, from_plan, to_plan in [
            ("upgrade",   INITIAL_PLAN,  UPGRADED_PLAN),
            ("downgrade", UPGRADED_PLAN, INITIAL_PLAN),
        ]:
            label = f"{direction}_{int(mbps)}mbps"
            summary = run_single_benchmark(
                throughput_mbps=mbps,
                from_plan=from_plan,
                to_plan=to_plan,
                direction=direction,
                producer_conf=producer_conf,
                consumer_conf=consumer_conf,
                topic=topic,
                aiven_client=client,
                service_name=service_name,
                stabilization_sec=args.stabilization,
                run_label=label,
            )
            all_summaries.append(summary)
            migration_events.append([
                direction, from_plan, to_plan,
                summary["trigger_ts"], summary["migration_duration_sec"], mbps,
            ])

            # Brief pause between consecutive migrations
            logger.info("Pausing 60 s between migrations …")
            time.sleep(60)

    # ── Save consolidated migration events ────────────────────────────────────
    ts_label = time.strftime("%Y%m%dT%H%M%S")
    save_migration_event(
        migration_events,
        RESULTS_DIR / f"{ts_label}_migration_events.csv",
    )

    # ── Print final summary table ─────────────────────────────────────────────
    logger.info("\n%s", "=" * 70)
    logger.info("BENCHMARK COMPLETE — SUMMARY")
    logger.info("%s", "=" * 70)
    header = f"{'Direction':<12} {'Throughput':>12} {'Duration (s)':>14} {'Gaps':>6} {'Max Lag':>10}"
    logger.info(header)
    logger.info("%s", "-" * 70)
    for s in all_summaries:
        logger.info(
            "%-12s  %10.1f MB/s  %12.1f s  %6d  %10d",
            s["direction"], s["throughput_mbps"], s["migration_duration_sec"],
            s["consumer_gaps"], s["max_consumer_lag"],
        )
    logger.info("%s", "=" * 70)


if __name__ == "__main__":
    main()
