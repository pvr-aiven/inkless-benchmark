
terraform {
  required_version = ">= 1.5"

  required_providers {
    aiven = {
      source  = "aiven/aiven"
      version = "~> 4.0"
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

# ─── Kafka Service (Inkless, BYOC GCP europe-west1) ─────────────────────────
resource "aiven_kafka" "inkless" {
  project      = var.aiven_project
  cloud_name   = var.custom_cloud_name
  plan         = var.kafka_plan
  service_name = var.service_name

  kafka_user_config {
    kafka_version = var.kafka_version

    # Required for Inkless plans — enables diskless (object-storage-backed) mode
    kafka_diskless {
      enabled = true
    }

    # Tiered storage must be enabled alongside diskless for Inkless plans
    tiered_storage {
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
  replication  = var.topic_replication

  config {
    # Retain data for 24 h — enough for the full benchmark run
    retention_ms      = "86400000"
    # Unlimited retention bytes (rely on time-based retention)
    retention_bytes   = "-1"
    # Compact + delete: keep last value per key and purge old segments
    cleanup_policy    = "delete"
    # 64 MB segment size — balanced for Inkless object-storage writes
    segment_bytes     = "67108864"
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
