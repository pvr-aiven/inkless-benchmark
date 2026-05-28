# ─── Inkless Plan Migration Benchmark ────────────────────────────────────────
# Targets:
#   make setup      — install Python dependencies
#   make init       — terraform init
#   make deploy     — terraform apply (provision Inkless cluster)
#   make benchmark  — run the benchmark (all throughputs, both directions)
#   make report     — generate plots and summary from existing results/
#   make destroy    — terraform destroy (tear down the cluster)
#   make clean      — remove results/ and the CA cert

SHELL := /bin/bash
.DEFAULT_GOAL := help

ENV_FILE := .env
TERRAFORM_DIR := terraform
RESULTS_DIR := results
PYTHON := python3

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
	@echo "  Usage: make <target>"
	@echo ""
	@echo "  Targets:"
	@echo "    setup      Install Python dependencies"
	@echo "    init       Terraform init"
	@echo "    deploy     Provision the Inkless Kafka cluster (terraform apply)"
	@echo "    benchmark  Run the full benchmark (all throughputs, upgrade + downgrade)"
	@echo "    report     Generate plots and Markdown summary from results/"
	@echo "    destroy    Tear down the cluster (terraform destroy)"
	@echo "    clean      Remove results/ directory and CA cert"
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
	@echo "→ Terraform apply …"
	terraform -chdir=$(TERRAFORM_DIR) apply -auto-approve

.PHONY: destroy
destroy:
	@echo "→ Terraform destroy …"
	terraform -chdir=$(TERRAFORM_DIR) destroy -auto-approve

# ─── Benchmark ───────────────────────────────────────────────────────────────
THROUGHPUT ?= 1 5
STABILIZATION ?= 30

.PHONY: benchmark
benchmark:
	@echo "→ Running benchmark (throughput=$(THROUGHPUT) MB/s, stabilization=$(STABILIZATION)s) …"
	@mkdir -p $(RESULTS_DIR)
	$(PYTHON) -m benchmark.runner \
		--throughput $(THROUGHPUT) \
		--stabilization $(STABILIZATION)

# Run with a single throughput value (useful for quick tests)
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
