PAGE_MAPPING = {

    #Registration, Billing and Forms
    r'^/_b_a_c_k_e_n_d/LIS/test-summary/?(\?.*)?$':'SD-P-TS',
    r'^/_b_a_c_k_e_n_d/LIS/communication_logs/?(\?.*)?$': 'SD-P-CL',
    '/_b_a_c_k_e_n_d/LIS/create_patient/': 'SD-P-PF',
    r'^/_b_a_c_k_e_n_d/LIS/create_patient/?(\?.*)?$': 'SD-P-PG',
    '/_b_a_c_k_e_n_d/LIS/latest-patient-id/': 'SD-P-LPI',
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
    '/_b_a_c_k_e_n_d/LIS/get_devices/':'SD-API-GD',
    '/_b_a_c_k_e_n_d/LIS/appointments/':'SD-P-BA',
    r'^/_b_a_c_k_e_n_d/LIS/patient_report/?(\?.*)?$': 'SD-API-PR',


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
    '/_b_a_c_k_e_n_d/LIS/hospitallabform/':'SD-P-SHF',
    '/_b_a_c_k_e_n_d/LIS/get_all_clinicalnames/':'SD-P-SGAC',
    '/_b_a_c_k_e_n_d/LIS/get_sales_individual_report/':'SD-P-SIR',
    '/_b_a_c_k_e_n_d/LIS/clinicalname_update/':'SD-P-SCU',
    '/_b_a_c_k_e_n_d/LIS/get_sales_executives/': 'SD-P-GSP',
    '/_b_a_c_k_e_n_d/LIS/salesexecutive_report/': 'SD-P-SIR',
    r'^/_b_a_c_k_e_n_d/LIS/salesexecutive_report/?(\?.*)?$': 'SD-P-SIR',

    #Logistics
    '/_b_a_c_k_e_n_d/LIS/get_sample_collectors/':'SD-P-LGSC',
    '/_b_a_c_k_e_n_d/LIS/get_logistic_data/':'SD-P-LGLD',
    '/_b_a_c_k_e_n_d/LIS/save_logistic_data/':'SD-P-LSD',
    '/_b_a_c_k_e_n_d/LIS/sample_collector_location/':'SD-P-LSCL',
    '/_b_a_c_k_e_n_d/LIS/savesamplecollector/':'SD-P-LSC',
    '/_b_a_c_k_e_n_d/LIS/updatesamplecollectordetails/':'SD-P-LUSCD',
    r'^/_b_a_c_k_e_n_d/LIS/get_logistic_task/?(\?.*)?$':'SD-P-LGLT',
    '/_b_a_c_k_e_n_d/LIS/logisticdashboard/':'SD-P-LGD',


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

    #O/S Test Values:
    r'^/_b_a_c_k_e_n_d/LIS/os-samplestatus-testvalue/?(\?.*)?$':'SD-P-PD',       
    r'^/_b_a_c_k_e_n_d/LIS/os-compare_test_details/?(\?.*)?$':'SD-P-TD',  
    '/_b_a_c_k_e_n_d/LIS/os-test-value/save/':'SD-P-TD',

    #Test Approval:
    r'^/_b_a_c_k_e_n_d/LIS/test-values/?(\?.*)?$':'SD-API-TV',       
    r'^/_b_a_c_k_e_n_d/LIS/test-approval(?:/[^/]+)+/$':'SD-P-DF',
    r'^/_b_a_c_k_e_n_d/LIS/test-rerun(?:/[^/]+)+/$':'SD-P-DF',

    #Reports:
    r'^/_b_a_c_k_e_n_d/LIS/overall_report/?(\?.*)?$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/LIS/patient_test_sorting/?(\?.*)?$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/LIS/get_patient_test_details/?(\?.*)?$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/LIS/update_dispatch_status(?:/[^/]+)+/$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/LIS/upload-pdf/?(\?.*)?$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/LIS/credit_amount/?(\?.*)?$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/LIS/update-credit/?(\?.*)?$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/LIS/get_batch_investigation_status/?(\?.*)?$':'SD-P-CHC',
    '/_b_a_c_k_e_n_d/LIS/communication_logs/':'SD-API-MIS',
    '/_b_a_c_k_e_n_d/LIS/test-summary/':'SD-API-MIS',
    '/_b_a_c_k_e_n_d/LIS/communication_logs/':'SD-API-MIS',
    
    '/_b_a_c_k_e_n_d/LIS/get_rejected_samples/': 'SD-API-MIS',
    '/_b_a_c_k_e_n_d/LIS/get_outsourced_samples/': 'SD-API-MIS',
    '/_b_a_c_k_e_n_d/LIS/get_home_collection_report/': 'SD-API-MIS',


    r'^/_b_a_c_k_e_n_d/LIS/preetham_hospital_report/?(\?.*)?$':'SD-P-PHR',

    # Invoice URLs
    '/_b_a_c_k_e_n_d/LIS/get_clinicalname_invoice/': 'SD-API-IVM',
    '/_b_a_c_k_e_n_d/LIS/update-invoice/': 'SD-API-IVM',
    '/_b_a_c_k_e_n_d/LIS/delete-invoice/': 'SD-API-IVM',
    '/_b_a_c_k_e_n_d/LIS/get-invoices/': 'SD-API-IVM',
    '/_b_a_c_k_e_n_d/LIS/generate-invoice/': 'SD-API-IVM',
    '/_b_a_c_k_e_n_d/LIS/all-patients/': 'SD-API-IVM',

    #Hms Billing
    '/_b_a_c_k_e_n_d/LIS/hms_patient_billing/':'SD-P-HMSPB',
    '/_b_a_c_k_e_n_d/LIS/hms_testdetails/':'SD-P-HMSTD',
    '/_b_a_c_k_e_n_d/LIS/hms-list_doctor/':'SD-P-HMSLD',

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

