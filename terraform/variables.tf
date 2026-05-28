
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

variable "cloud_name" {
  description = "Aiven cloud region (standard deployment, e.g. google-europe-west1)"
  type        = string
  default     = "google-europe-west1"
}

# ─── Service ────────────────────────────────────────────────────────────────
variable "service_name" {
  description = "Name of the Aiven Kafka service"
  type        = string
  default     = "inkless-benchmark"
}

variable "kafka_plan" {
  description = "Aiven Kafka Inkless plan to provision initially (upgrade/downgrade handled by benchmark runner)"
  type        = string
  default     = "inkless-professional-3x-8-1"
}

variable "kafka_version" {
  description = "Kafka version to deploy (must be >= 4.0 for diskless topics)"
  type        = string
  default     = "4.1"
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
  description = "Replication factor for the benchmark topic (not used — diskless topics require replication = 1, hardcoded in main.tf)"
  type        = number
  default     = 1
}

# ─── Benchmark user ─────────────────────────────────────────────────────────
variable "benchmark_username" {
  description = "Kafka username used by the benchmark producer and consumer"
  type        = string
  default     = "benchmark-user"
}
