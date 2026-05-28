"""
Kafka consumer that tracks lag and message availability during plan migration.

Metrics captured every REPORT_INTERVAL_SEC:
- Consumer lag per partition (high watermark − committed offset)
- Messages received in the window
- Gap events: windows where zero messages were received (indicates unavailability)
- End-to-end latency: difference between production timestamp and consumption time
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List

from confluent_kafka import Consumer, KafkaError, TopicPartition

logger = logging.getLogger(__name__)

REPORT_INTERVAL_SEC = 5.0    # how often to record a consumer metric sample
POLL_TIMEOUT_SEC = 1.0       # max time to block waiting for a message


@dataclass
class ConsumerMetric:
    """One measurement sample from the consumer."""
    timestamp: float                    # Unix epoch
    messages_received: int              # cumulative messages consumed
    lag_total: int                      # total lag across all partitions
    lag_per_partition: Dict[int, int]   # partition_id → lag
    gap_detected: bool                  # True if no messages received this window
    e2e_latency_ms: float               # average end-to-end latency in this window (ms)


class BenchmarkConsumer:
    """
    Threaded Kafka consumer that records lag and availability metrics.

    Usage::

        c = BenchmarkConsumer(conf, topic, group_id="bench-cg")
        c.start()
        # … wait …
        c.stop()
        metrics = c.metrics   # list[ConsumerMetric]
    """

    def __init__(
        self,
        kafka_conf: dict,
        topic: str,
        group_id: str = "inkless-benchmark-cg",
        metrics: List[ConsumerMetric] | None = None,
    ) -> None:
        self._kafka_conf = {
            **kafka_conf,
            "group.id": group_id,
            "auto.offset.reset": "latest",   # start consuming from now, not the beginning
            "enable.auto.commit": True,
        }
        self._topic = topic
        self.metrics: List[ConsumerMetric] = metrics if metrics is not None else []

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self._total_received = 0
        self._window_received = 0
        self._window_latencies: List[float] = []
        self._lock = threading.Lock()

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="consumer")
        self._thread.start()
        logger.info("Consumer started (topic=%s)", self._topic)

    def stop(self, timeout: float = 30.0) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        logger.info("Consumer stopped — total messages received: %d", self._total_received)

    # ── Internal ─────────────────────────────────────────────────────────────

    def _get_lag(self, consumer: Consumer, partitions) -> Dict[int, int]:
        """
        Compute per-partition lag: lag = high_watermark − current_position.

        During plan migration, partition leaders move between brokers and
        get_watermark_offsets may raise NOT_LEADER_FOR_PARTITION or similar
        errors. These are transient — return an empty dict so the caller
        records a gap rather than crashing the consumer thread.
        """
        from confluent_kafka import KafkaException
        lag: Dict[int, int] = {}
        for tp in partitions:
            try:
                low, high = consumer.get_watermark_offsets(tp, timeout=5.0)
                pos_list = consumer.position([tp])
                current = pos_list[0].offset if pos_list else low
                lag[tp.partition] = max(0, high - current)
            except KafkaException as exc:
                logger.debug(
                    "Skipping lag for partition %d during migration: %s",
                    tp.partition, exc,
                )
        return lag

    def _run(self) -> None:
        consumer = Consumer(self._kafka_conf)
        consumer.subscribe([self._topic])

        report_deadline = time.time() + REPORT_INTERVAL_SEC
        window_start = time.time()
        assigned_partitions = []

        try:
            while not self._stop_event.is_set():
                try:
                    msg = consumer.poll(timeout=POLL_TIMEOUT_SEC)
                except Exception as exc:
                    # Transient errors (e.g. SSL disconnect during broker restart)
                    # are handled by librdkafka internally; log and continue.
                    logger.warning("consumer.poll exception (transient): %s", exc)
                    continue

                if msg is None:
                    pass  # timeout — no message, continue
                elif msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        pass  # end of partition, not an error
                    else:
                        logger.warning("Consumer error: %s", msg.error())
                else:
                    # Valid message
                    with self._lock:
                        self._total_received += 1
                        self._window_received += 1

                        # End-to-end latency from producer timestamp
                        ts_type, ts_value = msg.timestamp()
                        if ts_value:
                            lat = (time.time() * 1000) - ts_value
                            self._window_latencies.append(lat)

                # Track assigned partitions
                assigned_partitions = consumer.assignment()

                # Periodic metric snapshot
                now = time.time()
                if now >= report_deadline:
                    lag_map = self._get_lag(consumer, assigned_partitions) if assigned_partitions else {}
                    total_lag = sum(lag_map.values())

                    with self._lock:
                        window_count = self._window_received
                        avg_lat = (
                            sum(self._window_latencies) / len(self._window_latencies)
                            if self._window_latencies
                            else 0.0
                        )
                        self._window_received = 0
                        self._window_latencies.clear()

                    gap = window_count == 0
                    metric = ConsumerMetric(
                        timestamp=now,
                        messages_received=self._total_received,
                        lag_total=total_lag,
                        lag_per_partition=lag_map,
                        gap_detected=gap,
                        e2e_latency_ms=avg_lat,
                    )
                    self.metrics.append(metric)

                    logger.info(
                        "Consumer | recv/window %d | lag %d | gap %s | e2e_lat %.1f ms",
                        window_count,
                        total_lag,
                        "YES" if gap else "no",
                        avg_lat,
                    )

                    report_deadline = now + REPORT_INTERVAL_SEC

        finally:
            consumer.close()
