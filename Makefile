SHELL := /bin/bash
.ONESHELL:
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

.PHONY: help install doctor setup-ca base-build branch-build

help:
	@printf '%s\n' \
	  'make install       Set up an Apple Silicon Mac and build Debian desktop images' \
	  'make doctor        Check the host runtime and built images' \
	  'make branch-build  Build or refresh the desktop runtime' \
	  'uv run cu --help   Create, checkpoint, fork, and view desktops' \
	  'uv run fork-agent run --help  Run an agent on a desktop'

install:
	bash scripts/install.sh

doctor:
	bash scripts/doctor.sh

setup-ca:
	bash scripts/setup-ca.sh

BASE_TAG ?= fork-base:latest
BRANCH_TAG ?= fork-branch:latest
base-build: setup-ca
	docker buildx build --platform linux/arm64 --load -t $(BASE_TAG) -f env/base/Dockerfile .

branch-build: base-build
	docker buildx build --platform linux/arm64 --load -t $(BRANCH_TAG) --build-arg BASE_IMAGE=$(BASE_TAG) -f repl/Dockerfile .
