"""
Aiven REST API client.

Responsibilities:
- Trigger Kafka plan changes (upgrade / downgrade)
- Poll service state until RUNNING
- Fetch the CA certificate for SSL connections
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.aiven.io/v1"
_DEFAULT_POLL_INTERVAL = 15   # seconds between state checks
_DEFAULT_TIMEOUT = 3600       # max seconds to wait for migration to complete


class AivenAPIError(Exception):
    """Raised when the Aiven API returns an unexpected response."""


class AivenClient:
    """Thin wrapper around the Aiven REST API."""

    def __init__(self, token: str, project: str) -> None:
        self._project = project
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
        )

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _url(self, *parts: str) -> str:
        return "/".join([_BASE_URL, "project", self._project, *parts])

    def _get(self, *parts: str) -> dict:
        url = self._url(*parts)
        resp = self._session.get(url, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _patch(self, body: dict, *parts: str) -> dict:
        url = self._url(*parts)
        resp = self._session.patch(url, json=body, timeout=30)
        resp.raise_for_status()
        return resp.json()

    # ── Public API ───────────────────────────────────────────────────────────

    def get_service(self, service_name: str) -> dict:
        """Return the full service object from the Aiven API."""
        return self._get("service", service_name)["service"]

    def get_service_state(self, service_name: str) -> str:
        """Return the current service state string, e.g. RUNNING, REBUILDING."""
        return self.get_service(service_name)["state"]

    def get_ca_cert(self, service_name: str) -> str:
        """
        Fetch the CA certificate PEM string for the given service.

        The certificate is used by the Kafka producer and consumer to
        establish a trusted SSL connection.
        """
        data = self._get("service", service_name, "ca")
        return data["certificate"]

    def change_plan(self, service_name: str, new_plan: str) -> float:
        """
        Request a plan change on the Aiven API.

        Returns the Unix timestamp of the request so the caller can
        measure migration duration accurately.
        """
        logger.info("Requesting plan change → %s for service %s", new_plan, service_name)
        trigger_ts = time.time()
        self._patch({"plan": new_plan}, "service", service_name)
        return trigger_ts

    def poll_until_running(
        self,
        service_name: str,
        timeout: int = _DEFAULT_TIMEOUT,
        poll_interval: int = _DEFAULT_POLL_INTERVAL,
        start_time: Optional[float] = None,
    ) -> float:
        """
        Block until the service state returns to RUNNING.

        Returns the duration in seconds from *start_time* (or now) until
        the service is RUNNING again.

        Raises TimeoutError if the service has not recovered within *timeout* seconds.
        """
        t0 = start_time or time.time()
        deadline = t0 + timeout

        while True:
            state = self.get_service_state(service_name)
            elapsed = time.time() - t0
            logger.info("  Service state: %-15s  elapsed: %.0fs", state, elapsed)

            if state == "RUNNING":
                logger.info("Service is RUNNING after %.1f s", elapsed)
                return elapsed

            if time.time() > deadline:
                raise TimeoutError(
                    f"Service {service_name!r} did not reach RUNNING within {timeout}s "
                    f"(last state: {state})"
                )

            time.sleep(poll_interval)

    def get_current_plan(self, service_name: str) -> str:
        """Return the plan currently applied to the service."""
        return self.get_service(service_name)["plan"]
