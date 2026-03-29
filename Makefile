# Makefile — convenience shortcuts for Docker operations

build:
	docker compose build

build-fresh:
	docker compose build --no-cache

up:
	docker compose up -d

up-dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

down:
	docker compose down

logs:
	docker compose logs -f

pull:
	docker compose pull

restart:
	docker compose down && docker compose up -d
