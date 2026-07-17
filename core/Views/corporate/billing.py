from django.http import JsonResponse, HttpResponse
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
import json
from core.mongo_client import get_client
from rest_framework.decorators import api_view, permission_classes
from pyauth.auth import HasRoleAndDataPermission
from dotenv import load_dotenv
import logging
from datetime import datetime, timedelta
import os, json, traceback
from django.utils.timezone import make_aware
from core.models import TestValue
from bson import json_util
import gridfs
import base64
from bson.objectid import ObjectId
import re
from datetime import timezone, timedelta
from core.pagination import paginate_queryset


load_dotenv()
logger = logging.getLogger(__name__)


@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def corporate_credit_billing(request):
    """
    Get all corporate billing records where paymentMode is 'Credit'
    and map the company_id to company_name from core_company.
    """
    if request.method == "GET":
        client = None
        try:
            client = get_client()
            db = client.Corporatehealthcheckup

            billing_collection = db.core_billing
            company_collection = db.core_company

            # 1. Fetch all companies and create a map of company_id -> company_name
            companies = list(company_collection.find({}, {"_id": 0, "company_id": 1, "company_name": 1}))
            company_map = {str(comp.get("company_id", "")).strip(): comp.get("company_name", "") for comp in companies if comp.get("company_id")}

            # 2. Fetch billing records where paymentMode is Credit
            # 2. Fetch billing records that are not yet fully paid/invoiced
            query = {"$or": [{"paymentMode": "Credit"}, {"paymentMode": {"$exists": False}}, {"paymentMode": None}]}


            req_company_id = request.GET.get('company_id')
            if req_company_id:
                query['company_id'] = req_company_id

            from_date = request.GET.get('from_date')
            to_date = request.GET.get('to_date')

            if from_date or to_date:
                date_query = {}
                if from_date:
                    from_datetime = datetime.strptime(from_date, '%Y-%m-%d')
                    from_datetime = from_datetime.replace(hour=0, minute=0, second=0, microsecond=0)
                    date_query['$gte'] = from_datetime
                if to_date:
                    to_datetime = datetime.strptime(to_date, '%Y-%m-%d')
                    to_datetime = to_datetime.replace(hour=23, minute=59, second=59, microsecond=999999)
                    date_query['$lte'] = to_datetime

                if date_query:
                    query['date'] = date_query

            # Fetch all invoiced bill IDs to exclude them
            invoice_collection = db.core_corporate_invoices
            invoiced_bill_ids = []
            all_invoices = list(invoice_collection.find({}, {"bill_items.bill_id": 1}))
            for inv in all_invoices:
                if "bill_items" in inv:
                    for item in inv["bill_items"]:
                        if "bill_id" in item:
                            invoiced_bill_ids.append(item["bill_id"])

            if invoiced_bill_ids:
                from bson import ObjectId
                oid_list = []
                for bid in invoiced_bill_ids:
                    try:
                        oid_list.append(ObjectId(bid))
                    except Exception as e:
                        logger.debug(f"Skipping invalid bill_id {bid} for ObjectId conversion: {e}")
                        pass
                # Exclude bills that are in any invoice (support ObjectId, string, and integer IDs)
                query["_id"] = {
                    "$nin": oid_list + invoiced_bill_ids + [int(bid) for bid in invoiced_bill_ids if str(bid).isdigit()]
                }




            billing_records = list(billing_collection.find(query).sort("date", -1))

            # 3. Optimize: Fetch all unique employee IDs in one go (chunked to avoid BSON limits)
            employee_ids = list(set(str(bill.get('employee_id', '')).strip() for bill in billing_records if bill.get('employee_id')))
            employee_name_map = {}
            if employee_ids:
                # Chunk IDs to avoid BSON length/limit issues with very large $in lists
                chunk_size = 500
                for i in range(0, len(employee_ids), chunk_size):
                    chunk = employee_ids[i:i + chunk_size]
                    try:
                        employees = list(db.core_chcregistration.find({"employee_id": {"$in": chunk}}))
                        for emp in employees:
                            eid = str(emp.get('employee_id', '')).strip()
                            if eid:
                                employee_name_map[eid] = emp.get('employee_name')
                    except Exception as e:
                        logger.error(f"Error fetching employee chunk: {str(e)}")

            # 4. Format the response

            processed_data = []
            for bill in billing_records:
                bill['_id'] = str(bill['_id'])

                # Get the company name from the map
                company_id = str(bill.get('company_id', '')).strip()
                bill['company_name'] = company_map.get(company_id, "Unknown Company")

                # Format dates
                if 'created_date' in bill and bill['created_date']:
                    bill['created_date'] = bill['created_date'].isoformat() if hasattr(bill['created_date'], 'isoformat') else str(bill['created_date'])
                if 'date' in bill and bill['date']:
                    bill['date'] = bill['date'].isoformat() if hasattr(bill['date'], 'isoformat') else str(bill['date'])

                # Handle test details JSON strings
                if 'testdetails' in bill and isinstance(bill['testdetails'], str):
                    try:
                        bill['testdetails'] = json.loads(bill['testdetails'])
                    except json.JSONDecodeError:
                        bill['testdetails'] = []

                if 'chctestdetails' in bill and isinstance(bill['chctestdetails'], str):
                    try:
                        bill['chctestdetails'] = json.loads(bill['chctestdetails'])
                    except json.JSONDecodeError:
                        bill['chctestdetails'] = []

                # Handle Decimal values (like netAmount)
                if 'netAmount' in bill:
                    bill['netAmount'] = float(str(bill['netAmount'])) if bill['netAmount'] else 0.0

                # ✅ NEW: Fetch employee name from optimized map or fallback to patientname
                employee_id = str(bill.get('employee_id', '')).strip()
                emp_name = employee_name_map.get(employee_id)

                # Fallback sequence: Employee Registration Name -> Billing Patient Name -> "N/A"
                bill['employee_name'] = emp_name or bill.get('patientname') or "N/A"

                # ✅ NEW: Extract package details from chctestdetails
                chc_details = bill.get('chctestdetails', [])
                if isinstance(chc_details, list) and len(chc_details) > 0:
                    bill['package_id'] = chc_details[0].get('test_id', 'N/A')
                    bill['package_name'] = chc_details[0].get('test_name', 'N/A')

                processed_data.append(bill)

            total_count = len(processed_data)
            page_obj, page_meta = paginate_queryset(processed_data, request)
            return JsonResponse({
                "status": "success",
                "data": list(page_obj),
                "companies": companies,
                "count": total_count,
                **page_meta
            }, safe=False)

        except Exception as e:
            logger.error(f"Error fetching corporate credit billing: {str(e)}")
            return JsonResponse({
                "status": "error",
                "message": f"Database error: {str(e)}"
            }, status=500)

        finally:
            # Note: `client` is the shared, pooled MongoClient — do not close it here.
            pass


@api_view(['POST'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def generate_corporate_invoice(request):
    """
    Generate and save a corporate invoice.
    """
    client = None
    try:
        data = request.data
        client = get_client()
        db = client.Corporatehealthcheckup
        invoice_collection = db.core_corporate_invoices

        # Generate invoice number
        today = datetime.now().strftime("%Y%m%d")
        last_invoice = invoice_collection.find_one(sort=[("invoice_number", -1)])

        sequence = 1
        if last_invoice and last_invoice.get("invoice_number", "").startswith(f"CINV{today}"):
            try:
                sequence = int(last_invoice["invoice_number"][-3:]) + 1
            except Exception as e:
                logger.debug(f"Failed to parse invoice sequence number: {e}")
                sequence = 1

        invoice_number = f"CINV{today}{sequence:03d}"

        total_amount = float(data.get("total_amount", 0))
        invoice_data = {
            "invoice_number": invoice_number,
            "company_id": data.get("company_id"),
            "company_name": data.get("company_name"),
            "from_date": data.get("from_date"),
            "to_date": data.get("to_date"),
            "total_amount": total_amount,
            "paid_amount": 0.0,
            "remaining_amount": total_amount,
            "bill_items": data.get("bill_items"),
            "payment_method": data.get("payment_method"),
            "payment_history": [],
            "status": "Generated",
            "created_at": datetime.now().isoformat(),
            "created_by": data.get("auth-user-id") or "system"
        }

        result = invoice_collection.insert_one(invoice_data)

        # Update original billing records to 'Paid' so they don't appear in the pending list
        billing_collection = db.core_billing
        from bson import ObjectId
        for item in invoice_data["bill_items"]:
            bill_id = item.get("bill_id")
            if bill_id:
                try:
                    # Update by ObjectId, String ID, and Integer ID to ensure match
                    filter_q = {"$or": [
                        {"_id": ObjectId(str(bill_id))},
                        {"_id": str(bill_id)},
                        {"_id": int(bill_id) if str(bill_id).isdigit() else None}
                    ]}
                    billing_collection.update_many(
                        filter_q,
                        {"$set": {
                            "paymentMode": "Invoiced",
                            "invoice_number": invoice_number,
                            "invoiced_at": datetime.now().isoformat()
                        }}
                    )
                except Exception as e:
                    logger.error(f"Failed to update billing record {bill_id} as Invoiced: {e}")
                    pass

        return JsonResponse({
            "status": "success",
            "message": "Invoice generated successfully",
            "invoice_number": invoice_number,
            "id": str(result.inserted_id)
        })

    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=500)
    finally:
        # Note: `client` is the shared, pooled MongoClient — do not close it here.
        pass


@api_view(['GET'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def get_corporate_invoices(request):
    """
    Fetch all generated corporate invoices.
    """
    client = None
    try:
        client = get_client()
        db = client.Corporatehealthcheckup
        invoice_collection = db.core_corporate_invoices

        query = {}
        company_id = request.GET.get('company_id')
        if company_id:
            query['company_id'] = company_id

        invoices = list(invoice_collection.find(query, {"_id": 0}).sort("created_at", -1))

        total_count = len(invoices)
        page_obj, page_meta = paginate_queryset(invoices, request)
        return JsonResponse({
            "status": "success",
            "data": list(page_obj),
            "total_count": total_count,
            **page_meta
        })

    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=500)
    finally:
        # Note: `client` is the shared, pooled MongoClient — do not close it here.
        pass

@api_view(['POST', 'PUT'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def update_corporate_invoice(request):
    """
    Update an existing corporate invoice.
    """
    client = None
    try:
        data = request.data
        invoice_number = data.get("invoice_number")
        if not invoice_number:
            return JsonResponse({"status": "error", "message": "Invoice number is required"}, status=400)

        client = get_client()
        db = client.Corporatehealthcheckup
        invoice_collection = db.core_corporate_invoices

        existing_invoice = invoice_collection.find_one({"invoice_number": invoice_number})
        if not existing_invoice:
            return JsonResponse({"status": "error", "message": "Invoice not found"}, status=404)

        total_amount = float(data.get("total_amount", existing_invoice.get("total_amount", 0)))
        new_payment = float(data.get("new_payment", 0))

        current_paid = float(existing_invoice.get("paid_amount", 0))
        updated_paid = current_paid + new_payment
        remaining_amount = total_amount - updated_paid

        payment_history = existing_invoice.get("payment_history", [])
        if new_payment > 0:
            payment_history.append({
                "amount": new_payment,
                "date": data.get("payment_date", datetime.now().isoformat()),
                "method": data.get("payment_method", existing_invoice.get("payment_method")),
                "note": data.get("note", ""),
                "received_by": data.get("auth-user-id") or "system"
            })

        update_data = {
            "total_amount": total_amount,
            "paid_amount": updated_paid,
            "remaining_amount": remaining_amount,
            "payment_method": data.get("payment_method", existing_invoice.get("payment_method")),
            "payment_history": payment_history,
            "status": "Paid" if remaining_amount <= 0 else "Partially Paid",
            "last_modified_at": datetime.now().isoformat(),
            "last_modified_by": data.get("auth-user-id") or "system"
        }

        # Optional fields
        if "bill_items" in data:
            update_data["bill_items"] = data.get("bill_items")

        result = invoice_collection.update_one(
            {"invoice_number": invoice_number},
            {"$set": update_data}
        )

        if result.matched_count == 0:
            return JsonResponse({"status": "error", "message": "Invoice not found"}, status=404)

        # If fully paid, update original billing records to reflect settlement
        if remaining_amount <= 0.01:
            billing_collection = db.core_billing
            bill_items = existing_invoice.get("bill_items", [])
            from bson import ObjectId
            for item in bill_items:
                bill_id = item.get("bill_id")
                if bill_id:
                    try:
                        billing_collection.update_one(
                            {"_id": ObjectId(str(bill_id))},
                            {"$set": {
                                "paymentMode": "Paid",
                                "settled_via_invoice": invoice_number,
                                "settled_at": datetime.now().isoformat()
                            }}
                        )
                        billing_collection.update_one(
                            {"_id": str(bill_id)},
                            {"$set": {
                                "paymentMode": "Paid",
                                "settled_via_invoice": invoice_number,
                                "settled_at": datetime.now().isoformat()
                            }}
                        )
                    except Exception as e:
                        logger.error(f"Failed to mark billing record {bill_id} as Paid via invoice: {e}")
                        pass

        return JsonResponse({
            "status": "success",
            "message": "Invoice updated successfully"
        })

    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=500)
    finally:
        # Note: `client` is the shared, pooled MongoClient — do not close it here.
        pass


@api_view(['POST', 'DELETE'])
@csrf_exempt
@permission_classes([HasRoleAndDataPermission])
def delete_corporate_invoice(request):
    """
    Delete a corporate invoice.
    """
    client = None
    try:
        data = request.data
        invoice_number = data.get("invoice_number")
        if not invoice_number:
            return JsonResponse({"status": "error", "message": "Invoice number is required"}, status=400)

        client = get_client()
        db = client.Corporatehealthcheckup
        invoice_collection = db.core_corporate_invoices
        billing_collection = db.core_billing

        # 1. Find the invoice first to get associated bills
        invoice = invoice_collection.find_one({"invoice_number": invoice_number})
        if not invoice:
            return JsonResponse({"status": "error", "message": "Invoice not found"}, status=404)

        # 2. Revert associated billing records
        bill_items = invoice.get("bill_items", [])
        from bson import ObjectId
        for item in bill_items:
            bill_id = item.get("bill_id")
            if bill_id:
                try:
                    # Update by ObjectId, String ID, and Integer ID to ensure match
                    filter_q = {"$or": [
                        {"_id": ObjectId(str(bill_id))},
                        {"_id": str(bill_id)},
                        {"_id": int(bill_id) if str(bill_id).isdigit() else None}
                    ]}
                    billing_collection.update_many(
                        filter_q,
                        {"$set": {"paymentMode": "Credit"},
                         "$unset": {"invoice_number": "", "invoiced_at": ""}}
                    )
                except Exception as e:
                    logger.error(f"Failed to revert billing record {bill_id} to Credit: {e}")
                    pass

        # 3. Delete the invoice
        result = invoice_collection.delete_one({"invoice_number": invoice_number})

        if result.deleted_count == 0:
            return JsonResponse({"status": "error", "message": "Failed to delete invoice document"}, status=500)

        return JsonResponse({
            "status": "success",
            "message": "Invoice deleted and billing records reverted successfully"
        })


    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=500)
    finally:
        # Note: `client` is the shared, pooled MongoClient — do not close it here.
        pass


@api_view(['GET'])
def export_corporate_invoice_pdf(request):
    """
    Generate a PDF for a corporate invoice.
    """
    client = None
    try:
        invoice_number = request.GET.get('invoice_number')
        if not invoice_number:
            return JsonResponse({"status": "error", "message": "Invoice number is required"}, status=400)

        client = get_client()
        db = client.Corporatehealthcheckup
        invoice = db.core_corporate_invoices.find_one({"invoice_number": invoice_number})

        if not invoice:
            return JsonResponse({"status": "error", "message": "Invoice not found"}, status=404)

        # For a professional PDF, we'd typically use reportlab or a template.
        # For now, let's provide a structured JSON or HTML that can be printed,
        # OR implement a basic PDF if the user has the libraries.
        # Let's assume we want a real PDF. I'll use reportlab if available.

        from django.http import HttpResponse
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

        # Define paths to header/footer images from backend static
        backend_static_dir = os.path.join(settings.BASE_DIR, "core", "static", "images")
        header_path = os.path.join(backend_static_dir, "Header.png")
        footer_path = os.path.join(backend_static_dir, "Footer.png")

        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="Invoice_{invoice_number}.pdf"'

        # Use a custom PageTemplate or draw on Canvas to handle header/footer on every page
        def add_header_footer(canvas, doc):
            canvas.saveState()
            # Draw Header
            if os.path.exists(header_path):
                canvas.drawImage(header_path, 0, A4[1]-100, width=A4[0], height=100, preserveAspectRatio=True, mask='auto')

            # Draw Footer
            if os.path.exists(footer_path):
                canvas.drawImage(footer_path, 0, 0, width=A4[0], height=60, preserveAspectRatio=True, mask='auto')

            # Page Number
            canvas.setFont('Helvetica', 8)
            canvas.drawRightString(A4[0]-40, 20, f"Page {doc.page}")
            canvas.restoreState()

        doc = SimpleDocTemplate(response, pagesize=A4, topMargin=110, bottomMargin=70)
        styles = getSampleStyleSheet()
        elements = []

        # Header / Title
        title_style = ParagraphStyle('TitleStyle', parent=styles['Heading1'], alignment=1, fontSize=16, spaceAfter=20)
        elements.append(Paragraph("CORPORATE HEALTH CHECKUP INVOICE", title_style))

        # Extract common details
        bill_items = invoice.get('bill_items', [])
        patient_names = set(filter(None, [item.get('patient_name') for item in bill_items]))
        package_names = set(filter(None, [item.get('package_name') for item in bill_items]))

        common_patient = list(patient_names)[0] if len(patient_names) == 1 else "Multiple"
        common_package = list(package_names)[0] if len(package_names) == 1 else "Multiple"

        # Info Table
        info_data = [
            [f"Invoice No: {invoice_number}", f"Date: {invoice.get('created_at', '')[:10]}"],
            [f"Company: {invoice.get('company_name')}", f"Period: {invoice.get('from_date')} to {invoice.get('to_date')}"],
            [f"Patient Name: {common_patient}", f"Package: {common_package}"],
            [f"Payment Method: {invoice.get('payment_method')}", f"Status: {invoice.get('status')}"]
        ]
        info_table = Table(info_data, colWidths=[250, 250])
        info_table.setStyle(TableStyle([
            ('FONTNAME', (0,0), (-1,-1), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 10),
            ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ]))
        elements.append(info_table)
        elements.append(Spacer(1, 20))

        # Patient Details Table
        p_header = ["S.No", "Date", "Patient Name", "Employee ID", "Package ID", "Barcode", "Amount (₹)"]
        p_data = [p_header]

        for idx, item in enumerate(bill_items, 1):
            p_data.append([
                str(idx),
                item.get('date', '')[:10],
                item.get('patient_name', 'N/A'),
                item.get('employee_id', 'N/A'),
                item.get('package_id', 'N/A'),
                item.get('barcode', 'N/A'),
                f"{float(item.get('amount', 0)):.2f}"
            ])

        p_table = Table(p_data, colWidths=[30, 65, 110, 80, 80, 80, 70])
        p_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.grey),
            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,0), 10),
            ('BOTTOMPADDING', (0,0), (-1,0), 12),
            ('BACKGROUND', (0,1), (-1,-1), colors.beige),
            ('GRID', (0,0), (-1,-1), 1, colors.black),
            ('ALIGN', (-1,0), (-1,-1), 'RIGHT'),
        ]))
        elements.append(p_table)
        elements.append(Spacer(1, 20))

        # Summary
        summary_data = [
            ["", "Total Amount:", f"INR {float(invoice.get('total_amount', 0)):.2f}"],
            ["", "Paid Amount:", f"INR {float(invoice.get('paid_amount', 0)):.2f}"],
            ["", "Remaining Pending:", f"INR {float(invoice.get('remaining_amount', 0)):.2f}"]
        ]
        summary_table = Table(summary_data, colWidths=[280, 100, 100])
        summary_table.setStyle(TableStyle([
            ('FONTNAME', (1,0), (-1,-1), 'Helvetica-Bold'),
            ('ALIGN', (1,0), (-1,-1), 'RIGHT'),
            ('FONTSIZE', (1,0), (-1,-1), 10),
            ('TEXTCOLOR', (1,2), (-1,2), colors.red if float(invoice.get('remaining_amount', 0)) > 0 else colors.green),
        ]))
        elements.append(summary_table)

        # Build document with header/footer
        doc.build(elements, onFirstPage=add_header_footer, onLaterPages=add_header_footer)
        return response

    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=500)
    finally:
        # Note: `client` is the shared, pooled MongoClient — do not close it here.
        pass
