import os
from pymongo import MongoClient
from dotenv import load_dotenv
from bson import ObjectId

load_dotenv()

def find_orphaned_bills():
    client = MongoClient(os.getenv('GLOBAL_DB_HOST'))
    db = client.Corporatehealthcheckup
    billing_collection = db.core_billing
    invoice_collection = db.core_corporate_invoices
    
    print("Finding bills where paymentMode is 'Paid' but not in any invoice...")
    
    # Get all invoiced bill IDs
    invoiced_bill_ids = []
    all_invoices = list(invoice_collection.find({}, {"bill_items.bill_id": 1}))
    for inv in all_invoices:
        if "bill_items" in inv:
            for item in inv["bill_items"]:
                if "bill_id" in item:
                    invoiced_bill_ids.append(str(item["bill_id"]))
    
    # Find bills with paymentMode 'Paid'
    paid_bills = list(billing_collection.find({"paymentMode": "Paid"}))
    
    orphaned = []
    for bill in paid_bills:
        bid = str(bill.get('_id'))
        if bid not in invoiced_bill_ids:
            orphaned.append(bill)
            
    print(f"Found {len(orphaned)} orphaned 'Paid' bills.")
    if orphaned:
        print("\nSample Orphaned Bill:")
        s = orphaned[0]
        print(f"  _id: {s.get('_id')}")
        print(f"  barcode: {s.get('barcode')}")
        print(f"  invoice_number: {s.get('invoice_number')}")
    
    client.close()

if __name__ == "__main__":
    find_orphaned_bills()
