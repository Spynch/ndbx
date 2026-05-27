#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA_FILE="${SCRIPT_DIR}/schema.js"

read_required_env() {
  local name="$1"
  local value="${!name:-}"

  if [[ -z "$value" ]]; then
    echo "${name} is required" >&2
    exit 1
  fi

  printf '%s' "$value"
}

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

database_names_from_env() {
  if [[ -n "${MONGODB_DATABASES:-}" ]]; then
    printf '%s' "$MONGODB_DATABASES"
  elif [[ -n "${MONGODB_DATABSE:-}" ]]; then
    printf '%s' "$MONGODB_DATABSE"
  elif [[ -n "${MONGODB_DATABASE:-}" ]]; then
    printf '%s' "$MONGODB_DATABASE"
  else
    echo "MONGODB_DATABASES, MONGODB_DATABSE, or MONGODB_DATABASE is required" >&2
    exit 1
  fi
}

init_mongodb_database() {
  local database_name="$1"
  local mongodb_host
  local mongodb_port
  local mongo_args

  mongodb_host="$(read_required_env MONGODB_HOST)"
  mongodb_port="$(read_required_env MONGODB_PORT)"
  mongo_args=(--quiet --host "$mongodb_host" --port "$mongodb_port")

  if [[ -n "${MONGODB_USER:-}" ]]; then
    mongo_args+=(--username "$MONGODB_USER" --password "${MONGODB_PASSWORD:-}" --authenticationDatabase admin)
  fi

  echo "Initializing MongoDB database ${database_name}..."
  MONGODB_INIT_DATABASE="$database_name" mongosh "${mongo_args[@]}" "$SCHEMA_FILE"
}

IFS=',' read -r -a database_names <<< "$(database_names_from_env)"
initialized=0

for raw_database_name in "${database_names[@]}"; do
  database_name="$(trim "$raw_database_name")"
  if [[ -z "$database_name" ]]; then
    continue
  fi

  init_mongodb_database "$database_name"
  initialized=1
done

if [[ "$initialized" -eq 0 ]]; then
  echo "No MongoDB databases were selected for initialization."
fi
