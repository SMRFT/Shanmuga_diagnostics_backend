# This package replaces the former single-file core/Views/corporate.py.
# Every name that used to live in that module is re-exported here so that
# existing callers doing `corporate.<name>` (e.g. core/urls.py) keep working
# without any changes.

from .samples import (
    get_corporate_sample,
    update_corporate_sample,
    get_corporate_batch_generation_data,
    update_corporate_batch_received_status,
)

from .reports import (
    corporate_overall_report,
    corporate_patient_test_details,
    corporate_approval_report,
    corporate_health_report,
    get_investigation_file,
    get_investigation_status,
)

from .investigation import (
    get_batch_investigation_status,
    save_overall_approval,
    get_batch_corporate_health_reports,
)

from .billing import (
    corporate_credit_billing,
    generate_corporate_invoice,
    get_corporate_invoices,
    update_corporate_invoice,
    delete_corporate_invoice,
    export_corporate_invoice_pdf,
)
