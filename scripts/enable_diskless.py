"""
Standalone script to enable kafka_diskless on the Inkless service.

Run this between `terraform apply -target=aiven_kafka.inkless` and
`terraform apply` (topic creation) when the service state is stale.

Usage:
    python -m scripts.enable_diskless
"""

import sys
import os
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from benchmark.runner import load_env, get_terraform_outputs
from benchmark.aiven_api import AivenClient


def main() -> None:
    env = load_env()
    outputs = get_terraform_outputs()

    service_name = outputs["service_name"]
    project = outputs["aiven_project"]

    client = AivenClient(token=env["token"], project=project)

    print(f"Enabling diskless storage on service '{service_name}' (project: {project}) …")
    client.ensure_service_diskless(service_name)
    print("Done — diskless is enabled, service is RUNNING.")


if __name__ == "__main__":
    main()
