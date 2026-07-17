"""
Shared, connection-pooled PyMongo client for the whole application.

Historically, many view modules instantiated `pymongo.MongoClient(...)` ad hoc,
inline, on every request. Each ad-hoc MongoClient() call creates its own
connection pool, which wastes connections against MongoDB and is unnecessary
overhead. PyMongo's MongoClient is thread-safe and already manages an internal
connection pool, so a single instance should be created once per process and
reused everywhere.

This module creates that single instance at import time (module load happens
once per process) and exposes small helpers to fetch the client / a database
handle from it. All call sites use the same `GLOBAL_DB_HOST` connection
string that is also used by djongo's DATABASES config in settings.py.
"""
import os
from pymongo import MongoClient

_MONGO_HOST = os.getenv("GLOBAL_DB_HOST")

# Single shared, pooled client for the whole process.
# maxPoolSize is set explicitly so pooling behavior is intentional rather
# than relying on pymongo's implicit default.
_client = MongoClient(_MONGO_HOST, maxPoolSize=50)


def get_client():
    """Return the shared, connection-pooled MongoClient instance."""
    return _client


def get_db(db_name):
    """Return a Database handle for `db_name` from the shared client."""
    return _client[db_name]
