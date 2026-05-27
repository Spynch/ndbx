#!/usr/bin/env bash
set -euo pipefail

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
    printf '%s' "eventhub"
  fi
}

init_mongodb_database() {
  local database_name="$1"
  local mongo_args=(--quiet --host "$MONGODB_HOST" --port "$MONGODB_PORT")

  if [[ -n "${MONGODB_USER:-}" ]]; then
    mongo_args+=(--username "$MONGODB_USER" --password "${MONGODB_PASSWORD:-}" --authenticationDatabase admin)
  fi

  echo "Initializing MongoDB database ${database_name}..."
  MONGODB_INIT_DATABASE="$database_name" mongosh "${mongo_args[@]}" --eval '
const databaseName = process.env.MONGODB_INIT_DATABASE;
if (!databaseName) {
  throw new Error("MONGODB_INIT_DATABASE is required");
}

const database = db.getSiblingDB(databaseName);
for (const collectionName of ["users", "events"]) {
  if (database.getCollectionInfos({ name: collectionName }).length === 0) {
    database.createCollection(collectionName);
  }
}

database.events.createIndex({ created_by: "hashed" }, { name: "created_by_hashed" });

sh.enableSharding(databaseName);
const eventsNamespace = databaseName + ".events";
const shardedEvents = db.getSiblingDB("config").collections.findOne({ _id: eventsNamespace });
if (!shardedEvents) {
  sh.shardCollection(eventsNamespace, { created_by: "hashed" });
}

database.users.createIndex({ username: 1 }, { unique: true });
database.users.createIndex({ full_name: 1 });
database.events.createIndex({ title: 1, created_by: 1 });
database.events.createIndex({ created_by: 1, title: 1 });
database.events.createIndex({ created_by: 1 });
database.events.createIndex({ title: 1 });
database.events.createIndex({ category: 1 });
database.events.createIndex({ price: 1 });
database.events.createIndex({ "location.city": 1 });
database.events.createIndex({ started_at: 1 });

print("MongoDB database " + databaseName + " initialized.");
'
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
