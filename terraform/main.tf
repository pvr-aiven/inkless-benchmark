
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

# ─── Kafka Service (Inkless, BYOC GCP europe-west1) ─────────────────────────
resource "aiven_kafka" "inkless" {
  project      = var.aiven_project
  cloud_name   = var.custom_cloud_name
  plan         = var.kafka_plan
  service_name = var.service_name

  kafka_user_config {
    kafka_version = var.kafka_version

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
