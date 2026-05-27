#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

wait_for_mongo() {
  local host="$1"
  local port="$2"

  until mongosh --quiet --host "$host" --port "$port" --eval "db.adminCommand({ ping: 1 }).ok" >/dev/null 2>&1; do
    sleep 1
  done
}

wait_for_primary() {
  local host="$1"
  local port="$2"

  while true; do
    local result
    result="$(mongosh --quiet --host "$host" --port "$port" --eval "const hello = db.hello(); (hello.isWritablePrimary || hello.secondary) ? 'yes' : 'no'" 2>/dev/null || true)"
    if [[ "$result" == *"yes"* ]]; then
      break
    fi
    sleep 1
  done
}

init_replica_set() {
  local host="$1"
  local port="$2"
  local config_json="$3"

  mongosh --quiet --host "$host" --port "$port" --eval "
try {
  const status = rs.status();
  if (status.ok !== 1) {
    throw new Error('Replica set status is not ok');
  }
} catch (e) {
  rs.initiate(${config_json});
}
"
}

add_shard_if_missing() {
  local shard_name="$1"
  local shard_connection="$2"

  mongosh --quiet --host "$MONGODB_HOST" --port "$MONGODB_PORT" --eval "
const shardName = '${shard_name}';
const shardConnection = '${shard_connection}';
const existing = db.getSiblingDB('config').shards.findOne({ _id: shardName });
if (!existing) {
  sh.addShard(shardConnection);
}
"
}

echo "Waiting for MongoDB nodes..."
wait_for_mongo "mongo-cfg-1" "$MONGO_CFG_1_PORT"
wait_for_mongo "mongo-cfg-2" "$MONGO_CFG_2_PORT"
wait_for_mongo "mongo-cfg-3" "$MONGO_CFG_3_PORT"
wait_for_mongo "mongo-shard1-1" "$MONGO_SHARD1_1_PORT"
wait_for_mongo "mongo-shard1-2" "$MONGO_SHARD1_2_PORT"
wait_for_mongo "mongo-shard1-3" "$MONGO_SHARD1_3_PORT"
wait_for_mongo "mongo-shard2-1" "$MONGO_SHARD2_1_PORT"
wait_for_mongo "mongo-shard2-2" "$MONGO_SHARD2_2_PORT"
wait_for_mongo "mongo-shard2-3" "$MONGO_SHARD2_3_PORT"

echo "Initializing config server replica set..."
init_replica_set "mongo-cfg-1" "$MONGO_CFG_1_PORT" "{
  _id: '${MONGO_CFG_RS}',
  configsvr: true,
  members: [
    { _id: 0, host: 'mongo-cfg-1:${MONGO_CFG_1_PORT}' },
    { _id: 1, host: 'mongo-cfg-2:${MONGO_CFG_2_PORT}' },
    { _id: 2, host: 'mongo-cfg-3:${MONGO_CFG_3_PORT}' }
  ]
}"
wait_for_primary "mongo-cfg-1" "$MONGO_CFG_1_PORT"

echo "Initializing shard #1 replica set..."
init_replica_set "mongo-shard1-1" "$MONGO_SHARD1_1_PORT" "{
  _id: '${MONGO_SHARD1_RS}',
  members: [
    { _id: 0, host: 'mongo-shard1-1:${MONGO_SHARD1_1_PORT}' },
    { _id: 1, host: 'mongo-shard1-2:${MONGO_SHARD1_2_PORT}' },
    { _id: 2, host: 'mongo-shard1-3:${MONGO_SHARD1_3_PORT}' }
  ]
}"
wait_for_primary "mongo-shard1-1" "$MONGO_SHARD1_1_PORT"

echo "Initializing shard #2 replica set..."
init_replica_set "mongo-shard2-1" "$MONGO_SHARD2_1_PORT" "{
  _id: '${MONGO_SHARD2_RS}',
  members: [
    { _id: 0, host: 'mongo-shard2-1:${MONGO_SHARD2_1_PORT}' },
    { _id: 1, host: 'mongo-shard2-2:${MONGO_SHARD2_2_PORT}' },
    { _id: 2, host: 'mongo-shard2-3:${MONGO_SHARD2_3_PORT}' }
  ]
}"
wait_for_primary "mongo-shard2-1" "$MONGO_SHARD2_1_PORT"

wait_for_mongo "$MONGODB_HOST" "$MONGODB_PORT"

echo "Registering shards in mongos..."
add_shard_if_missing \
  "${MONGO_SHARD1_RS}" \
  "${MONGO_SHARD1_RS}/mongo-shard1-1:${MONGO_SHARD1_1_PORT},mongo-shard1-2:${MONGO_SHARD1_2_PORT},mongo-shard1-3:${MONGO_SHARD1_3_PORT}"
add_shard_if_missing \
  "${MONGO_SHARD2_RS}" \
  "${MONGO_SHARD2_RS}/mongo-shard2-1:${MONGO_SHARD2_1_PORT},mongo-shard2-2:${MONGO_SHARD2_2_PORT},mongo-shard2-3:${MONGO_SHARD2_3_PORT}"

echo "Initializing application databases..."
bash "${SCRIPT_DIR}/init-databases.sh"

echo "MongoDB initialization complete."
