# dbcollection.py  

from pymongo import MongoClient
import os

# Create Mongo client (single place)
mongo_url = os.getenv("GLOBAL_DB_HOST")
client = MongoClient(mongo_url)

# Databases

global_db = client["Global"]

# Collections

profile_collection = global_db["backend_diagnostics_profile"]

# ✅ Add this
B2B_ROLES = ["SD-R-SAS", "SD-R-SE", "SD-R-SMC"]