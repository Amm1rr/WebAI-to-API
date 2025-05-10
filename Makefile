# Makefile

build:
	docker build -t cornatul/webai.ai:latest .

build-fresh:
	docker build --no-cache -t cornatul/webai.ai:latest .

up:
	docker-compose up -d

stop:
	docker-compose down

push:
	docker push cornatul/webai.ai:latest
