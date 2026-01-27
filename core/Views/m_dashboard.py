from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from pyauth.auth import HasRoleAndDataPermission
from django.views.decorators.csrf import csrf_exempt
from ..models import Billing
from django.db.models import Sum
from pymongo import MongoClient
import os
import json
from datetime import datetime, timedelta
from django.utils import timezone
import logging
from bson.decimal128 import Decimal128
import traceback

logger = logging.getLogger(__name__)

def to_float(value):
    """ Safely convert various number formats (BSON Decimal128, string, dict) to float """
    if value is None:
        return 0.0
    
    try:
        if isinstance(value, (int, float)):
            return float(value)
        
        if isinstance(value, str):
            clean_val = value.strip()
            if not clean_val:
                return 0.0
            return float(clean_val)
            
        # Handle Decimal128 (PyMongo)
        if isinstance(value, Decimal128):
            return float(value.to_decimal())
            
        # Handle decimal128-like object (sometimes imported differently)
        if hasattr(value, 'to_decimal'):
            return float(value.to_decimal())

        # Handle Extended JSON dict format i.e. {"$numberDecimal": "123.45"}
        if isinstance(value, dict):
            if '$numberDecimal' in value:
                return float(value['$numberDecimal'])
            # Fallback for other dict structures if any
            return 0.0
            
        return float(value)
    except Exception as e:
        # logger.warning(f"Failed to convert value {value} to float: {e}")
        return 0.0

@api_view(['POST'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def m_dashboard_stats(request):
    try:
        # Date Filtering
        date_str = request.data.get('date')
        from_date_str = request.data.get('from_date')
        to_date_str = request.data.get('to_date')
        
        start_date = None
        end_date = None

        if from_date_str and to_date_str:
            try:
                start_date = datetime.strptime(from_date_str, '%Y-%m-%d')
                end_date = datetime.strptime(to_date_str, '%Y-%m-%d') + timedelta(days=1)
            except ValueError:
                return Response({'error': 'Invalid date format. Use YYYY-MM-DD'}, status=400)
        elif date_str:
            try:
                start_date = datetime.strptime(date_str, '%Y-%m-%d')
                end_date = start_date + timedelta(days=1)
            except ValueError:
                return Response({'error': 'Invalid date format. Use YYYY-MM-DD'}, status=400)
        
        # If no date provided, default to today
        if not start_date:
            today = timezone.localtime(timezone.now()).date()
            start_date = datetime.combine(today, datetime.min.time())
            end_date = start_date + timedelta(days=1)
            
        # Ensure UTC/Offset-naive handling matches DB expectations
        # MongoDB usually stores naive datetime as UTC.
        # If start_date is timezon-aware, converting to naive might be needed depending on how PyMongo is configured or how data was inserted.
        # Assuming standard usage:
        
        # Initialize Stats
        stats = {
            "samples": {
                "total": 0,
                "segments": {
                    "home_collection": 0,
                    "b2b": 0,
                    "franchise": 0,
                    "company_health_check": 0,
                    "other": 0
                }
            },
            "tests": {
                "total": 0
            },
            "financials": {
                "gross": {
                    "b2b": 0,
                    "home_collection": 0,
                    "company_health_check": 0,
                    "franchise_share": 0
                },
                "credit_amount": 0,
                "net_amount": 0
            }
        }

        # --- 1. Core Billing (Django) ---
        core_query = Billing.objects.filter(date__gte=start_date, date__lt=end_date)
        
        for bill in core_query:
            # Segment
            segment = (bill.segment or "").strip()
            # Normalize segment matching
            if "Home" in segment and "Collection" in segment:
                stats["samples"]["segments"]["home_collection"] += 1
                stats["financials"]["gross"]["home_collection"] += to_float(bill.totalAmount)
            elif "B2B" in segment or (bill.B2B and bill.B2B.strip()): 
                stats["samples"]["segments"]["b2b"] += 1
                stats["financials"]["gross"]["b2b"] += to_float(bill.totalAmount)
            elif "Company" in segment and "Health" in segment:
                stats["samples"]["segments"]["company_health_check"] += 1
                stats["financials"]["gross"]["company_health_check"] += to_float(bill.totalAmount)
            else:
                stats["samples"]["segments"]["other"] += 1

            # Total Samples
            stats["samples"]["total"] += 1

            # Tests Count
            if bill.testdetails:
                try:
                    td = bill.testdetails
                    if isinstance(td, str):
                        td = json.loads(td)
                    if isinstance(td, list):
                        stats["tests"]["total"] += len(td)
                except:
                    pass
            
            # Financials
            stats["financials"]["credit_amount"] += to_float(bill.credit_amount)
            stats["financials"]["net_amount"] += to_float(bill.netAmount)


        # --- MongoDB Connection ---
        client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
        
        # --- 2. Franchise (MongoDB) ---
        try:
            db_franchise = client.franchise
            col_franchise = db_franchise.franchise_billing
            
            franchise_query = {
                "created_date": {"$gte": start_date, "$lt": end_date}
            }
            
            franchise_bills = list(col_franchise.find(franchise_query))
            
            for bill in franchise_bills:
                stats["samples"]["segments"]["franchise"] += 1
                stats["samples"]["total"] += 1
                
                try:
                    # Credit Amount
                    credit_val = to_float(bill.get("credit_amount"))
                    stats["financials"]["credit_amount"] += credit_val
                    
                    # Franchisor Share (billed_amount is priority, else netAmount)
                    billed_amt = to_float(bill.get("billed_amount"))
                    net_amt_val = to_float(bill.get("netAmount"))

                    if billed_amt == 0 and net_amt_val > 0:
                        billed_amt = net_amt_val

                    stats["financials"]["gross"]["franchise_share"] += billed_amt
                    
                    # Net Amount
                    stats["financials"]["net_amount"] += net_amt_val
                    
                except Exception as e:
                    logger.error(f"Error processing franchise bill financial: {e}")

                # Tests
                try:
                    td = bill.get("testdetails")
                    if isinstance(td, str):
                        td = json.loads(td)
                    if isinstance(td, list):
                        stats["tests"]["total"] += len(td)
                except:
                    pass

        except Exception as e:
            logger.error(f"Error fetching franchise data: {e}")
            logger.error(traceback.format_exc())


        # --- 3. Company Health Checkup (MongoDB) ---
        try:
            db_corporate = client.Corporatehealthcheckup
            col_corporate = db_corporate.core_billing 
            
            corp_query = {
                "created_date": {"$gte": start_date, "$lt": end_date}
            }
            
            corp_bills = list(col_corporate.find(corp_query))
            
            for bill in corp_bills:
                stats["samples"]["segments"]["company_health_check"] += 1
                stats["samples"]["total"] += 1
                
                try:
                    # Amounts
                    # Try 'total', then 'totalAmount', then 'netAmount'
                    total_amt = to_float(bill.get("total"))
                    if total_amt == 0:
                        total_amt = to_float(bill.get("totalAmount"))
                    if total_amt == 0:
                        total_amt = to_float(bill.get("netAmount"))
                        
                    stats["financials"]["gross"]["company_health_check"] += total_amt
                    
                    # Net
                    net_amt = to_float(bill.get("netAmount"))
                    stats["financials"]["net_amount"] += net_amt
                    
                    # Credit
                    credit_val = to_float(bill.get("credit_amount"))
                    # If credit_amount is 0/missing, check paymentMode
                    if credit_val == 0:
                        pm = bill.get("paymentMode", "")
                        if isinstance(pm, str) and pm.lower() in ["credit", "due"]:
                            credit_val = net_amt
                            
                    stats["financials"]["credit_amount"] += credit_val
                    
                except:
                    pass

                # Tests
                try:
                    td = bill.get("testdetails")
                    if isinstance(td, str):
                        td = json.loads(td)
                    if isinstance(td, list):
                        stats["tests"]["total"] += len(td)
                except:
                    pass

        except Exception as e:
            logger.error(f"Error fetching corporate data: {e}")
            logger.error(traceback.format_exc())

        finally:
            client.close()

        return Response({"success": True, "data": stats})

    except Exception as e:
        logger.error(f"Error in m_dashboard_stats: {str(e)}")
        logger.error(traceback.format_exc())
        return Response({"success": False, "error": str(e)}, status=500)