PAGE_MAPPING = {

    #Registration, Billing and Forms
    r'^/_b_a_c_k_e_n_d/LIS/test-summary/?(\?.*)?$':'SD-P-TS',
    r'^/_b_a_c_k_e_n_d/LIS/communication_logs/?(\?.*)?$': 'SD-P-CL',
    '/_b_a_c_k_e_n_d/LIS/create_patient/': 'SD-P-PF',
    r'^/_b_a_c_k_e_n_d/LIS/create_patient/?(\?.*)?$': 'SD-P-PG',
    r'^/_b_a_c_k_e_n_d/LIS/update_patient/[^/]+/?(\?.*)?$': 'SD-P-PF',
    '/_b_a_c_k_e_n_d/LIS/latest-patient-id/': 'SD-P-LPI',
    r'^/_b_a_c_k_e_n_d/LIS/patient_list/?(\?.*)?$': 'SD-P-PF',
    r'^/_b_a_c_k_e_n_d/LIS/patient_record_dashboard/?(\?.*)?$': 'SD-P-PF',
    r'^/_b_a_c_k_e_n_d/LIS/patient_full_record(?:/[^/]+)+/?(\?.*)?$': 'SD-P-PF',
    r'^/_b_a_c_k_e_n_d/LIS/prescription_image(?:/[^/]+)+/?(\?.*)?$': 'SD-P-PF',
    r'^/_b_a_c_k_e_n_d/LIS/patient-get/?(\?.*)?$': 'SD-P-SP',
    '/_b_a_c_k_e_n_d/LIS/latest-bill-no/': 'SD-P-LBN',
    '/_b_a_c_k_e_n_d/LIS/create_bill/': 'SD-P-PB',
    '/_b_a_c_k_e_n_d/LIS/update_bill/': 'SD-P-UPB',
    r'^/_b_a_c_k_e_n_d/LIS/patients_by_date/?.*$': 'SD-P-GPD',
    '/_b_a_c_k_e_n_d/LIS/testdetails/':'SD-API-TM',
    '/_b_a_c_k_e_n_d/LIS/sample-collector/': 'SD-P-SC',
    '/_b_a_c_k_e_n_d/LIS/sales_person/': 'SD-P-GSP',
    '/_b_a_c_k_e_n_d/LIS/clinical_name/last/': 'SD-P-LRC',
    '/_b_a_c_k_e_n_d/LIS/dashboard-data/': 'SD-P-BD',
    '/_b_a_c_k_e_n_d/LIS/refby/':'SD-API-RB',
    '/_b_a_c_k_e_n_d/LIS/clinical_name/':'SD-API-CN',
    '/_b_a_c_k_e_n_d/LIS/test_details_test/':'SD-P-TE',
    '/_b_a_c_k_e_n_d/LIS/send_approval_email/':'SD-P-TE',
    r'^/_b_a_c_k_e_n_d/LIS/approve_test/?(\?.*)?$':'SD-P-TE',
    '/_b_a_c_k_e_n_d/LIS/get_devices/':'SD-API-GD',
    '/_b_a_c_k_e_n_d/LIS/appointments/':'SD-P-BA',
    r'^/_b_a_c_k_e_n_d/LIS/appointments_by_date/?(\?.*)?$':'SD-P-BA',
    r'^/_b_a_c_k_e_n_d/LIS/appointments/[^/]+/cancel/?(\?.*)?$':'SD-P-BA',
    r'^/_b_a_c_k_e_n_d/LIS/patient_report/?(\?.*)?$': 'SD-API-PR',
    r'^/_b_a_c_k_e_n_d/LIS/b2b_packages/?(\?.*)?$': 'SD-API-CN',

    #Refund and Cancellation
    r'^/_b_a_c_k_e_n_d/LIS/search_refund/?(\?.*)?$': 'SD-API-GR',
    '/_b_a_c_k_e_n_d/LIS/verify_and_process_refund/': 'SD-API-VP',
    '/_b_a_c_k_e_n_d/LIS/generate_otp_refund/': 'SD-API-GOR',
    r'^/_b_a_c_k_e_n_d/LIS/search_cancellation/?(\?.*)?$': 'SD-API-GC',
    '/_b_a_c_k_e_n_d/LIS/generate_otp_cancellation/': 'SD-API-GOC',
    '/_b_a_c_k_e_n_d/LIS/verify_and_process_cancellation/': 'SD-API-VC',
    '/_b_a_c_k_e_n_d/LIS/refund_cancellation_logs/': 'SD-API-RCL',

    #barcode:
    r'^/_b_a_c_k_e_n_d/LIS/patients_get_barcode/?(\?.*)?$': 'SD-P-BG',
    '/_b_a_c_k_e_n_d/LIS/get-max-barcode/': 'SD-P-BTD',
    r'^/_b_a_c_k_e_n_d/LIS/save-barcodes/?(\?.*)?$': 'SD-P-BTD',
    r'^/_b_a_c_k_e_n_d/LIS/get-existing-barcode/.*$': 'SD-P-BTD',
    r'^/_b_a_c_k_e_n_d/LIS/get_patientsbyb2b/?(\?.*)?$': 'SD-P-GPB',

    #sales
    '/_b_a_c_k_e_n_d/LIS/SalesVisitLog/':'SD-P-SVF',
    r'^/_b_a_c_k_e_n_d/LIS/salesdashboard/?(\?.*)?$':'SD-P-SVD',
    '/_b_a_c_k_e_n_d/LIS/hospitallabform/':'SD-P-SHF',
    '/_b_a_c_k_e_n_d/LIS/get_all_clinicalnames/':'SD-P-SGAC',
    r'^/_b_a_c_k_e_n_d/LIS/getsalesindividual/?(\?.*)?$':'SD-P-SIR',
    '/_b_a_c_k_e_n_d/LIS/clinicalname_update/':'SD-P-SCU',
    '/_b_a_c_k_e_n_d/LIS/get_sales_executives/': 'SD-P-GSP',
    r'^/_b_a_c_k_e_n_d/LIS/salesexecutive_report/?(\?.*)?$': 'SD-P-SOR',
    '/_b_a_c_k_e_n_d/LIS/get_clinicalname/':'SD-P-SCU',
    '/_b_a_c_k_e_n_d/LIS/clinical-names/':'SD-P-SCU',
    r'^/_b_a_c_k_e_n_d/LIS/clinical-names/?(\?.*)?$':'SD-P-SCU',
    r'^/_b_a_c_k_e_n_d/LIS/serve_sales_image/<str:file_id>/$':'SD-P-SCU',
    

    r'^/_b_a_c_k_e_n_d/LIS/clinical-names/[^/]+/first_approve/?(\?.*)?$':'SD-P-SCU',
    r'^/_b_a_c_k_e_n_d/LIS/clinical-names/[^/]+/final_approve/?(\?.*)?$':'SD-P-SCU',
    r'^/_b_a_c_k_e_n_d/LIS/clinical-names/[^/]+/reject/?(\?.*)?$':'SD-P-SCU',
 
    #Logistics
    r'^/_b_a_c_k_e_n_d/LIS/logistics/?(\?.*)?$': 'SD-P-LTA',
    r'^/_b_a_c_k_e_n_d/LIS/logistics_by_collector/?(\?.*)?$': 'SD-P-LBC',
    r'^/_b_a_c_k_e_n_d/LIS/logistics-dashboard/?(\?.*)?$': 'SD-P-LD',
    r'^/_b_a_c_k_e_n_d/LIS/logistics-tat-report/?(\?.*)?$': 'SD-P-LTR',
    r'^/_b_a_c_k_e_n_d/LIS/logistics/.*$': 'SD-P-LTM',
    '/_b_a_c_k_e_n_d/LIS/sample-collector-location/': 'SD-P-LSL',
    '/_b_a_c_k_e_n_d/LIS/get_b2b_employees/': 'SD-P-LGE',
    '/_b_a_c_k_e_n_d/LIS/get_b2b_lab_employees/': 'SD-P-LBL',
    r'/_b_a_c_k_e_n_d/LIS/customer_complaints/?(\?.*)?$': 'SD-P-LCC',
    r'/_b_a_c_k_e_n_d/LIS/bus_fare/?(\?.*)?$': 'SD-P-LBF',
     r'/_b_a_c_k_e_n_d/LIS/salesplan/?(\?.*)?$': 'SD-P-LSP',
    r'^/_b_a_c_k_e_n_d/LIS/sample-collector-location-history/?(\\?.*)?$': 'SD-P-LGD',

    r'^/_b_a_c_k_e_n_d/LIS/routesetup/?(\?.*)?$': 'SD-P-LTM',
    r'^/_b_a_c_k_e_n_d/LIS/route-analysis/start/?(\?.*)?$': 'SD-P-LTM',
    r'^/_b_a_c_k_e_n_d/LIS/route-analysis/end/?(\?.*)?$': 'SD-P-LTM',
    r'^/_b_a_c_k_e_n_d/LIS/route-analysis/mark/?(\?.*)?$': 'SD-P-LTM',
    r'^/_b_a_c_k_e_n_d/LIS/route-analysis/image/[^/]+/?(\?.*)?$': 'SD-P-LTM',
    r'^/_b_a_c_k_e_n_d/LIS/route-analysis/active/[^/]+/?(\?.*)?$': 'SD-P-LTM',
    r'^/_b_a_c_k_e_n_d/LIS/route-analysis/today-status/?(\?.*)?$': 'SD-P-LTM',
    r'^/_b_a_c_k_e_n_d/LIS/route-analysis/admin-report/?(\?.*)?$': 'SD-P-LTA',


    #Sample Status: 
    r'^/_b_a_c_k_e_n_d/LIS/sample_patient/?(\?.*)?$':'SD-P-SS',
    '/_b_a_c_k_e_n_d/LIS/sample_status/':'SD-P-SS',
    r'^/_b_a_c_k_e_n_d/LIS/testdetails/?(\?.*)?$':'SD-API-TM',
    r'^/_b_a_c_k_e_n_d/LIS/update_sample_status(?:/[^/]+)+/$':'SD-P-SS',
    r'^/_b_a_c_k_e_n_d/LIS/check_sample_status(?:/[^/]+)+/$':'SD-P-SS',
    r'^/_b_a_c_k_e_n_d/LIS/sample_statusupdate(?:/[^/]+)+/$':'SD-P-SS',

    #Sample Status Update:
    r'^/_b_a_c_k_e_n_d/LIS/get_sample_collected/?(\?.*)?$':'SD-P-SSU',
    r'^/_b_a_c_k_e_n_d/LIS/get_outsource_labs/?(\?.*)?$':'SD-P-SSU',
    r'^/_b_a_c_k_e_n_d/LIS/update_sample_collected(?:/[^/]+)+/$':'SD-P-SSU',

    #Franchise:
    '/_b_a_c_k_e_n_d/LIS/franchise-batches/':'SD-P-SSU',
    r'^/_b_a_c_k_e_n_d/LIS/franchise-batches/?(\?.*)?$':'SD-P-SSU',
    r'^/_b_a_c_k_e_n_d/LIS/franchise-receive(?:/[^/]+)+/$':'SD-P-SSU',
    r'^/_b_a_c_k_e_n_d/LIS/get_franchise_Transferred(?:/[^/]+)+/$':'SD-P-SSU',
    r'^/_b_a_c_k_e_n_d/LIS/update_franchise_sample(?:/[^/]+)+/$':'SD-P-SSU',
    r'^/_b_a_c_k_e_n_d/LIS/franchise_overall_report/?(\?.*)?$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/LIS/franchise_patient_test_details/?(\?.*)?$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/LIS/franchise_update_dispatch_status(?:/[^/]+)+/$':'SD-P-POV',  
    '/_b_a_c_k_e_n_d/LIS/get-test-values/':'SD-P-SSU',  

    #Franchise Reports:
    r'^/_b_a_c_k_e_n_d/LIS/franchise_overall_report/?(\?.*)?$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/LIS/get_franchise_patient_test_details/?(\?.*)?$':'SD-P-POV',

    #Test Values:
    r'^/_b_a_c_k_e_n_d/LIS/samplestatus-testvalue/?(\?.*)?$':'SD-P-PD',       
    r'^/_b_a_c_k_e_n_d/LIS/compare_test_details/?(\?.*)?$':'SD-P-TD',
    '/_b_a_c_k_e_n_d/LIS/test-value/save/':'SD-P-TD',
    r'^/_b_a_c_k_e_n_d/LIS/worklist/?(\?.*)?$':'SD-P-TD',
    

    #O/S Test Values:
    r'^/_b_a_c_k_e_n_d/LIS/os-samplestatus-testvalue/?(\?.*)?$':'SD-P-PD',       
    r'^/_b_a_c_k_e_n_d/LIS/os-compare_test_details/?(\?.*)?$':'SD-P-TD',  

    #M/B Test Values:
    r'^/_b_a_c_k_e_n_d/LIS/micro_biology_testvalue/?(\?.*)?$':'SD-P-MBPD',       
    r'^/_b_a_c_k_e_n_d/LIS/mb-compare_test_details/?(\?.*)?$':'SD-API-MBTD',  
    '/_b_a_c_k_e_n_d/LIS/mb-test-value/save/':'SD-API-MBTD',

    #M/B Test Approval:
    r'^/_b_a_c_k_e_n_d/LIS/mb-test-values/?(\?.*)?$':'SD-P-MBTV',       
    r'^/_b_a_c_k_e_n_d/LIS/mb-test-approval(?:/[^/]+)+/$':'SD-P-MBDF',
    r'^/_b_a_c_k_e_n_d/LIS/mb-test-rerun(?:/[^/]+)+/$':'SD-P-MBDF',

    #Mole/Bio Test Values:
    r'^/_b_a_c_k_e_n_d/LIS/mol_biology_testvalue/?(\?.*)?$':'SD-P-MOLPD',       
    r'^/_b_a_c_k_e_n_d/LIS/mol_biology-compare_test_details/?(\?.*)?$':'SD-P-MOLPD',  
    '/_b_a_c_k_e_n_d/LIS/mol_biology-test-value/save/':'SD-P-MOLPD',

    #Mole/Bio Test Approval:
    r'^/_b_a_c_k_e_n_d/LIS/mol_biology-test-values/?(\?.*)?$':'SD-P-MOLDF',       
    r'^/_b_a_c_k_e_n_d/LIS/mol_biology-test-approval(?:/[^/]+)+/$':'SD-P-MOLDF',
    r'^/_b_a_c_k_e_n_d/LIS/mol_biology-test-rerun(?:/[^/]+)+/$':'SD-P-MOLDF',

    #Test Approval:
    r'^/_b_a_c_k_e_n_d/LIS/test-values/?(\?.*)?$':'SD-API-TV',       
    r'^/_b_a_c_k_e_n_d/LIS/test-approved-values/?(\?.*)?$':'SD-API-TV',       
    r'^/_b_a_c_k_e_n_d/LIS/test-approval(?:/[^/]+)+/$':'SD-P-DF',
    r'^/_b_a_c_k_e_n_d/LIS/test-rerun(?:/[^/]+)+/$':'SD-P-DF',
    r'^/_b_a_c_k_e_n_d/LIS/test-edit(?:/[^/]+)+/$':'SD-P-DF',

    #Reports:
    r'^/_b_a_c_k_e_n_d/LIS/overall_report/?(\?.*)?$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/LIS/patient_test_sorting/?(\?.*)?$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/LIS/get_patient_test_details/?(\?.*)?$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/LIS/mb_patient_test_sorting/?(\?.*)?$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/LIS/mb_get_patient_test_details/?(\?.*)?$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/LIS/hms_mb_get_patient_test_details/?(\?.*)?$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/LIS/franchise_mb_get_patient_test_details/?(\?.*)?$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/LIS/mb_update_dispatch_status(?:/[^/]+)+/$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/LIS/update_dispatch_status(?:/[^/]+)+/$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/LIS/update_printed_status(?:/[^/]+)+/$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/LIS/upload-pdf/?(\?.*)?$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/LIS/credit_amount/?(\?.*)?$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/LIS/update-credit/?(\?.*)?$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/LIS/get_batch_investigation_status/?(\?.*)?$':'SD-P-CHC',
    '/_b_a_c_k_e_n_d/LIS/communication_logs/':'SD-API-MIS',
    '/_b_a_c_k_e_n_d/LIS/test-summary/':'SD-API-MIS',
    '/_b_a_c_k_e_n_d/LIS/communication_logs/':'SD-API-MIS',
    '/_b_a_c_k_e_n_d/LIS/m-dashboard-stats/':'SD-API-MIS',
    '/_b_a_c_k_e_n_d/LIS/get_rejected_samples/': 'SD-API-MIS',
    '/_b_a_c_k_e_n_d/LIS/get_outsourced_samples/': 'SD-API-MIS',
    '/_b_a_c_k_e_n_d/LIS/get_home_collection_report/': 'SD-API-MIS',

    # Invoice URLs
    '/_b_a_c_k_e_n_d/LIS/get_clinicalname_invoice/': 'SD-API-IVM',
    '/_b_a_c_k_e_n_d/LIS/update-invoice/': 'SD-API-IVM',
    '/_b_a_c_k_e_n_d/LIS/delete-invoice/': 'SD-API-IVM',
    '/_b_a_c_k_e_n_d/LIS/get-invoices/': 'SD-API-IVM',
    '/_b_a_c_k_e_n_d/LIS/generate-invoice/': 'SD-API-IVM',
    '/_b_a_c_k_e_n_d/LIS/all-patients/': 'SD-API-IVM',
    r'^/_b_a_c_k_e_n_d/LIS/corporate_credit_billing/?(\?.*)?$':'SD-API-IVM',
    r'^/_b_a_c_k_e_n_d/LIS/generate_corporate_invoice/?(\?.*)?$':'SD-API-IVM',
    r'^/_b_a_c_k_e_n_d/LIS/get_corporate_invoices/?(\?.*)?$':'SD-API-IVM',
    r'^/_b_a_c_k_e_n_d/LIS/update_corporate_invoice/?(\?.*)?$':'SD-API-IVM',
    r'^/_b_a_c_k_e_n_d/LIS/delete_corporate_invoice/?(\?.*)?$':'SD-API-IVM',
    r'^/_b_a_c_k_e_n_d/LIS/export_corporate_invoice_pdf/?(\?.*)?$':'SD-API-IVM',


    #Hms Billing
    '/_b_a_c_k_e_n_d/LIS/hms_patient_billing/':'SD-P-HMSPB',
    '/_b_a_c_k_e_n_d/LIS/hms_testdetails/':'SD-P-HMSTD',
    '/_b_a_c_k_e_n_d/LIS/hms_list_doctor/':'SD-P-HMSLD',

    #HMS barcode:
    r'^/_b_a_c_k_e_n_d/LIS/hms_patients_get_barcode/?(\?.*)?$': 'SD-P-HMSLD',
    r'^/_b_a_c_k_e_n_d/LIS/save-hms-barcodes/?(\?.*)?$': 'SD-P-HMSPB',

    # HMS Sample Status 
    r'^/_b_a_c_k_e_n_d/LIS/hms_sample_patient/?(\?.*)?$':'SD-P-HMSSP',
    '/_b_a_c_k_e_n_d/LIS/hms_sample_status/': 'SD-P-HMSSS',
    r'^/_b_a_c_k_e_n_d/LIS/hms_check_sample_status(?:/[^/]+)+/$': 'SD-P-HMSCS',
    r'^/_b_a_c_k_e_n_d/LIS/hms_sample_status_data(?:/[^/]+)+/$': 'SD-P-HMSSD',
    r'^/_b_a_c_k_e_n_d/LIS/hms_patch_sample_status(?:/[^/]+)+/$': 'SD-P-HMSPS',
    r'^/_b_a_c_k_e_n_d/LIS/hms_get_sample_collected/?(\?.*)?$':'SD-P-HMSGC',
    r'^/_b_a_c_k_e_n_d/LIS/hms_update_sample_collected(?:/[^/]+)+/$':'SD-P-HMSUC',

    #HMS Reports:
    r'^/_b_a_c_k_e_n_d/LIS/hms_overall_report/?(\?.*)?$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/LIS/hms_patient_test_sorting/?(\?.*)?$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/LIS/get_hms_patient_test_details/?(\?.*)?$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/LIS/hms_update_dispatch_status(?:/[^/]+)+/$':'SD-P-POV',
    
    #Corporate:
    '/_b_a_c_k_e_n_d/LIS/corporate-batches/':'SD-P-SSU',
    r'^/_b_a_c_k_e_n_d/LIS/corporate-batches/?(\?.*)?$':'SD-P-SSU',
    r'^/_b_a_c_k_e_n_d/LIS/corporate-receive(?:/[^/]+)+/$':'SD-P-SSU',
    r'^/_b_a_c_k_e_n_d/LIS/get_corporate_Transferred(?:/[^/]+)+/$':'SD-P-SSU',
    r'^/_b_a_c_k_e_n_d/LIS/update_corporate_sample(?:/[^/]+)+/$':'SD-P-SSU',
    r'^/_b_a_c_k_e_n_d/LIS/corporate_overall_report/?(\?.*)?$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/LIS/corporate_patient_test_details/?(\?.*)?$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/LIS/get-corporate-test-values(?:/[^/]+)+/$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/LIS/corporate_approval_report/?(\?.*)?$':'SD-P-CHC',
    r'^/_b_a_c_k_e_n_d/LIS/corporate_health_report/?(\?.*)?$':'SD-P-CHC',
    r'^/_b_a_c_k_e_n_d/LIS/get_investigation_file/?(\?.*)?$':'SD-P-CHC',
    r'^/_b_a_c_k_e_n_d/LIS/get_investigation_status/?(\?.*)?$':'SD-P-CHC',
    r'^/_b_a_c_k_e_n_d/LIS/get_batch_investigation_status/?(\?.*)?$':'SD-P-CHC',
    r'^/_b_a_c_k_e_n_d/LIS/get_batch_corporate_health_reports/?(\?.*)?$':'SD-P-CHC',
    r'^/_b_a_c_k_e_n_d/LIS/save_overall_approval/?(\?.*)?$':'SD-P-CHC',

    #MIS
    r'^/_b_a_c_k_e_n_d/LIS/consolidated-data/?(\?.*)?$':'SD-P-MIS',
    r'^/_b_a_c_k_e_n_d/LIS/hms-consolidated-data/?(\?.*)?$':'SD-P-MIS',
    r'^/_b_a_c_k_e_n_d/LIS/franchise-consolidated-data/?(\?.*)?$':'SD-P-MIS',
    r'^/_b_a_c_k_e_n_d/LIS/hms-test-count/?(\?.*)?$':'SD-P-MIS',


    r'^/_b_a_c_k_e_n_d/LIS/clinical_hospital_report/?(\?.*)?$':'SD-P-CR',
    r'^/_b_a_c_k_e_n_d/LIS/clinical_billing_dashboard/?(\?.*)?$':'SD-P-CD',
    r'^/_b_a_c_k_e_n_d/LIS/get_clinicalpatient_test_details/?(\?.*)?$':'SD-P-CPT',
    r'^/_b_a_c_k_e_n_d/LIS/clinical_hospital_ledger/?(\?.*)?$':'SD-P-CHL',

}

PAGE_ACTION_MAPPING = {
    'GL-P-EPM': {
        'DELETE':'RWD',
    },
}

GEN_ACTION_MAPPING = {
    'POST': 'RW',
    'PUT': 'RW',
    'DELETE': 'RW',
    'GET': 'R',
    'PATCH': 'RW',
    'OPTIONS': 'RW',
}




