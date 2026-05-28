# ─── Inkless Plan Migration Benchmark ────────────────────────────────────────
# Targets:
#   make setup           — install Python dependencies
#   make init            — terraform init
#   make deploy          — terraform apply (provision Inkless cluster)
#   make benchmark       — run the benchmark (all throughputs, both directions)
#   make report          — generate plots and summary from existing results/
#   make destroy         — terraform destroy (tear down the cluster)
#   make clean           — remove results/
#
# Overridable variables (pass on the command line):
#   FROM_PLAN    starting plan  (default: inkless-professional-3x-8-1, max 1 MB/s)
#   TO_PLAN      target plan   (default: inkless-professional-3x-8-3, max 5 MB/s)
#   THROUGHPUT   space-separated list of MB/s values (default: 1 5)
#   STABILIZATION  pre/post-migration wait in seconds (default: 30)
#
# Plan table (offering → max ingress / max egress):
#   inkless-professional-3x-8-1   1 MB/s  /   3 MB/s
#   inkless-professional-3x-8-2   3 MB/s  /   9 MB/s
#   inkless-professional-3x-8-3   5 MB/s  /  15 MB/s
#   inkless-professional-3x-16-4  10 MB/s /  30 MB/s
#   inkless-professional-3x-16-5  25 MB/s /  75 MB/s
#   inkless-professional-3x-16-6  50 MB/s / 150 MB/s
#   inkless-professional-6x-16-7  100 MB/s / 300 MB/s
#   inkless-professional-9x-16-8  200 MB/s / 600 MB/s
#   inkless-professional-6x-32-9  300 MB/s / 900 MB/s

SHELL := /bin/bash
.DEFAULT_GOAL := help

ENV_FILE      := .env
TERRAFORM_DIR := terraform
RESULTS_DIR   := results
PYTHON        := python3

# Load .env so TF_VAR_* and AIVEN_TOKEN are available to sub-processes
ifneq (,$(wildcard $(ENV_FILE)))
  include $(ENV_FILE)
  export
endif

# ─── Help ────────────────────────────────────────────────────────────────────
.PHONY: help
help:
	@echo ""
	@echo "  Inkless Plan Migration Benchmark"
	@echo ""
	@echo "  Usage: make <target> [VAR=value …]"
	@echo ""
	@echo "  Targets:"
	@echo "    setup             Install Python dependencies"
	@echo "    init              Terraform init"
	@echo "    deploy            Provision the Inkless Kafka cluster"
	@echo "    benchmark         Run the full benchmark (upgrade + downgrade)"
	@echo "    benchmark-quick   Quick single-run test (1 MB/s, 10s stabilization)"
	@echo "    report            Generate plots and Markdown summary from results/"
	@echo "    destroy           Tear down the cluster"
	@echo "    clean             Remove results/ directory"
	@echo ""
	@echo "  Variables (with defaults):"
	@echo "    FROM_PLAN=$(FROM_PLAN)"
	@echo "    TO_PLAN=$(TO_PLAN)"
	@echo "    THROUGHPUT=$(THROUGHPUT)"
	@echo "    STABILIZATION=$(STABILIZATION)"
	@echo ""
	@echo "  Example — test 5→10 MB/s migration at 3 MB/s ingress:"
	@echo "    make benchmark FROM_PLAN=inkless-professional-3x-8-3 TO_PLAN=inkless-professional-3x-16-4 THROUGHPUT=3"
	@echo ""

# ─── Setup ───────────────────────────────────────────────────────────────────
.PHONY: setup
setup:
	@echo "→ Installing Python dependencies …"
	$(PYTHON) -m pip install -r requirements.txt

# ─── Terraform ───────────────────────────────────────────────────────────────
.PHONY: init
init:
	@echo "→ Terraform init …"
	terraform -chdir=$(TERRAFORM_DIR) init

.PHONY: deploy
deploy:
	@echo "→ Terraform apply (step 1: service + user + ACLs) …"
	terraform -chdir=$(TERRAFORM_DIR) apply -auto-approve \
		-target=data.aiven_project.main \
		-target=aiven_kafka.inkless \
		-target=aiven_kafka_user.benchmark \
		-target=aiven_kafka_native_acl.benchmark_topic \
		-target=aiven_kafka_native_acl.benchmark_group
	@echo "→ Enabling diskless storage on service (API call) …"
	$(PYTHON) -m scripts.enable_diskless
	@echo "→ Terraform apply (step 2: diskless topic) …"
	terraform -chdir=$(TERRAFORM_DIR) apply -auto-approve

.PHONY: destroy
destroy:
	@echo "→ Terraform destroy …"
	terraform -chdir=$(TERRAFORM_DIR) destroy -auto-approve

# ─── Benchmark ───────────────────────────────────────────────────────────────
FROM_PLAN     ?= inkless-professional-3x-8-1
TO_PLAN       ?= inkless-professional-3x-8-3
THROUGHPUT    ?= 1 5
STABILIZATION ?= 30

.PHONY: benchmark
benchmark:
	@echo "→ Benchmark: $(FROM_PLAN) ↔ $(TO_PLAN) | throughput=$(THROUGHPUT) MB/s | stabilization=$(STABILIZATION)s"
	@mkdir -p $(RESULTS_DIR)
	$(PYTHON) -m benchmark.runner \
		--from-plan $(FROM_PLAN) \
		--to-plan   $(TO_PLAN) \
		--throughput $(THROUGHPUT) \
		--stabilization $(STABILIZATION)

# Quick single-throughput smoke test
.PHONY: benchmark-quick
benchmark-quick:
	@$(MAKE) benchmark THROUGHPUT=1 STABILIZATION=10

# ─── Report ──────────────────────────────────────────────────────────────────
.PHONY: report
report:
	@echo "→ Generating report …"
	$(PYTHON) -m benchmark.report \
		--results-dir $(RESULTS_DIR) \
		--output-dir $(RESULTS_DIR)

# ─── Clean ───────────────────────────────────────────────────────────────────
.PHONY: clean
clean:
	@echo "→ Cleaning results …"
	rm -rf $(RESULTS_DIR)
	@echo "Done."
