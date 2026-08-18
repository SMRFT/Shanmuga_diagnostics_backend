# dbcollection.py  

from pymongo import MongoClient
import os

# Create Mongo client (single place)
mongo_url = os.getenv("GLOBAL_DB_HOST")
client = MongoClient(mongo_url)

# Databases

global_db = client["Global"]
diag_db = client["Diagnostics"]

# Collections

profile_collection = global_db["backend_diagnostics_profile"]
desigation_collection = global_db["backend_diagnostics_Designation"]
cluster_collection = diag_db["core_franchise_location_details"]
location_collection = cluster_collection


# ✅ Add this
B2B_ROLES = ["SD-R-SAS", "SD-R-SE", "SD-R-SMC","SD-R-GM"] 
B2B_LAB_Roles = ["SD-R-SAS", "SD-R-SE", "SD-R-SMC","SD-R-GM"]
BIO_CHESMISTRY_ROLES =["DESIG099"]



