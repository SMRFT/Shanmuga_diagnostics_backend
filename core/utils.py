import os
from core.mongo_client import get_client

# Simple in-memory cache to prevent excessive DB calls
_EMPLOYEE_NAME_CACHE = {}
_CACHE_INITIALIZED = False

def get_employee_map():
    global _EMPLOYEE_NAME_CACHE, _CACHE_INITIALIZED
    
    if _CACHE_INITIALIZED:
        return _EMPLOYEE_NAME_CACHE
        
    try:
        mongo_url = os.getenv("GLOBAL_DB_HOST")
        if mongo_url:
            client = get_client()
            db = client["Global"]
            collection = db["backend_diagnostics_profile"]
            
            # Fetch only employeeId and employeeName
            docs = collection.find({}, {"employeeId": 1, "employeeName": 1, "_id": 0})
            for doc in docs:
                emp_id = doc.get("employeeId")
                emp_name = doc.get("employeeName")
                if emp_id and emp_name:
                    _EMPLOYEE_NAME_CACHE[str(emp_id)] = str(emp_name)
                    
            _CACHE_INITIALIZED = True
    except Exception as e:
        print(f"Error fetching employee cache: {e}")
        
    return _EMPLOYEE_NAME_CACHE

def get_employee_name(employee_id_or_name):
    """
    Returns the employee name if the input is a valid employee_id.
    If it's already a name or not found, returns the original input.
    """
    if not employee_id_or_name:
        return ""
        
    employee_id_str = str(employee_id_or_name)
    
    cache = get_employee_map()
    
    if employee_id_str in cache:
        return cache[employee_id_str]
        
    return employee_id_or_name
