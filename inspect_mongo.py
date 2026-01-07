import os
import pymongo
from pymongo import MongoClient
import sys

# Add the project root to sys.path so we can setup django if needed, 
# but for simple mongo inspection we just need the env var or settings.
# Let's try to load settings or just read the file.
# The previous view_file showed: 'host': os.getenv("GLOBAL_DB_HOST")
# I'll rely on the env var being set in the shell context or try to read it.

# Actually, I don't have the env var in this shell context unless I source something?
# I saw settings.py uses os.getenv.
# The user's environment likely has it.
# I'll try to import settings.

import os
import django
from django.conf import settings

# Setup django to load settings
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'shanmuga_backend.settings')
django.setup()

host = os.getenv("GLOBAL_DB_HOST")
if not host:
    print("GLOBAL_DB_HOST not found in env, using settings.DATABASES")
    host = settings.DATABASES['default']['CLIENT']['host']

print(f"Connecting to {host}")
client = MongoClient(host)

print("Databases:", client.list_database_names())

if 'machine_hmsmi' in client.list_database_names():
    db = client.machine_hmsmi
    print("Collections in machine_hmsmi:", db.list_collection_names())
    
    # Peek into the first collection if exists
    cols = db.list_collection_names()
    if cols:
        print(f"Sample from {cols[0]}:", db[cols[0]].find_one())
else:
    print("machine_hmsmi database not found.")
    # Check if it's a collection in Diagnostics
    db = client.Diagnostics
    if 'machine_hmsmi' in db.list_collection_names():
        print("machine_hmsmi IS A COLLECTION in Diagnostics")
        print("Sample:", db.machine_hmsmi.find_one())

