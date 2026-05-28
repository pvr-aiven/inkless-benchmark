"""
Aiven REST API client.

Responsibilities:
- Trigger Kafka plan changes (upgrade / downgrade)
- Poll service state until RUNNING
- Fetch the CA certificate for SSL connections
- Ensure Kafka topics are created as diskless (Inkless) topics
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

    def _put(self, body: dict, *parts: str) -> dict:
        url = self._url(*parts)
        resp = self._session.put(url, json=body, timeout=30)
        if not resp.ok:
            raise AivenAPIError(
                f"PUT {url} returned {resp.status_code}: {resp.text}"
            )
        return resp.json()

    def _post(self, body: dict, *parts: str) -> dict:
        url = self._url(*parts)
        resp = self._session.post(url, json=body, timeout=30)
        if not resp.ok:
            raise AivenAPIError(
                f"POST {url} returned {resp.status_code}: {resp.text}"
            )
        return resp.json()

    # ── Public API ───────────────────────────────────────────────────────────

    def get_service(self, service_name: str) -> dict:
        """Return the full service object from the Aiven API."""
        return self._get("service", service_name)["service"]

    def get_service_state(self, service_name: str) -> str:
        """Return the current service state string, e.g. RUNNING, REBUILDING."""
        return self.get_service(service_name)["state"]

    def change_plan(self, service_name: str, new_plan: str) -> float:
        """
        Request a plan change on the Aiven API.

        The Aiven PUT /service/{name} endpoint requires at minimum the current
        cloud_name alongside the new plan — sending only {"plan": ...} returns 403.
        We fetch the live service object to carry over all required fields.

        Returns the Unix timestamp of the request so the caller can
        measure migration duration accurately.
        """
        logger.info("Requesting plan change → %s for service %s", new_plan, service_name)
        service = self.get_service(service_name)
        current_plan = service.get("plan")
        logger.info("Current service plan: %s", current_plan)

        if current_plan == new_plan:
            logger.warning(
                "Service is already on plan %s — no plan change needed. "
                "Check --from-plan / --to-plan arguments.",
                new_plan,
            )

        # Build the user_config for the plan change.
        # The API replaces user_config entirely, so carry over all existing settings.
        # inkless + tiered_storage are required for inkless-professional plans.
        # kafka_diskless is a legacy flag (business-*-inkless) that conflicts here.
        user_config = dict(service.get("user_config") or {})
        user_config["inkless"] = {"enabled": True}
        user_config["tiered_storage"] = {"enabled": True}
        user_config.pop("kafka_diskless", None)

        body = {"plan": new_plan, "user_config": user_config}
        logger.info("PUT body plan=%s | user_config keys=%s", new_plan, list(user_config.keys()))

        trigger_ts = time.time()
        resp = self._put(body, "service", service_name)
        confirmed_plan = resp.get("service", {}).get("plan", "unknown")
        logger.info(
            "API accepted plan change — response plan: %s (state: %s)",
            confirmed_plan,
            resp.get("service", {}).get("state", "unknown"),
        )
        return trigger_ts

    def poll_until_running(
        self,
        service_name: str,
        timeout: int = _DEFAULT_TIMEOUT,
        poll_interval: int = _DEFAULT_POLL_INTERVAL,
        start_time: Optional[float] = None,
        transition_wait: int = 120,
    ) -> float:
        """
        Block until the service completes a plan migration and returns to RUNNING.

        Two-phase polling to avoid a race condition where the service state has
        not yet left RUNNING after the plan-change API call:

          Phase 1 — wait for state to leave RUNNING (migration has started).
                     Gives up after *transition_wait* seconds and moves on.
          Phase 2 — wait for state to return to RUNNING (migration complete).

        Returns the duration in seconds from *start_time* (or now) until
        the service is RUNNING again.

        Raises TimeoutError if the service has not recovered within *timeout* seconds.
        """
        t0 = start_time or time.time()
        deadline = t0 + timeout

        # ── Phase 1: wait for state to leave RUNNING ──────────────────────────
        transition_deadline = t0 + transition_wait
        logger.info("Phase 1: waiting for service to leave RUNNING …")
        while True:
            state = self.get_service_state(service_name)
            if state != "RUNNING":
                logger.info("  Service entered state: %s — migration started", state)
                break
            if time.time() > transition_deadline:
                logger.warning(
                    "Service still RUNNING after %ds — migration may not have started yet; "
                    "proceeding to Phase 2 anyway",
                    transition_wait,
                )
                break
            time.sleep(poll_interval)

        # ── Phase 2: wait for state to return to RUNNING ──────────────────────
        logger.info("Phase 2: waiting for service to return to RUNNING …")
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

    def ensure_service_diskless(self, service_name: str) -> None:
        """
        Ensure the Kafka service has the diskless feature enabled.

        Diskless topics cannot be created unless kafka_diskless.enabled=true is set
        in the service user_config. This call is idempotent: if diskless is already
        enabled it logs the fact and returns immediately.

        If it needs to be enabled, the method PUTs the updated user_config and then
        polls until the service returns to RUNNING before returning.
        """
        logger.info("Checking service diskless status for '%s' …", service_name)
        service = self.get_service(service_name)
        user_config = dict(service.get("user_config") or {})

        kafka_diskless = user_config.get("kafka_diskless") or {}
        if kafka_diskless.get("enabled"):
            logger.info("  Service already has diskless enabled — no update needed.")
            return

        logger.info("  Enabling kafka_diskless on service '%s' …", service_name)
        user_config["kafka_diskless"] = {"enabled": True}
        # Preserve required inkless-professional flags
        user_config["inkless"] = {"enabled": True}
        user_config["tiered_storage"] = {"enabled": True}

        body = {"user_config": user_config}
        resp = self._put(body, "service", service_name)
        logger.info(
            "  Service update accepted — state: %s",
            resp.get("service", {}).get("state", "unknown"),
        )

        logger.info("  Waiting for service to return to RUNNING after diskless enable …")
        self.poll_until_running(service_name)

    def get_topic(self, service_name: str, topic_name: str) -> dict:
        """Return the full topic object from the Aiven API."""
        return self._get("service", service_name, "topic", topic_name)["topic"]

    def ensure_topic_diskless(self, service_name: str, topic_name: str) -> None:
        """
        Ensure the Kafka topic is configured as a diskless (Inkless) topic.

        The Aiven Terraform provider does not yet expose the diskless/inkless flag
        at the topic level, so we call the REST API directly after topic creation.

        The topic update endpoint is:
            PUT /v1/project/{project}/service/{service}/topic/{topic}

        The field that makes a topic store data in object storage only (no local disk)
        is {"inkless": true} at the topic body level.

        We first fetch the current topic config to log its state, then issue the
        update so this call is idempotent (safe to call even if already diskless).
        """
        logger.info("Checking topic diskless status for '%s' …", topic_name)
        try:
            topic = self.get_topic(service_name, topic_name)
            current_inkless = topic.get("inkless", False)
            logger.info(
                "  Topic '%s': inkless=%s (config keys: %s)",
                topic_name, current_inkless, list(topic.keys()),
            )
            if current_inkless:
                logger.info("  Topic is already diskless — no update needed.")
                return
        except Exception as exc:
            logger.warning("Could not fetch topic details: %s — proceeding with update anyway", exc)

        logger.info("  Setting topic '%s' to diskless (inkless=true) …", topic_name)
        try:
            resp = self._put({"inkless": True}, "service", service_name, "topic", topic_name)
            logger.info("  Topic update response: %s", resp)
            logger.info("  Topic '%s' is now diskless.", topic_name)
        except AivenAPIError as exc:
            # The API may reject the field name — log the full error so we can
            # identify the correct field on the first attempt.
            logger.error(
                "  Failed to set topic diskless via PUT with inkless=true: %s\n"
                "  Will retry with 'remote_storage_enable' variant …",
                exc,
            )
            # Fallback: try the alternative field name used in some API versions
            try:
                resp = self._put(
                    {"config": {"remote_storage_enable": True}},
                    "service", service_name, "topic", topic_name,
                )
                logger.info("  Fallback update response: %s", resp)
            except AivenAPIError as exc2:
                logger.error(
                    "  Fallback also failed: %s — topic may not be diskless. "
                    "Inspect the API response above to identify the correct field.",
                    exc2,
                )
