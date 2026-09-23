import os
import mimetypes
from datetime import datetime
from bson import ObjectId
from bson.errors import InvalidId
from pymongo import MongoClient
import gridfs
from dotenv import load_dotenv

load_dotenv()

from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.permissions import AllowAny
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.response import Response
from rest_framework import status
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse, Http404

# Mongo connection
def get_mongo_db():
    mongo_url = os.getenv("GLOBAL_DB_HOST")
    client = MongoClient(mongo_url)
    return client["Diagnostics"]

db = get_mongo_db()
test_collection = db["diagnostic_test_directory"]
fs = gridfs.GridFS(db)


def _serialize_test(doc):
    """Helper to convert MongoDB document to JSON serializable dict."""
    if not doc:
        return None
    
    file_id = doc.get("sample_report_file_id")
    file_id_str = str(file_id) if file_id else None
    
    return {
        "id": str(doc.get("_id")),
        "test_name": doc.get("test_name", ""),
        "test_code": doc.get("test_code", ""),
        "department": doc.get("department", "General"),
        "specimen": doc.get("specimen", ""),
        "reference_range": doc.get("reference_range", ""),
        "unit": doc.get("unit", ""),
        "method": doc.get("method", ""),
        "clinical_purpose": doc.get("clinical_purpose", ""),
        "turnaround_time": doc.get("turnaround_time", ""),
        "patient_preparation": doc.get("patient_preparation", "No special preparation required"),
        "sample_report_file_id": file_id_str,
        "sample_report_file_name": doc.get("sample_report_file_name", ""),
        "sample_report_file_size": doc.get("sample_report_file_size", 0),
        "sample_report_file_type": doc.get("sample_report_file_type", ""),
        "created_at": doc.get("created_at").isoformat() if doc.get("created_at") else None,
        "updated_at": doc.get("updated_at").isoformat() if doc.get("updated_at") else None,
    }


@api_view(['GET', 'POST'])
@csrf_exempt
@permission_classes([AllowAny])
@parser_classes([MultiPartParser, FormParser, JSONParser])
def test_directory_list_create(request):
    """
    GET: List all tests with optional search, department, and specimen filters.
    POST: Create a new test entry with optional sample report file upload.
    """
    if request.method == 'GET':
        try:
            query = {}
            search = request.GET.get('search', '').strip()
            department = request.GET.get('department', '').strip()
            specimen = request.GET.get('specimen', '').strip()

            if search:
                query["$or"] = [
                    {"test_name": {"$regex": search, "$options": "i"}},
                    {"test_code": {"$regex": search, "$options": "i"}},
                    {"specimen": {"$regex": search, "$options": "i"}},
                    {"method": {"$regex": search, "$options": "i"}},
                    {"clinical_purpose": {"$regex": search, "$options": "i"}},
                    {"unit": {"$regex": search, "$options": "i"}},
                ]

            if department and department.lower() != 'all':
                query["department"] = {"$regex": f"^{department}$", "$options": "i"}

            if specimen and specimen.lower() != 'all':
                query["specimen"] = {"$regex": f"^{specimen}$", "$options": "i"}

            cursor = test_collection.find(query).sort("test_name", 1)
            tests = [_serialize_test(doc) for doc in cursor]

            return Response({
                "success": True,
                "count": len(tests),
                "data": tests
            }, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({
                "success": False,
                "error": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    elif request.method == 'POST':
        try:
            data = request.data
            test_name = data.get('test_name', '').strip()

            if not test_name:
                return Response({
                    "success": False,
                    "error": "Test Name is required."
                }, status=status.HTTP_400_BAD_REQUEST)

            # Handle sample report file upload to GridFS
            file_id = None
            file_name = ""
            file_size = 0
            file_type = ""

            if 'sample_report_file' in request.FILES:
                uploaded_file = request.FILES['sample_report_file']
                file_name = uploaded_file.name
                file_size = uploaded_file.size
                file_type = uploaded_file.content_type or mimetypes.guess_type(file_name)[0] or 'application/octet-stream'
                
                file_id = fs.put(
                    uploaded_file.read(),
                    filename=file_name,
                    content_type=file_type,
                    upload_date=datetime.now()
                )

            # Auto-generate test code if not provided
            test_code = data.get('test_code', '').strip()
            if not test_code:
                words = test_name.split()
                prefix = "".join([w[0] for w in words[:3]]).upper()
                count = test_collection.count_documents({}) + 1
                test_code = f"{prefix}{count:03d}"

            new_test = {
                "test_name": test_name,
                "test_code": test_code,
                "department": data.get('department', 'General').strip() or 'General',
                "specimen": data.get('specimen', '').strip(),
                "reference_range": data.get('reference_range', '').strip(),
                "unit": data.get('unit', '').strip(),
                "method": data.get('method', '').strip(),
                "clinical_purpose": data.get('clinical_purpose', '').strip(),
                "turnaround_time": data.get('turnaround_time', '').strip(),
                "patient_preparation": data.get('patient_preparation', 'No special preparation required').strip(),
                "sample_report_file_id": file_id,
                "sample_report_file_name": file_name,
                "sample_report_file_size": file_size,
                "sample_report_file_type": file_type,
                "created_at": datetime.now(),
                "updated_at": datetime.now(),
            }

            result = test_collection.insert_one(new_test)
            new_test["_id"] = result.inserted_id

            return Response({
                "success": True,
                "message": "Test added successfully to directory.",
                "data": _serialize_test(new_test)
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            return Response({
                "success": False,
                "error": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET', 'PUT', 'PATCH', 'DELETE'])
@csrf_exempt
@permission_classes([AllowAny])
@parser_classes([MultiPartParser, FormParser, JSONParser])
def test_directory_detail(request, test_id):
    """
    GET: Retrieve specific test details.
    PUT/PATCH: Update test details, optionally updating or replacing the sample report.
    DELETE: Delete test entry and its associated GridFS sample report file.
    """
    try:
        oid = ObjectId(test_id)
    except (InvalidId, TypeError):
        return Response({
            "success": False,
            "error": "Invalid Test ID format."
        }, status=status.HTTP_400_BAD_REQUEST)

    doc = test_collection.find_one({"_id": oid})
    if not doc:
        return Response({
            "success": False,
            "error": "Test not found in directory."
        }, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        return Response({
            "success": True,
            "data": _serialize_test(doc)
        }, status=status.HTTP_200_OK)

    elif request.method in ['PUT', 'PATCH']:
        try:
            data = request.data
            update_fields = {"updated_at": datetime.now()}

            for field in [
                'test_name', 'test_code', 'department', 'specimen',
                'reference_range', 'unit', 'method', 'clinical_purpose',
                'turnaround_time', 'patient_preparation'
            ]:
                if field in data:
                    update_fields[field] = str(data.get(field, '')).strip()

            # Check if a new file is uploaded
            if 'sample_report_file' in request.FILES:
                # Delete old file from GridFS if existed
                old_file_id = doc.get("sample_report_file_id")
                if old_file_id:
                    try:
                        fs.delete(old_file_id)
                    except Exception:
                        pass

                uploaded_file = request.FILES['sample_report_file']
                file_name = uploaded_file.name
                file_size = uploaded_file.size
                file_type = uploaded_file.content_type or mimetypes.guess_type(file_name)[0] or 'application/octet-stream'

                new_file_id = fs.put(
                    uploaded_file.read(),
                    filename=file_name,
                    content_type=file_type,
                    upload_date=datetime.now()
                )

                update_fields["sample_report_file_id"] = new_file_id
                update_fields["sample_report_file_name"] = file_name
                update_fields["sample_report_file_size"] = file_size
                update_fields["sample_report_file_type"] = file_type
            
            # Check if user explicitly asked to remove file
            elif data.get('remove_sample_report') in [True, 'true', '1']:
                old_file_id = doc.get("sample_report_file_id")
                if old_file_id:
                    try:
                        fs.delete(old_file_id)
                    except Exception:
                        pass
                update_fields["sample_report_file_id"] = None
                update_fields["sample_report_file_name"] = ""
                update_fields["sample_report_file_size"] = 0
                update_fields["sample_report_file_type"] = ""

            test_collection.update_one({"_id": oid}, {"$set": update_fields})
            updated_doc = test_collection.find_one({"_id": oid})

            return Response({
                "success": True,
                "message": "Test updated successfully.",
                "data": _serialize_test(updated_doc)
            }, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({
                "success": False,
                "error": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    elif request.method == 'DELETE':
        try:
            # Delete associated file from GridFS
            old_file_id = doc.get("sample_report_file_id")
            if old_file_id:
                try:
                    fs.delete(old_file_id)
                except Exception:
                    pass

            test_collection.delete_one({"_id": oid})
            return Response({
                "success": True,
                "message": f"Test '{doc.get('test_name')}' deleted successfully."
            }, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({
                "success": False,
                "error": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@csrf_exempt
@permission_classes([AllowAny])
def stream_sample_report(request, file_id):
    """
    Stream or download the sample report file stored in GridFS.
    """
    try:
        oid = ObjectId(file_id)
        grid_out = fs.get(oid)
    except Exception:
        raise Http404("Sample report file not found.")

    content_type = grid_out.content_type or mimetypes.guess_type(grid_out.filename or "")[0] or "application/octet-stream"
    
    response = HttpResponse(grid_out.read(), content_type=content_type)
    
    # Check if download is requested or inline preview
    is_download = request.GET.get('download', '0') in ['1', 'true']
    disposition = 'attachment' if is_download else 'inline'
    filename = grid_out.filename or "sample_report.pdf"
    
    response['Content-Disposition'] = f'{disposition}; filename="{filename}"'
    return response


@api_view(['GET'])
@csrf_exempt
@permission_classes([AllowAny])
def test_directory_stats(request):
    """
    Get summary statistics for the diagnostic test directory.
    """
    try:
        total_tests = test_collection.count_documents({})
        
        # Aggregate counts by department
        dept_pipeline = [
            {"$group": {"_id": "$department", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}}
        ]
        dept_counts = list(test_collection.aggregate(dept_pipeline))
        
        # Aggregate counts by specimen
        specimen_pipeline = [
            {"$group": {"_id": "$specimen", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}}
        ]
        specimen_counts = list(test_collection.aggregate(specimen_pipeline))

        tests_with_reports = test_collection.count_documents({
            "sample_report_file_id": {"$ne": None}
        })

        return Response({
            "success": True,
            "total_tests": total_tests,
            "tests_with_reports": tests_with_reports,
            "departments": [{"name": d["_id"] or "General", "count": d["count"]} for d in dept_counts],
            "specimens": [{"name": s["_id"] or "Unspecified", "count": s["count"]} for s in specimen_counts],
        }, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({
            "success": False,
            "error": str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@csrf_exempt
@permission_classes([AllowAny])
def get_core_test_options(request):
    """
    Fetch all master tests from core_testdetails collection
    to populate the dropdown and allow autocomplete/autofill in Test Directory form.
    """
    try:
        core_collection = db["core_testdetails"]
        cursor = core_collection.find({}, {
            "_id": 1,
            "test_id": 1,
            "test_name": 1,
            "test_code": 1,
            "department": 1,
            "specimen_type": 1,
            "collection_container": 1,
            "unit": 1,
            "method": 1,
            "reference_range": 1,
            "TAT_Time": 1,
            "parameters": 1,
        }).sort("test_name", 1)

        test_list = []
        for doc in cursor:
            test_name = (doc.get("test_name") or "").strip()
            if not test_name:
                continue
            
            department = doc.get("department") or "General"
            specimen = doc.get("specimen_type") or doc.get("collection_container") or ""
            unit = doc.get("unit") or ""
            method = doc.get("method") or ""
            ref_range = doc.get("reference_range") or ""
            tat = doc.get("TAT_Time") or ""
            
            # If reference_range or unit is empty and parameters exist, compile from parameters
            raw_params = doc.get("parameters")
            parameters = []
            if isinstance(raw_params, dict):
                for val in raw_params.values():
                    if isinstance(val, list):
                        parameters.extend(val)
                    elif isinstance(val, dict):
                        parameters.append(val)
                    elif isinstance(val, str) and val.strip():
                        parameters.append({"test_name": val.strip()})
            elif isinstance(raw_params, list):
                parameters = raw_params
            elif isinstance(raw_params, str) and raw_params.strip():
                try:
                    import json
                    parsed = json.loads(raw_params)
                    if isinstance(parsed, list):
                        parameters = parsed
                    elif isinstance(parsed, dict):
                        for val in parsed.values():
                            if isinstance(val, list):
                                parameters.extend(val)
                except Exception:
                    pass

            if not ref_range and parameters:
                param_ranges = []
                for p in parameters:
                    if isinstance(p, dict):
                        p_name = (p.get("test_name") or p.get("parameter_name") or "").strip()
                        p_ref = (p.get("reference_range") or p.get("normal_range") or "").strip()
                        p_unit = (p.get("unit") or "").strip()
                        if p_ref:
                            if p_name:
                                param_ranges.append(f"{p_name}: {p_ref} {p_unit}".strip())
                            else:
                                param_ranges.append(f"{p_ref} {p_unit}".strip())
                        if not unit and p_unit:
                            unit = p_unit
                        if not method and p.get("method"):
                            method = str(p.get("method"))
                    elif isinstance(p, str) and p.strip():
                        param_ranges.append(p.strip())
                if param_ranges:
                    ref_range = "\n".join(param_ranges)

            test_list.append({
                "id": str(doc.get("_id")),
                "test_id": doc.get("test_id"),
                "test_name": test_name,
                "test_code": doc.get("test_code") or "",
                "department": department,
                "specimen": specimen,
                "unit": unit,
                "method": method,
                "reference_range": ref_range,
                "turnaround_time": tat,
            })

        return Response({
            "success": True,
            "count": len(test_list),
            "data": test_list
        }, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({
            "success": False,
            "error": str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
