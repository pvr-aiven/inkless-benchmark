
# ─── Service connectivity ─────────────────────────────────────────────────────
output "service_name" {
  description = "Aiven Kafka service name"
  value       = aiven_kafka.inkless.service_name
}

output "bootstrap_servers" {
  description = "Kafka bootstrap server address (host:port)"
  value       = "${aiven_kafka.inkless.service_host}:${aiven_kafka.inkless.service_port}"
}

output "service_host" {
  description = "Kafka service hostname"
  value       = aiven_kafka.inkless.service_host
}

output "service_port" {
  description = "Kafka service port"
  value       = aiven_kafka.inkless.service_port
}

# ─── Authentication ───────────────────────────────────────────────────────────
output "kafka_username" {
  description = "Benchmark Kafka username"
  value       = aiven_kafka_user.benchmark.username
}

output "kafka_password" {
  description = "Benchmark Kafka user password (sensitive)"
  value       = aiven_kafka_user.benchmark.password
  sensitive   = true
}

# ─── Project context (needed by the runner to call the Aiven API) ────────────
output "aiven_project" {
  description = "Aiven project name"
  value       = var.aiven_project
}

output "current_plan" {
  description = "Currently provisioned Kafka plan"
  value       = aiven_kafka.inkless.plan
}

# ─── Topic ───────────────────────────────────────────────────────────────────
output "topic_name" {
  description = "Benchmark Kafka topic name"
  value       = aiven_kafka_topic.benchmark.topic_name
}
