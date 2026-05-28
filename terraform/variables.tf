
# ─── Authentication ─────────────────────────────────────────────────────────
variable "aiven_token" {
  description = "Aiven API authentication token (read from .env)"
  type        = string
  sensitive   = true
}

# ─── Project & Cloud ────────────────────────────────────────────────────────
variable "aiven_project" {
  description = "Aiven project name"
  type        = string
}

variable "custom_cloud_name" {
  description = "Aiven BYOC custom cloud identifier (e.g. byoc-gcp-europe-west1-xxxxxx)"
  type        = string
}

# ─── Service ────────────────────────────────────────────────────────────────
variable "service_name" {
  description = "Name of the Aiven Kafka service"
  type        = string
  default     = "inkless-benchmark"
}

variable "kafka_plan" {
  description = "Aiven Kafka plan to provision initially (upgrade/downgrade is handled by the benchmark runner)"
  type        = string
  default     = "business-8-inkless"
}

variable "kafka_version" {
  description = "Kafka version to deploy"
  type        = string
  default     = "3.8"
}

# ─── Topic ──────────────────────────────────────────────────────────────────
variable "topic_name" {
  description = "Benchmark Kafka topic name"
  type        = string
  default     = "inkless-benchmark-topic"
}

variable "topic_partitions" {
  description = "Number of partitions for the benchmark topic"
  type        = number
  default     = 6
}

variable "topic_replication" {
  description = "Replication factor for the benchmark topic"
  type        = number
  default     = 3
}

# ─── Benchmark user ─────────────────────────────────────────────────────────
variable "benchmark_username" {
  description = "Kafka username used by the benchmark producer and consumer"
  type        = string
  default     = "benchmark-user"
}
