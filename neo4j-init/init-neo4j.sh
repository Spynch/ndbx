#!/usr/bin/env bash
set -euo pipefail

: "${NEO4J_URL:?NEO4J_URL is required}"
: "${NEO4J_USERNAME:?NEO4J_USERNAME is required}"
: "${NEO4J_PASSWORD:?NEO4J_PASSWORD is required}"

cypher-shell \
  -a "${NEO4J_URL}" \
  -u "${NEO4J_USERNAME}" \
  -p "${NEO4J_PASSWORD}" \
  -f /scripts/schema.cypher
