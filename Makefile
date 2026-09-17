export ANSIBLE_GALAXY_CACHE_DIR ?= $(CURDIR)/.cache/ansible/galaxy
export ANSIBLE_LOCAL_TEMP ?= $(CURDIR)/.cache/ansible/local
export UV_CACHE_DIR ?= $(CURDIR)/.cache/uv

PLAYBOOKS := $(sort $(wildcard *.yml))
PYTHON ?= python3

TEST_VM_NAME ?= deburner-test
TEST_VM_URI ?= qemu:///session
TEST_VM_MEMORY_MIB ?= 8192
TEST_VM_VCPUS ?= 4
TEST_VM_DISK_GIB ?= 450
TEST_VM_STATE_ROOT ?= $(CURDIR)/.test-vm
TEST_VM_CACHE_ROOT ?= $(CURDIR)/.cache/test-vm
TEST_VM_RESULTS_ROOT ?= $(CURDIR)/.test-results
TEST_VM_IMAGE_URL ?= https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2
TEST_VM_CHECKSUMS_URL ?= https://cloud.debian.org/images/cloud/trixie/latest/SHA512SUMS
TEST_VM_COMMAND = $(PYTHON) tools/test_vm.py \
	--name "$(TEST_VM_NAME)" \
	--uri "$(TEST_VM_URI)" \
	--state-root "$(TEST_VM_STATE_ROOT)" \
	--cache-root "$(TEST_VM_CACHE_ROOT)" \
	--results-root "$(TEST_VM_RESULTS_ROOT)" \
	--image-url "$(TEST_VM_IMAGE_URL)" \
	--checksums-url "$(TEST_VM_CHECKSUMS_URL)" \
	--memory-mib "$(TEST_VM_MEMORY_MIB)" \
	--vcpus "$(TEST_VM_VCPUS)" \
	--disk-gib "$(TEST_VM_DISK_GIB)"

.PHONY: setup check check-whitespace check-yaml check-ansible-lint check-python check-syntax test \
	test-bloodhound \
	test-prerequisites test-image test-image-status test-image-purge test-create test-start test-stop \
	test-destroy test-status test-console test-wait test-provision test-verify test-reboot \
	test-idempotence test-refresh test-clean

setup:
	@command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/getting-started/installation/" >&2; exit 1; }
	uv sync --frozen

check: setup check-whitespace check-yaml check-ansible-lint check-python check-syntax

check-whitespace:
	git diff --check HEAD

check-yaml:
	uv run --frozen yamllint .

check-ansible-lint:
	uv run --frozen ansible-lint

check-python:
	uv run --frozen ruff check tools
	uv run --frozen ruff format --check tools

check-syntax:
	@set -eu; \
	for playbook in $(PLAYBOOKS); do \
		echo "Syntax checking $$playbook"; \
		uv run --frozen ansible-playbook --syntax-check "$$playbook"; \
	done

test: check
	$(TEST_VM_COMMAND) run-workflow

test-bloodhound: check
	$(TEST_VM_COMMAND) --enable-bloodhound run-workflow

test-prerequisites:
	$(TEST_VM_COMMAND) prerequisites

test-image:
	$(TEST_VM_COMMAND) image-prepare

test-image-status:
	$(TEST_VM_COMMAND) image-status

test-image-purge:
	$(TEST_VM_COMMAND) image-purge

test-create:
	$(TEST_VM_COMMAND) create

test-start:
	$(TEST_VM_COMMAND) start

test-stop:
	$(TEST_VM_COMMAND) stop

test-destroy:
	$(TEST_VM_COMMAND) destroy

test-status:
	$(TEST_VM_COMMAND) status

test-console:
	$(TEST_VM_COMMAND) console

test-wait:
	$(TEST_VM_COMMAND) wait-agent

test-provision:
	$(TEST_VM_COMMAND) provision

test-verify:
	$(TEST_VM_COMMAND) verify

test-reboot:
	$(TEST_VM_COMMAND) reboot

test-idempotence:
	$(TEST_VM_COMMAND) idempotence

test-refresh:
	$(TEST_VM_COMMAND) refresh-source

test-clean:
	$(TEST_VM_COMMAND) clean
