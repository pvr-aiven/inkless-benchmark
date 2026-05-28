
terraform {
  required_version = ">= 1.5"

  required_providers {
    aiven = {
      source  = "aiven/aiven"
      version = "~> 4.40"  # diskless_enable on aiven_kafka_topic requires >= 4.40
    }
  }
}

# ─── Provider ────────────────────────────────────────────────────────────────
provider "aiven" {
  api_token = var.aiven_token
}

# ─── Project (exposes the CA certificate used for SSL connections) ────────────
data "aiven_project" "main" {
  project = var.aiven_project
}

# ─── Kafka Service (Inkless, standard Aiven cloud) ───────────────────────────
resource "aiven_kafka" "inkless" {
  project      = var.aiven_project
  cloud_name   = var.cloud_name
  plan         = var.kafka_plan
  service_name = var.service_name

  kafka_user_config {
    kafka_version = var.kafka_version

    # Required for Inkless Professional plans.
    inkless {
      enabled = true
    }

    tiered_storage {
      enabled = true
    }

    # Enables diskless topic creation on the service.
    # Required before any aiven_kafka_topic with diskless_enable=true can be created.
    # Also requires kafka_version >= 4.0 (set in variables.tf).
    kafka_diskless {
      enabled = true
    }

    # Expose Kafka REST API for observability (optional but useful for debugging)
    kafka_rest = true

    kafka {
      # Allow auto topic creation to be disabled — topics are managed explicitly
      auto_create_topics_enable = false

      # Retain messages long enough to survive plan migration
      log_retention_hours = 24
    }
  }
}

# ─── Benchmark Topic ─────────────────────────────────────────────────────────
resource "aiven_kafka_topic" "benchmark" {
  project      = var.aiven_project
  service_name = aiven_kafka.inkless.service_name
  topic_name   = var.topic_name
  partitions   = var.topic_partitions

  # Diskless topics do not use broker-managed replication — replication must be 1.
  replication = 1

  config {
    # diskless_enable = true marks the topic as diskless (Inkless) at creation time.
    # Data is stored directly in cloud object storage, bypassing local broker disks.
    # Immutable: cannot be changed after topic creation.
    # Requires provider >= 4.40. Run `terraform init -upgrade` if the apply fails.
    diskless_enable = true

    # Retain data for 24 h — enough for the full benchmark run
    retention_ms    = "86400000"
    retention_bytes = "-1"
    cleanup_policy  = "delete"
  }
}

# ─── Benchmark Kafka User ────────────────────────────────────────────────────
resource "aiven_kafka_user" "benchmark" {
  project      = var.aiven_project
  service_name = aiven_kafka.inkless.service_name
  username     = var.benchmark_username
}

# ─── Kafka Native ACLs ───────────────────────────────────────────────────────
# aiven_kafka_acl only covers topic-level access in Aiven's simplified ACL model.
# Consumer group authorization requires native Kafka ACLs (aiven_kafka_native_acl).

# Allow all operations on the benchmark topic (produce + consume)
resource "aiven_kafka_native_acl" "benchmark_topic" {
  project         = var.aiven_project
  service_name    = aiven_kafka.inkless.service_name
  host            = "*"
  operation       = "All"
  pattern_type    = "LITERAL"
  permission_type = "ALLOW"
  principal       = "User:${var.benchmark_username}"
  resource_name   = var.topic_name
  resource_type   = "Topic"
}

# Allow all operations on the benchmark consumer group (resolves GROUP_AUTHORIZATION_FAILED)
# PREFIXED matches "inkless-benchmark-cg" and any other group sharing this prefix
resource "aiven_kafka_native_acl" "benchmark_group" {
  project         = var.aiven_project
  service_name    = aiven_kafka.inkless.service_name
  host            = "*"
  operation       = "All"
  pattern_type    = "PREFIXED"
  permission_type = "ALLOW"
  principal       = "User:${var.benchmark_username}"
  resource_name   = "inkless-benchmark"
  resource_type   = "Group"
}
