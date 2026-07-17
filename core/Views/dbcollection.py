# dbcollection.py  

from core.mongo_client import get_client
import os

# Create Mongo client (single place)
mongo_url = os.getenv("GLOBAL_DB_HOST")
client = get_client()

# Databases

global_db = client["Global"]

# Collections

profile_collection = global_db["backend_diagnostics_profile"]
desigation_collection = global_db["backend_diagnostics_Designation"]

# ✅ Add this
B2B_ROLES = ["SD-R-SAS", "SD-R-SE", "SD-R-SMC"] 
B2B_LAB_Roles = ["SD-R-SAS", "SD-R-SE", "SD-R-SMC"]
BIO_CHESMISTRY_ROLES =["DESIG099"]