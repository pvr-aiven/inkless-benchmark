"""
Rate-controlled Kafka producer.

Produces messages at a target throughput (MB/s) and records per-message
latency, send errors, and actual achieved throughput.

Design notes:
- Message payload is random bytes padded to MESSAGE_SIZE_BYTES.
- Rate control uses a token-bucket approach: the producer sleeps between
  batches to honor the target MB/s without busy-waiting.
- Metrics are written to a shared list passed from the runner, so they
  can be collected even if the producer is interrupted mid-run.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import List

from confluent_kafka import Producer, KafkaError

logger = logging.getLogger(__name__)

# ─── Tunable constants ───────────────────────────────────────────────────────
MESSAGE_SIZE_BYTES = 10_000        # 10 KB per message
BATCH_SIZE_MESSAGES = 100          # messages per batch before a rate-control sleep
REPORT_INTERVAL_SEC = 5.0          # how often to log a throughput snapshot


@dataclass
class ProducerMetric:
    """One measurement sample from the producer."""
    timestamp: float           # Unix epoch
    messages_sent: int         # cumulative messages successfully acked
    bytes_sent: int            # cumulative bytes acked
    errors: int                # cumulative delivery errors
    latency_ms: float          # average ack latency for this sample window (ms)
    throughput_mbps: float     # MB/s achieved in this sample window


class BenchmarkProducer:
    """
    Threaded Kafka producer that targets a given MB/s throughput.

    Usage::

        p = BenchmarkProducer(conf, topic, target_mbps=1.0)
        p.start()
        # … wait …
        p.stop()
        metrics = p.metrics   # list[ProducerMetric]
    """

    def __init__(
        self,
        kafka_conf: dict,
        topic: str,
        target_mbps: float,
        metrics: List[ProducerMetric] | None = None,
    ) -> None:
        self._kafka_conf = kafka_conf
        self._topic = topic
        self._target_mbps = target_mbps
        self.metrics: List[ProducerMetric] = metrics if metrics is not None else []

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        # Cumulative counters (updated inside the producer thread)
        self._total_messages = 0
        self._total_bytes = 0
        self._total_errors = 0
        self._latencies: List[float] = []   # per-window ack latencies in ms
        self._lock = threading.Lock()

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the producer in a background thread."""
        self._thread = threading.Thread(target=self._run, daemon=True, name="producer")
        self._thread.start()
        logger.info("Producer started (target %.1f MB/s, topic=%s)", self._target_mbps, self._topic)

    def stop(self, timeout: float = 30.0) -> None:
        """Signal the producer to stop and wait for the thread to finish."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        logger.info(
            "Producer stopped — total messages: %d, total bytes: %d, errors: %d",
            self._total_messages,
            self._total_bytes,
            self._total_errors,
        )

    # ── Internal ─────────────────────────────────────────────────────────────

    def _delivery_callback(self, err, msg) -> None:
        """Called by confluent-kafka for every produced message."""
        with self._lock:
            if err:
                self._total_errors += 1
                logger.warning("Delivery error: %s", err)
            else:
                self._total_messages += 1
                self._total_bytes += len(msg)
                # latency in ms: difference between now and message timestamp
                if msg.timestamp()[1]:
                    lat = (time.time() * 1000) - msg.timestamp()[1]
                    self._latencies.append(lat)

    def _run(self) -> None:
        """Main producer loop — runs in the background thread."""
        producer = Producer(self._kafka_conf)
        payload = os.urandom(MESSAGE_SIZE_BYTES)

        target_bytes_per_sec = self._target_mbps * 1024 * 1024
        target_bytes_per_batch = BATCH_SIZE_MESSAGES * MESSAGE_SIZE_BYTES
        # How long one batch should take to stay on target
        batch_target_sec = target_bytes_per_batch / target_bytes_per_sec

        report_deadline = time.time() + REPORT_INTERVAL_SEC
        window_start = time.time()
        window_bytes = 0
        window_messages = 0

        while not self._stop_event.is_set():
            batch_start = time.time()

            for _ in range(BATCH_SIZE_MESSAGES):
                if self._stop_event.is_set():
                    break
                try:
                    producer.produce(
                        self._topic,
                        value=payload,
                        timestamp=int(time.time() * 1000),
                        on_delivery=self._delivery_callback,
                    )
                except BufferError:
                    # Internal queue full — flush and retry
                    producer.poll(0.1)
                    producer.produce(
                        self._topic,
                        value=payload,
                        timestamp=int(time.time() * 1000),
                        on_delivery=self._delivery_callback,
                    )

            producer.poll(0)   # trigger delivery callbacks without blocking

            # Rate control: sleep for the remaining batch budget
            elapsed = time.time() - batch_start
            sleep_time = batch_target_sec - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

            window_bytes += target_bytes_per_batch
            window_messages += BATCH_SIZE_MESSAGES

            # Periodic throughput snapshot
            now = time.time()
            if now >= report_deadline:
                window_elapsed = now - window_start
                actual_mbps = (window_bytes / 1024 / 1024) / window_elapsed if window_elapsed > 0 else 0

                with self._lock:
                    avg_lat = (sum(self._latencies) / len(self._latencies)) if self._latencies else 0.0
                    self._latencies.clear()
                    errs = self._total_errors

                metric = ProducerMetric(
                    timestamp=now,
                    messages_sent=self._total_messages,
                    bytes_sent=self._total_bytes,
                    errors=errs,
                    latency_ms=avg_lat,
                    throughput_mbps=actual_mbps,
                )
                self.metrics.append(metric)
                logger.info(
                    "Producer | %.2f MB/s | lat_avg %.1f ms | errors %d",
                    actual_mbps,
                    avg_lat,
                    errs,
                )

                # Reset window
                window_start = now
                window_bytes = 0
                window_messages = 0
                report_deadline = now + REPORT_INTERVAL_SEC

        # Flush remaining messages before exiting
        logger.info("Producer flushing remaining messages …")
        producer.flush(timeout=30)
