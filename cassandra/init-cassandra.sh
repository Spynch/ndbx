#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA_TEMPLATE="${SCRIPT_DIR}/schema.cql"

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

read_first_cassandra_host() {
  local hosts="$1"
  local first_host
  IFS=',' read -r first_host _ <<< "$hosts"
  first_host="$(trim "$first_host")"

  if [[ -z "$first_host" ]]; then
    echo "CASSANDRA_HOSTS must contain at least one host" >&2
    exit 1
  fi

  printf '%s' "$first_host"
}

validate_identifier() {
  local name="$1"
  local value="$2"

  if [[ ! "$value" =~ ^[A-Za-z][A-Za-z0-9_]*$ ]]; then
    echo "${name} has invalid Cassandra identifier value: ${value}" >&2
    exit 1
  fi
}

validate_positive_integer() {
  local name="$1"
  local value="$2"

  if [[ ! "$value" =~ ^[0-9]+$ ]] || [[ "$value" -lt 1 ]]; then
    echo "${name} must be a positive integer, got: ${value}" >&2
    exit 1
  fi
}

build_cqlsh_args() {
  CQLSH_ARGS=()

  if [[ -n "${CASSANDRA_USERNAME:-}" ]]; then
    CQLSH_ARGS+=(-u "$CASSANDRA_USERNAME" -p "${CASSANDRA_PASSWORD:-}")
  fi

  CQLSH_ARGS+=("$CASSANDRA_HOST" "$CASSANDRA_PORT")
}

render_schema() {
  sed \
    -e "s/__CASSANDRA_KEYSPACE__/${CASSANDRA_KEYSPACE}/g" \
    -e "s/__CASSANDRA_REPLICATION_CLASS__/${CASSANDRA_REPLICATION_CLASS}/g" \
    -e "s/__CASSANDRA_REPLICATION_FACTOR__/${CASSANDRA_REPLICATION_FACTOR}/g" \
    "$SCHEMA_TEMPLATE" > "$RENDERED_SCHEMA"
}

CASSANDRA_HOSTS="$(read_required_env CASSANDRA_HOSTS)"
CASSANDRA_HOST="$(read_first_cassandra_host "$CASSANDRA_HOSTS")"
CASSANDRA_PORT="$(read_required_env CASSANDRA_PORT)"
CASSANDRA_KEYSPACE="$(read_required_env CASSANDRA_KEYSPACE)"
CASSANDRA_REPLICATION_CLASS="$(read_required_env CASSANDRA_REPLICATION_CLASS)"
CASSANDRA_REPLICATION_FACTOR="$(read_required_env CASSANDRA_REPLICATION_FACTOR)"
RENDERED_SCHEMA="$(mktemp)"
trap 'rm -f "$RENDERED_SCHEMA"' EXIT

validate_positive_integer "CASSANDRA_PORT" "$CASSANDRA_PORT"
validate_identifier "CASSANDRA_KEYSPACE" "$CASSANDRA_KEYSPACE"
validate_identifier "CASSANDRA_REPLICATION_CLASS" "$CASSANDRA_REPLICATION_CLASS"
validate_positive_integer "CASSANDRA_REPLICATION_FACTOR" "$CASSANDRA_REPLICATION_FACTOR"
build_cqlsh_args

echo "Waiting for Cassandra at ${CASSANDRA_HOST}:${CASSANDRA_PORT}..."
until cqlsh "${CQLSH_ARGS[@]}" -e "DESCRIBE KEYSPACES" >/dev/null 2>&1; do
  sleep 2
done

render_schema

echo "Initializing Cassandra keyspace ${CASSANDRA_KEYSPACE}..."
cqlsh "${CQLSH_ARGS[@]}" -f "$RENDERED_SCHEMA"
echo "Cassandra initialization complete."
