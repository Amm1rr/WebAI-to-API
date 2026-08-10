# Makefile

# Load .env file if it exists
-include .env
ifneq ("$(wildcard .env)","")
export $(shell sed 's/=.*//' .env)
endif

setup:
	python scripts/bootstrap.py

doctor:
	python scripts/doctor.py

build:
	docker build -t cornatul/webai.ai:latest .

build-fresh:
	docker build --no-cache -t cornatul/webai.ai:latest .

up:
	@test -f config.conf || { echo "ERROR: config.conf missing or is a directory. Run 'python scripts/bootstrap.py' first."; exit 1; }
	@test -f .env || { echo "ERROR: .env missing or is a directory. Run 'python scripts/bootstrap.py' first."; exit 1; }
	docker compose up -d

up-attach:
	@test -f config.conf || { echo "ERROR: config.conf missing or is a directory. Run 'python scripts/bootstrap.py' first."; exit 1; }
	@test -f .env || { echo "ERROR: .env missing or is a directory. Run 'python scripts/bootstrap.py' first."; exit 1; }
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
