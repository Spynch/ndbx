.DEFAULT_GOAL = run

# Runs all services in detached mode.
.PHONY: run
run:
	docker compose --env-file .env.local up -d --build

# Runs all services without detached mode (for debugging).
.PHONY: rund
rund:
	docker compose --env-file .env.local up --build

# Shows all service statuses.
.PHONY: services
services:
	docker compose ps

# Stops all running services.
.PHONY: stop
stop:
	docker compose down

# Cleans up all resources including volumes.
.PHONY: clean
clean:
	docker compose down -v
