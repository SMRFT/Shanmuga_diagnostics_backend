from rest_framework.response import Response
from datetime import datetime,timedelta
from django.utils import timezone
import pytz
from rest_framework.views import APIView
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission
import json
from ..models import Patient,SampleStatus,TestValue,MBTestValue,Billing,BarcodeTestDetails,HmspatientBilling,Hmsbarcode,Hmssamplestatus
from pymongo import MongoClient
import os
from dotenv import load_dotenv
load_dotenv()
import logging
import re

logger = logging.getLogger(__name__)
@permission_classes([HasRoleAndDataPermission])
class ConsolidatedDataView(APIView):

    def parse_tat_format(self, tat_str):
        if not tat_str or tat_str == 'N/A':
            return None
        try:
            total_seconds = 0
            days = re.search(r'(\d+)D', str(tat_str))
            hours = re.search(r'(\d+)H', str(tat_str))
            minutes = re.search(r'(\d+)M', str(tat_str))

            if days:
                total_seconds += int(days.group(1)) * 86400
            if hours:
                total_seconds += int(hours.group(1)) * 3600
            if minutes:
                total_seconds += int(minutes.group(1)) * 60

            return total_seconds if total_seconds > 0 else None
        except:
            return None

    def get(self, request):

        single_date = request.query_params.get('date')
        from_date = request.query_params.get('from_date')
        to_date = request.query_params.get('to_date')

        ist = pytz.timezone('Asia/Kolkata')

        # ---------------- DATE FILTER ----------------
        try:
            if from_date and to_date:
                from_date_obj = datetime.strptime(from_date, '%Y-%m-%d')
                to_date_obj = datetime.strptime(to_date, '%Y-%m-%d')
                to_date_obj = to_date_obj.replace(hour=23, minute=59, second=59)
            elif single_date:
                from_date_obj = datetime.strptime(single_date, '%Y-%m-%d')
                to_date_obj = from_date_obj.replace(hour=23, minute=59, second=59)
            else:
                today = datetime.now(ist).date()
                from_date_obj = datetime.combine(today, datetime.min.time())
                to_date_obj = datetime.combine(today, datetime.max.time())
        except ValueError:
            return Response({"error": "Invalid date format"}, status=400)

        from_date_ist = ist.localize(from_date_obj)
        to_date_ist = ist.localize(to_date_obj)

        # ---------------- BILLING ----------------
        billing_records = list(
            Billing.objects.filter(
                bill_date__gte=from_date_ist,
                bill_date__lte=to_date_ist
            ).only('bill_no', 'patient_id', 'bill_date')
        )

        if not billing_records:
            return Response({
                "data": [],
                "count": 0
            }, status=200)

        bill_nos = [b.bill_no for b in billing_records]

        # ---------------- BARCODE DETAILS ----------------
        barcode_qs = list(
            BarcodeTestDetails.objects.filter(
                bill_no__in=bill_nos
            ).only('bill_no', 'barcode', 'testdetails')
        )

        barcode_dict = {b.bill_no: b for b in barcode_qs}
        barcodes = [b.barcode for b in barcode_qs if b.barcode]

        # ---------------- SAMPLE STATUS ----------------
        sample_qs = list(
            SampleStatus.objects.filter(
                barcode__in=barcodes
            ).only('barcode', 'testdetails')
        )

        sample_dict = {s.barcode: s for s in sample_qs}

        # ---------------- TEST VALUES (TestValue) ----------------
        testvalue_qs = list(
            TestValue.objects.filter(
                barcode__in=barcodes
            ).order_by('-created_date', '-lastmodified_date')
        )

        testvalue_dict = {}
        for tv in testvalue_qs:
            testvalue_dict.setdefault(tv.barcode, []).append(tv)

        # ---------------- MB TEST VALUES (MBTestValue) ----------------
        mbtestvalue_qs = list(
            MBTestValue.objects.filter(
                barcode__in=barcodes
            ).order_by('-created_date', '-lastmodified_date')
        )

        mbtestvalue_dict = {}
        for mbtv in mbtestvalue_qs:
            mbtestvalue_dict.setdefault(mbtv.barcode, []).append(mbtv)

        # ---------------- PATIENTS ----------------
        patient_ids = list(set(b.patient_id for b in billing_records))
        patients = list(
            Patient.objects.filter(
                patient_id__in=patient_ids
            )
        )
        patient_dict = {p.patient_id: p for p in patients}

        # ---------------- TEST MASTER (Mongo) ----------------
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        db = client.Diagnostics
        test_collection = db.core_testdetails

        test_ids = set()
        for b in barcode_qs:
            try:
                tests = b.testdetails if isinstance(b.testdetails, list) else json.loads(b.testdetails)
                for t in tests:
                    if t.get("test_id"):
                        test_ids.add(t.get("test_id"))
            except:
                continue

        test_master_dict = {}
        if test_ids:
            cursor = test_collection.find(
                {"test_id": {"$in": list(test_ids)}},
                {"_id": 0}
            )
            for doc in cursor:
                test_master_dict[doc["test_id"]] = doc

        # ---------------- RESPONSE BUILD ----------------
        response_data = []

        def convert_to_iso_if_needed(time_str):
            if not time_str or time_str in ["pending", "null"]:
                return time_str
            try:
                dt_ist = datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
                dt_ist_aware = ist.localize(dt_ist)
                dt_utc = dt_ist_aware.astimezone(pytz.UTC)
                return dt_utc.isoformat()
            except:
                return time_str

        for billing in billing_records:

            barcode_obj = barcode_dict.get(billing.bill_no)
            if not barcode_obj:
                continue

            barcode = barcode_obj.barcode
            patient = patient_dict.get(billing.patient_id)
            if not patient:
                continue

            try:
                barcode_tests = barcode_obj.testdetails if isinstance(barcode_obj.testdetails, list) else json.loads(barcode_obj.testdetails)
            except:
                barcode_tests = []

            sample_obj = sample_dict.get(barcode)
            sample_tests = []
            if sample_obj:
                try:
                    sample_tests = sample_obj.testdetails if isinstance(sample_obj.testdetails, list) else json.loads(sample_obj.testdetails)
                except:
                    pass

            sample_by_id = {t.get("test_id"): t for t in sample_tests if t.get("test_id")}

            # Combine TestValue and MBTestValue data
            testvalues = testvalue_dict.get(barcode, [])
            mbtestvalues = mbtestvalue_dict.get(barcode, [])
            
            testvalue_by_id = {}
            
            # First, process TestValue records
            for tv in testvalues:
                try:
                    tv_list = tv.testdetails if isinstance(tv.testdetails, list) else json.loads(tv.testdetails)
                    for t in tv_list:
                        if t.get("test_id") and t.get("test_id") not in testvalue_by_id:
                            testvalue_by_id[t.get("test_id")] = t
                except:
                    continue

            # Then, process MBTestValue records (will override if same test_id exists)
            for mbtv in mbtestvalues:
                try:
                    mbtv_list = mbtv.testdetails if isinstance(mbtv.testdetails, list) else json.loads(mbtv.testdetails)
                    for t in mbtv_list:
                        test_id = t.get("test_id")
                        if test_id:
                            # Check if we should use MBTestValue data
                            # If test_id not in testvalue_by_id, add it directly
                            if test_id not in testvalue_by_id:
                                testvalue_by_id[test_id] = t
                            else:
                                # If test_id exists, merge the data, preferring non-null/non-pending values
                                existing = testvalue_by_id[test_id]
                                
                                # Update approve_time if MBTestValue has a valid value
                                if t.get("approve_time") and t.get("approve_time") not in ["pending", "null", None]:
                                    if not existing.get("approve_time") or existing.get("approve_time") in ["pending", "null"]:
                                        existing["approve_time"] = t.get("approve_time")
                                
                                # Update dispatch_time if MBTestValue has a valid value
                                if t.get("dispatch_time") and t.get("dispatch_time") not in ["pending", "null", None]:
                                    if not existing.get("dispatch_time") or existing.get("dispatch_time") in ["pending", "null"]:
                                        existing["dispatch_time"] = t.get("dispatch_time")
                except:
                    continue

            # REGISTERED TIME (same format as your original)
            registered_time = None
            if billing.bill_date:
                bill_date_ist = billing.bill_date.astimezone(ist)
                bill_date_utc = bill_date_ist.astimezone(pytz.UTC)
                registered_time = bill_date_utc.isoformat()

            for test in barcode_tests:

                test_id = test.get("test_id")
                if not test_id:
                    continue

                sample_data = sample_by_id.get(test_id, {})
                if sample_data.get("samplestatus") == "Outsource":
                    continue

                test_master = test_master_dict.get(test_id, {})

                expected_tat_display = (
                    test_master.get("TAT_Time")
                    or test_master.get("tat_time")
                    or (f"{test_master.get('TAT_hours')}H" if test_master.get("TAT_hours") else "N/A")
                )

                expected_tat_seconds = self.parse_tat_format(expected_tat_display)

                approval_time = convert_to_iso_if_needed(
                    testvalue_by_id.get(test_id, {}).get("approve_time")
                )

                dispatch_time = convert_to_iso_if_needed(
                    testvalue_by_id.get(test_id, {}).get("dispatch_time")
                )

                collected_time = convert_to_iso_if_needed(
                    sample_data.get("samplecollected_time")
                )

                received_time = convert_to_iso_if_needed(
                    sample_data.get("received_time")
                )

                # -------- ACTUAL TAT --------
                tat_time = "pending"
                tat_seconds = None

                if collected_time and approval_time not in ["pending", "null", None]:
                    try:
                        col_dt = datetime.fromisoformat(collected_time.replace('Z', '+00:00'))
                        app_dt = datetime.fromisoformat(approval_time.replace('Z', '+00:00'))
                        diff = app_dt - col_dt
                        tat_seconds = int(diff.total_seconds())
                        tat_time = str(timedelta(seconds=tat_seconds))
                    except:
                        tat_time = "pending"

                # -------- TOTAL PROCESSING TIME --------
                total_processing_time = "pending"
                total_seconds = None

                if registered_time not in ["pending", "null", None] and dispatch_time not in ["pending", "null", None]:
                    try:
                        reg_dt = datetime.fromisoformat(registered_time.replace('Z', '+00:00'))
                        dis_dt = datetime.fromisoformat(dispatch_time.replace('Z', '+00:00'))
                        diff = dis_dt - reg_dt
                        total_seconds = int(diff.total_seconds())
                        total_processing_time = str(timedelta(seconds=total_seconds))
                    except:
                        total_processing_time = "pending"

                # -------- TAT Overage --------
                tat_out_time = None
                tat_status = "pending"

                if tat_seconds is not None and expected_tat_seconds:
                    if tat_seconds > expected_tat_seconds:
                        over = tat_seconds - expected_tat_seconds
                        tat_out_time = str(timedelta(seconds=over))
                        tat_status = "exceeded"
                    else:
                        tat_status = "within_limit"

                response_data.append({
                    "patient_id": patient.patient_id,
                    "patient_name": patient.patientname,
                    "age": patient.age,
                    "date": registered_time,
                    "registered_time": registered_time,
                    "barcode": barcode,
                    "test_id": test_id,
                    "test_name": test_master.get("test_name"),
                    "department": test_master.get("department"),
                    "collected_time": collected_time,
                    "received_time": received_time,
                    "approval_time": approval_time,
                    "dispatch_time": dispatch_time,
                    "expected_tat": expected_tat_display,
                    "tat_time": tat_time,
                    "tat_out_time": tat_out_time,
                    "tat_status": tat_status,
                    "total_processing_time": total_processing_time
                })

        return Response({
            "data": response_data,
            "count": len(response_data)
        }, status=200)     

@permission_classes([HasRoleAndDataPermission])
class HMSConsolidatedDataView(APIView):
    
    def parse_tat_format(self, tat_str):
        """Parse TAT format like '2D 3H 45M' and return total seconds"""
        if not tat_str or tat_str == 'N/A':
            return None
        try:
            total_seconds = 0
            days = re.search(r'(\d+)D', str(tat_str))
            hours = re.search(r'(\d+)H', str(tat_str))
            minutes = re.search(r'(\d+)M', str(tat_str))

            if days:
                total_seconds += int(days.group(1)) * 86400
            if hours:
                total_seconds += int(hours.group(1)) * 3600
            if minutes:
                total_seconds += int(minutes.group(1)) * 60

            return total_seconds if total_seconds > 0 else None
        except:
            return None

    def get(self, request):
        # Get date parameters - support both single date and date range
        single_date = request.query_params.get('date')
        from_date = request.query_params.get('from_date')
        to_date = request.query_params.get('to_date')
        
        # Define Indian timezone
        ist = pytz.timezone('Asia/Kolkata')
        
        # ---------------- DATE FILTER ----------------
        try:
            if from_date and to_date:
                from_date_obj = datetime.strptime(from_date, '%Y-%m-%d')
                to_date_obj = datetime.strptime(to_date, '%Y-%m-%d')
                to_date_obj = to_date_obj.replace(hour=23, minute=59, second=59)
            elif single_date:
                from_date_obj = datetime.strptime(single_date, '%Y-%m-%d')
                to_date_obj = from_date_obj.replace(hour=23, minute=59, second=59)
            else:
                # Default to today if no date provided
                today = datetime.now(ist).date()
                from_date_obj = datetime.combine(today, datetime.min.time())
                to_date_obj = datetime.combine(today, datetime.max.time())
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)
        
        from_date_ist = ist.localize(from_date_obj)
        to_date_ist = ist.localize(to_date_obj)
        
        try:
            # Connect to MongoDB to get test details from core_testdetails
            client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
            db = client.Diagnostics
            test_details_collection = db.core_testdetails
            
            # ---------------- BARCODE RECORDS ----------------
            barcode_records = list(
                Hmsbarcode.objects.filter(
                    date__gte=from_date_ist,
                    date__lte=to_date_ist
                ).order_by('-date', 'barcode')
            )
            
            if not barcode_records:
                return Response({
                    "data": [],
                    "count": 0
                }, status=200)
            
            barcodes = [b.barcode for b in barcode_records if b.barcode]
            
            # ---------------- SAMPLE STATUS ----------------
            sample_qs = list(
                Hmssamplestatus.objects.filter(
                    barcode__in=barcodes
                ).only('barcode', 'testdetails')
            )
            
            sample_dict = {s.barcode: s for s in sample_qs}
            
            # ---------------- TEST VALUES (TestValue) ----------------
            testvalue_qs = list(
                TestValue.objects.filter(
                    barcode__in=barcodes
                ).order_by('-created_date', '-lastmodified_date')
            )
            
            testvalue_dict = {}
            for tv in testvalue_qs:
                testvalue_dict.setdefault(tv.barcode, []).append(tv)
            
            # ---------------- MB TEST VALUES (MBTestValue) ----------------
            mbtestvalue_qs = list(
                MBTestValue.objects.filter(
                    barcode__in=barcodes
                ).order_by('-created_date', '-lastmodified_date')
            )
            
            mbtestvalue_dict = {}
            for mbtv in mbtestvalue_qs:
                mbtestvalue_dict.setdefault(mbtv.barcode, []).append(mbtv)
            
            # ---------------- TEST MASTER (Mongo) ----------------
            test_ids = set()
            for barcode_record in barcode_records:
                try:
                    tests = barcode_record.testdetails if isinstance(barcode_record.testdetails, list) else json.loads(barcode_record.testdetails)
                    for t in tests:
                        if t.get("test_id"):
                            test_ids.add(t.get("test_id"))
                except:
                    continue
            
            test_master_dict = {}
            if test_ids:
                cursor = test_details_collection.find(
                    {"test_id": {"$in": list(test_ids)}},
                    {"_id": 0}
                )
                for doc in cursor:
                    test_master_dict[doc["test_id"]] = doc
            
            # ---------------- HELPER FUNCTION ----------------
            def convert_to_iso_if_needed(time_str):
                """Convert time string to ISO format if it's not pending/null"""
                if not time_str or time_str in ["pending", "null"]:
                    return time_str
                try:
                    dt_ist = datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
                    dt_ist_aware = ist.localize(dt_ist)
                    dt_utc = dt_ist_aware.astimezone(pytz.UTC)
                    return dt_utc.isoformat()
                except:
                    return time_str
            
            # ---------------- RESPONSE BUILD ----------------
            response_data = []
            processed_barcodes = set()  # Track processed barcodes to avoid duplicates
            
            for barcode_record in barcode_records:
                barcode = barcode_record.barcode
                
                if not barcode or barcode in processed_barcodes:
                    continue
                
                # Parse JSON fields from barcode record to get test_ids
                try:
                    barcode_tests = barcode_record.testdetails if isinstance(barcode_record.testdetails, list) else json.loads(barcode_record.testdetails)
                except json.JSONDecodeError:
                    continue
                
                # Get sample status data
                sample_obj = sample_dict.get(barcode)
                sample_tests = []
                if sample_obj:
                    try:
                        sample_tests = sample_obj.testdetails if isinstance(sample_obj.testdetails, list) else json.loads(sample_obj.testdetails)
                    except json.JSONDecodeError:
                        sample_tests = []
                
                # Create a dictionary to map test_id to sample status info
                sample_by_id = {t.get("test_id"): t for t in sample_tests if t.get("test_id")}
                
                # Combine TestValue and MBTestValue data
                testvalues = testvalue_dict.get(barcode, [])
                mbtestvalues = mbtestvalue_dict.get(barcode, [])
                
                testvalue_by_id = {}
                
                # First, process TestValue records
                for tv in testvalues:
                    try:
                        tv_list = tv.testdetails if isinstance(tv.testdetails, list) else json.loads(tv.testdetails)
                        for t in tv_list:
                            if t.get("test_id") and t.get("test_id") not in testvalue_by_id:
                                testvalue_by_id[t.get("test_id")] = t
                    except:
                        continue
                
                # Then, process MBTestValue records (will override if same test_id exists)
                for mbtv in mbtestvalues:
                    try:
                        mbtv_list = mbtv.testdetails if isinstance(mbtv.testdetails, list) else json.loads(mbtv.testdetails)
                        for t in mbtv_list:
                            test_id = t.get("test_id")
                            if test_id:
                                # Check if we should use MBTestValue data
                                # If test_id not in testvalue_by_id, add it directly
                                if test_id not in testvalue_by_id:
                                    testvalue_by_id[test_id] = t
                                else:
                                    # If test_id exists, merge the data, preferring non-null/non-pending values
                                    existing = testvalue_by_id[test_id]
                                    
                                    # Update approve_time if MBTestValue has a valid value
                                    if t.get("approve_time") and t.get("approve_time") not in ["pending", "null", None]:
                                        if not existing.get("approve_time") or existing.get("approve_time") in ["pending", "null"]:
                                            existing["approve_time"] = t.get("approve_time")
                                    
                                    # Update dispatch_time if MBTestValue has a valid value
                                    if t.get("dispatch_time") and t.get("dispatch_time") not in ["pending", "null", None]:
                                        if not existing.get("dispatch_time") or existing.get("dispatch_time") in ["pending", "null"]:
                                            existing["dispatch_time"] = t.get("dispatch_time")
                    except:
                        continue
                
                # Format registration time from barcode record's created_date and convert to IST
                registered_time = None
                if barcode_record.created_date:
                    # Convert to IST timezone
                    if hasattr(barcode_record.created_date, 'astimezone'):
                        # If it's a timezone-aware datetime, convert to IST then UTC
                        registered_dt_ist = barcode_record.created_date.astimezone(ist)
                    else:
                        # If it's a naive datetime, assume it's UTC and convert to IST
                        utc = pytz.UTC
                        registered_dt_utc = utc.localize(barcode_record.created_date)
                        registered_dt_ist = registered_dt_utc.astimezone(ist)
                    
                    # Convert to UTC for ISO format
                    registered_dt_utc = registered_dt_ist.astimezone(pytz.UTC)
                    registered_time = registered_dt_utc.isoformat()
                
                # Process each test
                for test in barcode_tests:
                    test_id = test.get('test_id')
                    
                    if not test_id:
                        continue
                    
                    # Get sample status for this test_id
                    sample_data = sample_by_id.get(test_id, {})
                    
                    # Skip if sample status is "Outsource"
                    if sample_data.get('samplestatus') == 'Outsource':
                        continue
                    
                    # Get test master data
                    test_master = test_master_dict.get(test_id, {})
                    
                    if not test_master:
                        continue
                    
                    # Get expected TAT
                    expected_tat_display = (
                        test_master.get("TAT_Time")
                        or test_master.get("tat_time")
                        or (f"{test_master.get('TAT_hours')}H" if test_master.get("TAT_hours") else "N/A")
                    )
                    
                    expected_tat_seconds = self.parse_tat_format(expected_tat_display)
                    
                    # Get timestamps and convert to ISO format
                    approval_time = convert_to_iso_if_needed(
                        testvalue_by_id.get(test_id, {}).get("approve_time")
                    )
                    
                    dispatch_time = convert_to_iso_if_needed(
                        testvalue_by_id.get(test_id, {}).get("dispatch_time")
                    )
                    
                    collected_time = convert_to_iso_if_needed(
                        sample_data.get("samplecollected_time")
                    )
                    
                    received_time = convert_to_iso_if_needed(
                        sample_data.get("received_time")
                    )
                    
                    # -------- ACTUAL TAT (approval_time - registered_time) --------
                    tat_time = "pending"
                    tat_seconds = None
                    
                    if collected_time and approval_time not in ["pending", "null", None]:
                        try:
                            col_dt = datetime.fromisoformat(collected_time.replace('Z', '+00:00'))
                            app_dt = datetime.fromisoformat(approval_time.replace('Z', '+00:00'))
                            diff = app_dt - col_dt
                            tat_seconds = int(diff.total_seconds())
                            tat_time = str(timedelta(seconds=tat_seconds))
                        except:
                            tat_time = "pending"
                    
                    # -------- TOTAL PROCESSING TIME (dispatch_time - collected_time) --------
                    total_processing_time = "pending"
                    total_seconds = None
                    
                    if registered_time not in ["pending", "null", None] and dispatch_time not in ["pending", "null", None]:
                        try:
                            reg_dt = datetime.fromisoformat(registered_time.replace('Z', '+00:00'))
                            dis_dt = datetime.fromisoformat(dispatch_time.replace('Z', '+00:00'))
                            diff = dis_dt - reg_dt
                            total_seconds = int(diff.total_seconds())
                            total_processing_time = str(timedelta(seconds=total_seconds))
                        except:
                            total_processing_time = "pending"
                    
                    # -------- TAT Overage (using total_processing_time) --------
                    tat_out_time = None
                    tat_status = "pending"
                    
                    if tat_seconds is not None and expected_tat_seconds:
                        if tat_seconds > expected_tat_seconds:
                            over = tat_seconds - expected_tat_seconds
                            tat_out_time = str(timedelta(seconds=over))
                            tat_status = "exceeded"
                        else:
                            tat_status = "within_limit"
                    
                    response_data.append({
                        "patient_id": barcode_record.patient_id or '',
                        "patient_name": barcode_record.patientname or '',
                        "age": barcode_record.age or 0,
                        "gender": barcode_record.gender or '',
                        "phone": getattr(barcode_record, 'phone', '') or '',
                        "ref_doctor": barcode_record.ref_doctor or '',
                        "date": registered_time,
                        "registered_time": registered_time,
                        "barcode": barcode,
                        "test_id": test_id,
                        "test_name": test_master.get("test_name"),
                        "department": test_master.get("department"),
                        "collected_time": collected_time,
                        "received_time": received_time,
                        "approval_time": approval_time,
                        "dispatch_time": dispatch_time,
                        "expected_tat": expected_tat_display,
                        "tat_time": tat_time,
                        "tat_out_time": tat_out_time,
                        "tat_status": tat_status,
                        "total_processing_time": total_processing_time
                    })
                
                # Mark this barcode as processed
                processed_barcodes.add(barcode)
            
            return Response({
                "data": response_data,
                "count": len(response_data)
            }, status=200)
            
        except Exception as e:
            return Response({
                "error": str(e)
            }, status=500)


@permission_classes([HasRoleAndDataPermission])
class FranchiseConsolidatedDataView(APIView):
    
    def parse_tat_format(self, tat_str):
        """Parse TAT format like '2D 3H 45M' and return total seconds"""
        if not tat_str or tat_str == 'N/A':
            return None
        try:
            total_seconds = 0
            days = re.search(r'(\d+)D', str(tat_str))
            hours = re.search(r'(\d+)H', str(tat_str))
            minutes = re.search(r'(\d+)M', str(tat_str))

            if days:
                total_seconds += int(days.group(1)) * 86400
            if hours:
                total_seconds += int(hours.group(1)) * 3600
            if minutes:
                total_seconds += int(minutes.group(1)) * 60

            return total_seconds if total_seconds > 0 else None
        except:
            return None
    
    def get(self, request):
        # Get date parameters - support both single date and date range
        single_date = request.query_params.get('date')
        from_date = request.query_params.get('from_date')
        to_date = request.query_params.get('to_date')
        
        # Define Indian timezone
        ist = pytz.timezone('Asia/Kolkata')
        
        # ---------------- DATE FILTER ----------------
        try:
            if from_date and to_date:
                from_date_obj = datetime.strptime(from_date, '%Y-%m-%d').date()
                to_date_obj = datetime.strptime(to_date, '%Y-%m-%d').date()
                use_date_range = True
            elif single_date:
                input_date = datetime.strptime(single_date, '%Y-%m-%d').date()
                use_date_range = False
            else:
                # Default to today if no date provided
                input_date = datetime.now(ist).date()
                use_date_range = False
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)
        
        try:
            # ---------------- MONGODB CONNECTION ----------------
            client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
            db = client.franchise
            franchise_billing_collection = db.franchise_billing
            franchise_sample_collection = db.franchise_sample
            franchise_patient_collection = db.franchise_patient
            
            # Also connect to test details for TAT information
            diagnostics_db = client.Diagnostics
            test_details_collection = diagnostics_db.core_testdetails
            
            # ---------------- GET BILLING RECORDS ----------------
            billing_records = list(franchise_billing_collection.find({}))
            
            if not billing_records:
                client.close()
                return Response({
                    "data": [],
                    "count": 0
                }, status=200)
            
            # ---------------- HELPER FUNCTION ----------------
            def convert_to_iso_if_needed(time_str):
                """Convert time string to ISO format if it's not pending/null"""
                if not time_str or time_str in ["pending", "null"]:
                    return time_str
                try:
                    # Handle ISO format input
                    if 'T' in str(time_str) and 'Z' in str(time_str):
                        dt_utc = datetime.fromisoformat(str(time_str).replace('Z', '+00:00'))
                        return dt_utc.isoformat()
                    
                    # Handle standard format - assume IST and convert to UTC
                    dt_ist = datetime.strptime(str(time_str), '%Y-%m-%d %H:%M:%S')
                    dt_ist_aware = ist.localize(dt_ist)
                    dt_utc = dt_ist_aware.astimezone(pytz.UTC)
                    return dt_utc.isoformat()
                except:
                    return time_str
            
            # ---------------- RESPONSE BUILD ----------------
            response_data = []
            processed_barcodes = set()  # Track processed barcodes to avoid duplicates
            
            # Collect all test_ids for batch lookup
            all_test_ids = set()
            barcode_to_billing = {}
            
            for billing in billing_records:
                # Filter by registrationDate in Python
                if billing.get('registrationDate'):
                    registration_date = billing['registrationDate']
                    if hasattr(registration_date, 'date'):
                        bill_record_date = registration_date.date()
                    else:
                        if isinstance(registration_date, str):
                            bill_record_date = datetime.strptime(registration_date, '%Y-%m-%d').date()
                        else:
                            bill_record_date = registration_date.date()
                    
                    # Apply date filtering based on mode
                    if use_date_range:
                        if not (from_date_obj <= bill_record_date <= to_date_obj):
                            continue
                    else:
                        if bill_record_date != input_date:
                            continue
                else:
                    continue
                
                patient_id = billing.get('patient_id')
                barcode = billing.get('barcode')
                
                if not barcode or not patient_id:
                    continue
                
                # Skip if we've already processed this barcode
                if barcode in processed_barcodes:
                    continue
                
                barcode_to_billing[barcode] = billing
            
            # Get all barcodes
            barcodes = list(barcode_to_billing.keys())
            
            if not barcodes:
                client.close()
                return Response({
                    "data": [],
                    "count": 0
                }, status=200)
            
            # ---------------- GET SAMPLE STATUS ----------------
            sample_statuses = list(franchise_sample_collection.find({"barcode": {"$in": barcodes}}))
            sample_dict = {s['barcode']: s for s in sample_statuses if s.get('barcode')}
            
            # ---------------- GET PATIENTS ----------------
            patient_ids = list(set(b.get('patient_id') for b in barcode_to_billing.values() if b.get('patient_id')))
            patients = list(franchise_patient_collection.find({"patient_id": {"$in": patient_ids}}))
            patient_dict = {p['patient_id']: p for p in patients if p.get('patient_id')}
            
            # ---------------- GET TEST VALUES (TestValue) ----------------
            testvalue_qs = list(
                TestValue.objects.filter(
                    barcode__in=barcodes
                ).order_by('-created_date', '-lastmodified_date')
            )
            
            testvalue_dict = {}
            for tv in testvalue_qs:
                testvalue_dict.setdefault(tv.barcode, []).append(tv)
            
            # ---------------- GET MB TEST VALUES (MBTestValue) ----------------
            mbtestvalue_qs = list(
                MBTestValue.objects.filter(
                    barcode__in=barcodes
                ).order_by('-created_date', '-lastmodified_date')
            )
            
            mbtestvalue_dict = {}
            for mbtv in mbtestvalue_qs:
                mbtestvalue_dict.setdefault(mbtv.barcode, []).append(mbtv)
            
            # Collect test_ids for test master lookup
            all_test_ids = set()
            for sample in sample_statuses:
                try:
                    testdetails_str = sample.get('testdetails', '[]')
                    if isinstance(testdetails_str, str):
                        tests = json.loads(testdetails_str)
                    else:
                        tests = testdetails_str
                    
                    for test in tests:
                        if test.get('test_id'):
                            all_test_ids.add(test.get('test_id'))
                except:
                    continue
            
            # ---------------- GET TEST MASTER DATA ----------------
            test_master_dict = {}
            if all_test_ids:
                cursor = test_details_collection.find(
                    {"test_id": {"$in": list(all_test_ids)}},
                    {"_id": 0}
                )
                for doc in cursor:
                    test_master_dict[doc.get("test_id")] = doc
            
            # ---------------- PROCESS EACH BARCODE ----------------
            for barcode in barcodes:
                billing = barcode_to_billing[barcode]
                patient_id = billing.get('patient_id')
                
                # Get sample status
                sample_status = sample_dict.get(barcode)
                if not sample_status:
                    continue
                
                # Get patient details
                patient = patient_dict.get(patient_id)
                if not patient:
                    continue
                
                # Parse sample tests
                try:
                    testdetails_str = sample_status.get('testdetails', '[]')
                    if isinstance(testdetails_str, str):
                        sample_tests = json.loads(testdetails_str)
                    else:
                        sample_tests = testdetails_str
                except json.JSONDecodeError:
                    continue
                
                # Combine TestValue and MBTestValue data
                testvalues = testvalue_dict.get(barcode, [])
                mbtestvalues = mbtestvalue_dict.get(barcode, [])
                
                testvalue_by_id = {}
                
                # First, process TestValue records
                for tv in testvalues:
                    try:
                        tv_list = tv.testdetails if isinstance(tv.testdetails, list) else json.loads(tv.testdetails)
                        for t in tv_list:
                            test_id = t.get('test_id')
                            if test_id and test_id not in testvalue_by_id:
                                testvalue_by_id[test_id] = t
                    except:
                        continue
                
                # Then, process MBTestValue records (will override if same test_id exists)
                for mbtv in mbtestvalues:
                    try:
                        mbtv_list = mbtv.testdetails if isinstance(mbtv.testdetails, list) else json.loads(mbtv.testdetails)
                        for t in mbtv_list:
                            test_id = t.get("test_id")
                            if test_id:
                                # Check if we should use MBTestValue data
                                # If test_id not in testvalue_by_id, add it directly
                                if test_id not in testvalue_by_id:
                                    testvalue_by_id[test_id] = t
                                else:
                                    # If test_id exists, merge the data, preferring non-null/non-pending values
                                    existing = testvalue_by_id[test_id]
                                    
                                    # Update approve_time if MBTestValue has a valid value
                                    if t.get("approve_time") and t.get("approve_time") not in ["pending", "null", None]:
                                        if not existing.get("approve_time") or existing.get("approve_time") in ["pending", "null"]:
                                            existing["approve_time"] = t.get("approve_time")
                                    
                                    # Update dispatch_time if MBTestValue has a valid value
                                    if t.get("dispatch_time") and t.get("dispatch_time") not in ["pending", "null", None]:
                                        if not existing.get("dispatch_time") or existing.get("dispatch_time") in ["pending", "null"]:
                                            existing["dispatch_time"] = t.get("dispatch_time")
                    except:
                        continue
                
                # Format registration time and convert to ISO
                registered_time = None
                if billing.get('registrationDate'):
                    registration_date = billing['registrationDate']
                    if hasattr(registration_date, 'astimezone'):
                        # Already timezone-aware
                        reg_dt_utc = registration_date.astimezone(pytz.UTC)
                        registered_time = reg_dt_utc.isoformat()
                    elif hasattr(registration_date, 'strftime'):
                        # Naive datetime - assume UTC
                        reg_dt = pytz.UTC.localize(registration_date)
                        registered_time = reg_dt.isoformat()
                    else:
                        registered_time = str(registration_date)
                
                # Process each test
                for test in sample_tests:
                    test_id = test.get('test_id')
                    
                    if not test_id:
                        continue
                    
                    # Get test master data based on test_id
                    test_master = test_master_dict.get(test_id, {})
                    
                    if not test_master:
                        continue
                    
                    # Get test_name from test_master
                    testname = test_master.get('test_name', 'N/A')
                    
                    # Get test value data
                    test_value_data = testvalue_by_id.get(test_id, {})
                    
                    # Get expected TAT
                    expected_tat_display = (
                        test_master.get("TAT_Time")
                        or test_master.get("tat_time")
                        or (f"{test_master.get('TAT_hours')}H" if test_master.get("TAT_hours") else "N/A")
                    )
                    
                    expected_tat_seconds = self.parse_tat_format(expected_tat_display)
                    
                    # Get timestamps and convert to ISO format
                    approval_time = convert_to_iso_if_needed(
                        test_value_data.get("approve_time", "pending")
                    )
                    
                    collected_time = convert_to_iso_if_needed(
                        test.get("samplecollected_time", "pending")
                    )
                    
                    received_time = convert_to_iso_if_needed(
                        test.get("received_time", "pending")
                    )
                    
                    # Get department (prefer from TestValue, fallback to test master)
                    department = (
                        test_value_data.get('department') 
                        or test_master.get('department') 
                        or 'N/A'
                    )
                    
                    # -------- ACTUAL TAT (approval_time - registered_time) --------
                    tat_time = "pending"
                    tat_seconds = None
                    
                    if collected_time and approval_time not in ["pending", "null", None]:
                        try:
                            col_dt = datetime.fromisoformat(collected_time.replace('Z', '+00:00'))
                            app_dt = datetime.fromisoformat(approval_time.replace('Z', '+00:00'))
                            diff = app_dt - col_dt
                            tat_seconds = int(diff.total_seconds())
                            tat_time = str(timedelta(seconds=tat_seconds))
                        except:
                            tat_time = "pending"
                    
                    # -------- PROCESSING TIME (approval_time - collected_time) --------
                    total_processing_time = "pending"
                    total_seconds = None
                    
                    if registered_time not in ["pending", "null", None] and approval_time not in ["pending", "null", None]:
                        try:
                            reg_dt = datetime.fromisoformat(registered_time.replace('Z', '+00:00'))
                            app_dt = datetime.fromisoformat(approval_time.replace('Z', '+00:00'))
                            diff = app_dt - reg_dt
                            total_seconds = int(diff.total_seconds())
                            total_processing_time = str(timedelta(seconds=total_seconds))
                        except:
                            total_processing_time = "pending"
                    
                    # -------- TAT Overage (using total_processing_time) --------
                    tat_out_time = None
                    tat_status = "pending"
                    
                    if tat_seconds is not None and expected_tat_seconds:
                        if tat_seconds > expected_tat_seconds:
                            over = tat_seconds - expected_tat_seconds
                            tat_out_time = str(timedelta(seconds=over))
                            tat_status = "exceeded"
                        else:
                            tat_status = "within_limit"
                    
                    response_data.append({
                        "patient_id": patient_id,
                        "patient_name": patient.get('patientname', 'N/A'),
                        "age": patient.get('age', 'N/A'),
                        "date": registered_time,
                        "registered_time": registered_time,
                        "barcode": barcode,
                        "test_id": test_id,
                        "test_name": testname,
                        "department": department,
                        "collected_time": collected_time,
                        "received_time": received_time,
                        "approval_time": approval_time,
                        "expected_tat": expected_tat_display,
                        "tat_time": tat_time,
                        "tat_out_time": tat_out_time,
                        "tat_status": tat_status,
                        "total_processing_time": total_processing_time
                    })
                
                # Mark this barcode as processed
                processed_barcodes.add(barcode)
            
            # Close MongoDB connection
            client.close()
            
            return Response({
                "data": response_data,
                "count": len(response_data)
            }, status=200)
            
        except Exception as e:
            if 'client' in locals():
                client.close()
            return Response({
                "error": str(e)
            }, status=500)

# @permission_classes([HasRoleAndDataPermission])
class HMSTestCountView(APIView):
    def post(self, request):
        from_date = request.data.get('from_date')
        to_date = request.data.get('to_date')
        
        if not from_date or not to_date:
            return Response({"error": "from_date and to_date are required"}, status=400)
        
        try:
            # The date in Hmsbarcode is models.DateField()
            # We filter directly on the date objects
            from_date_obj = datetime.strptime(from_date, '%Y-%m-%d').date()
            to_date_obj = datetime.strptime(to_date, '%Y-%m-%d').date()
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)

        # Filter Hmsbarcode records in the given date range
        # Note: In Mongo via Djongo, __range or __gte/__lte works on DateField
        qs = Hmsbarcode.objects.filter(date__gte=from_date_obj, date__lte=to_date_obj)
        
        test_counts = {}
        
        for record in qs:
            td = record.testdetails
            gender = str(record.gender).lower() if hasattr(record, 'gender') and record.gender else 'unknown'
            
            if isinstance(td, str):
                try:
                    td = json.loads(td)
                except:
                    continue
            
            if not isinstance(td, list):
                continue
                
            for test in td:
                test_id = test.get('test_id')
                # Try to get the most specific name available
                test_name = test.get('testname') or test.get('test_name') or "Unknown Test"
                
                if test_id:
                    # Use a unique key for grouping
                    key = (test_id, test_name)
                    if key not in test_counts:
                        test_counts[key] = {
                            "test_id": test_id,
                            "test_name": test_name,
                            "count": 0,
                            "male_count": 0,
                            "female_count": 0
                        }
                    test_counts[key]["count"] += 1
                    if gender in ['male', 'm']:
                        test_counts[key]["male_count"] += 1
                    elif gender in ['female', 'f']:
                        test_counts[key]["female_count"] += 1

        # Convert to list and sort by count descending
        report_data = list(test_counts.values())
        report_data.sort(key=lambda x: x['count'], reverse=True)
        
        return Response({
            "data": report_data,
            "total_records": len(report_data),
            "total_test_sum": sum(item['count'] for item in report_data)
        }, status=200)
