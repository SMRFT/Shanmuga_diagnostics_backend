# dbcollection.py  

from pymongo import MongoClient
import os

# Create Mongo client (single place)
mongo_url = os.getenv("GLOBAL_DB_HOST")
client = MongoClient(mongo_url)

# Databases

global_db = client["Global"]
diag_db = client["Diagnostics"]
franchise_db = client["franchise"]

# Collections

profile_collection = global_db["backend_diagnostics_profile"]
desigation_collection = global_db["backend_diagnostics_Designation"]
location_collection = franchise_db["franchise_location_details"]
franchise_register = franchise_db["franchise_franchise"]
franchise_homecollection = franchise_db["franchise_homecollection"]
franchise_barcode =franchise_db["franchise_barcodestock"]
franchise_patient =franchise_db["franchise_patient"]
franchise_billing=franchise_db["franchise_billing"]

# ✅ Add this
B2B_ROLES = ["SD-R-SAS", "SD-R-SE", "SD-R-SMC","SD-R-GM"] 
B2B_LAB_Roles = ["SD-R-SAS", "SD-R-SE", "SD-R-SMC","SD-R-GM"]
BIO_CHESMISTRY_ROLES =["DESIG099"]




