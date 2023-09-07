#!/bin/sh
# Makefile for news.ai
build:
	docker build -t cornatul/webai.ai:latest --progress=plain .
build-fresh:
	docker build -t cornatul/webai.ai:latest --no-cache --progress=plain .
up:
	docker-compose up
stop:
	docker-compose down
push:
	docker push cornatul/webai.ai:latest

