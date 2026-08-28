# Makefile

# Load .env file if it exists
-include .env
ifneq ("$(wildcard .env)","")
export $(shell sed 's/=.*//' .env)
endif

DOCKER_RUNTIME_DIR ?= ./runtime

setup:
	python scripts/bootstrap.py

doctor:
	python scripts/doctor.py

build:
	docker build --build-arg APP_UID=$${APP_UID:-1000} --build-arg APP_GID=$${APP_GID:-1000} -t cornatul/webai.ai:latest .

build-fresh:
	docker build --no-cache --build-arg APP_UID=$${APP_UID:-1000} --build-arg APP_GID=$${APP_GID:-1000} -t cornatul/webai.ai:latest .

up:
	@test -f config.conf || { echo "ERROR: config.conf missing or is a directory. Run 'python scripts/bootstrap.py' first."; exit 1; }
	@test -f .env || { echo "ERROR: .env missing or is a directory. Run 'python scripts/bootstrap.py' first."; exit 1; }
	@test -d "$(DOCKER_RUNTIME_DIR)" || { echo "ERROR: Docker runtime source '$(DOCKER_RUNTIME_DIR)' missing or is not a directory. Run 'python scripts/bootstrap.py' first."; exit 1; }
	docker compose up -d

up-attach:
	@test -f config.conf || { echo "ERROR: config.conf missing or is a directory. Run 'python scripts/bootstrap.py' first."; exit 1; }
	@test -f .env || { echo "ERROR: .env missing or is a directory. Run 'python scripts/bootstrap.py' first."; exit 1; }
	@test -d "$(DOCKER_RUNTIME_DIR)" || { echo "ERROR: Docker runtime source '$(DOCKER_RUNTIME_DIR)' missing or is not a directory. Run 'python scripts/bootstrap.py' first."; exit 1; }
	docker compose up

logs:
	docker compose logs -f web_ai

stop:
	docker compose down

down:
	docker compose down

push:
	docker push cornatul/webai.ai:latest

export-reqs:
	poetry export -f requirements.txt --output requirements.txt --without-hashes
