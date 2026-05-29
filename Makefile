-include .env.local

.DEFAULT_GOAL = run

COMPOSE = docker compose --env-file .env.local
APP_URL = http://localhost:$(APP_PORT)

.PHONY: help
help:
	@echo EventHub commands:
	@echo   make run              Start all services in detached mode with rebuild
	@echo   make rund             Start all services in foreground with rebuild
	@echo   make up               Start all services in detached mode without rebuild
	@echo   make build            Build application image
	@echo   make recreate         Recreate all services with rebuild
	@echo   make restart          Restart all services
	@echo   make restart-app      Restart only the application
	@echo   make services         Show service statuses
	@echo   make config           Render docker compose configuration
	@echo   make health           Call the health endpoint
	@echo   make logs             Follow logs from all services
	@echo   make logs-app         Follow application logs
	@echo   make logs-db          Follow database logs
	@echo   make shell-app        Open shell in the application container
	@echo   make shell-mongo      Open MongoDB shell
	@echo   make shell-cassandra  Open Cassandra shell
	@echo   make shell-neo4j      Open Neo4j shell
	@echo   make shell-redis      Open Redis shell
	@echo   make check-python     Compile Python files
	@echo   make verify           Run local static checks
	@echo   make stop             Stop services and keep volumes
	@echo   make clean            Stop services and remove volumes
	@echo   make reset            Clean data and start everything again

.PHONY: run
run:
	$(COMPOSE) up -d --build

.PHONY: rund
rund:
	$(COMPOSE) up --build

.PHONY: up
up:
	$(COMPOSE) up -d

.PHONY: build
build:
	$(COMPOSE) build

.PHONY: pull
pull:
	$(COMPOSE) pull

.PHONY: recreate
recreate:
	$(COMPOSE) up -d --build --force-recreate

.PHONY: restart
restart:
	$(COMPOSE) restart

.PHONY: restart-app
restart-app:
	$(COMPOSE) restart app

.PHONY: services
services:
	$(COMPOSE) ps

.PHONY: config
config:
	$(COMPOSE) config

.PHONY: health
health:
	curl -i $(APP_URL)/health

.PHONY: logs
logs:
	$(COMPOSE) logs -f

.PHONY: logs-app
logs-app:
	$(COMPOSE) logs -f app

.PHONY: logs-db
logs-db:
	$(COMPOSE) logs -f redis cassandra-test mongos neo4j

.PHONY: logs-mongo
logs-mongo:
	$(COMPOSE) logs -f mongo-cfg-1 mongo-cfg-2 mongo-cfg-3 mongo-shard1-1 mongo-shard1-2 mongo-shard1-3 mongo-shard2-1 mongo-shard2-2 mongo-shard2-3 mongos mongo-init

.PHONY: logs-cassandra
logs-cassandra:
	$(COMPOSE) logs -f cassandra-test cassandra-init

.PHONY: logs-neo4j
logs-neo4j:
	$(COMPOSE) logs -f neo4j neo4j-init

.PHONY: logs-redis
logs-redis:
	$(COMPOSE) logs -f redis

.PHONY: shell-app
shell-app:
	$(COMPOSE) exec app sh

.PHONY: shell-mongo
shell-mongo:
	$(COMPOSE) exec mongos mongosh

.PHONY: shell-cassandra
shell-cassandra:
	$(COMPOSE) exec cassandra-test cqlsh localhost $(CASSANDRA_PORT)

.PHONY: shell-neo4j
shell-neo4j:
	$(COMPOSE) exec neo4j cypher-shell -a bolt://localhost:7687 -u $(NEO4J_USERNAME) -p $(NEO4J_PASSWORD)

.PHONY: shell-redis
shell-redis:
	$(COMPOSE) exec redis redis-cli -p $(REDIS_PORT)

.PHONY: check-python
check-python:
	python -m compileall app

.PHONY: verify
verify: config check-python

.PHONY: stop
stop:
	$(COMPOSE) down

.PHONY: clean
clean:
	$(COMPOSE) down -v --remove-orphans

.PHONY: reset
reset:
	$(COMPOSE) down -v --remove-orphans
	$(COMPOSE) up -d --build
