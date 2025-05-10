# Makefile

# Load .env file if it exists
include .env
export $(shell sed 's/=.*//' .env)

build:
	docker build -t cornatul/webai.ai:latest .

build-fresh:
	docker build --no-cache -t cornatul/webai.ai:latest .

up:
	@if [ "$(ENVIRONMENT)" = "development" ]; then \
		printf "\033[1;33m🧪 Running in DEVELOPMENT mode...\033[0m\n"; \
		docker-compose up; \
	else \
		printf "\033[0;37m🚀 Running in PRODUCTION mode...\033[0m\n"; \
		docker-compose up -d; \
	fi

stop:
	docker-compose down

down:
	docker-compose down

push:
	docker push cornatul/webai.ai:latest
