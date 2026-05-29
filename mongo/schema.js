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
