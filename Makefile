# Makefile

# Load .env file if it exists
include .env
export $(shell sed 's/=.*//' .env)

build:
	docker build -t cornatul/webai.ai:latest .

build-fresh:
	docker build --no-cache -t cornatul/webai.ai:latest .

up:
	docker compose up -d

up-attach:
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
