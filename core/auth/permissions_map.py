PAGE_MAPPING = {

    '/_b_a_c_k_e_n_d/Diagnostics/create_patient/': 'SD-P-PF',
    r'^/_b_a_c_k_e_n_d/Diagnostics/create_patient/?(\?.*)?$': 'SD-P-PG',
    '/_b_a_c_k_e_n_d/Diagnostics/latest-patient-id/': 'SD-P-LPI',
    r'^/_b_a_c_k_e_n_d/Diagnostics/patient-get/?(\?.*)?$': 'SD-P-SP',
    '/_b_a_c_k_e_n_d/Diagnostics/latest-bill-no/': 'SD-P-LBN',
    '/_b_a_c_k_e_n_d/Diagnostics/create_bill/': 'SD-P-PB',
    '/_b_a_c_k_e_n_d/Diagnostics/update_bill/': 'SD-P-UPB',
    r'^/_b_a_c_k_e_n_d/Diagnostics/patients_by_date/?(\?.*)?$': 'SD-P-GPD',
    '/_b_a_c_k_e_n_d/Diagnostics/testdetails/':'SD-P-TM',
    '/_b_a_c_k_e_n_d/Diagnostics/sample-collector/': 'SD-P-SC',

    '/_b_a_c_k_e_n_d/Diagnostics/refby/':'SD-API-RB',
    '/_b_a_c_k_e_n_d/Diagnostics/clinical_name/':'SD-API-CN',


    #barcode:
    r'^/_b_a_c_k_e_n_d/Diagnostics/patients_get_barcode/?(\?.*)?$': 'SD-P-BG',
    '/_b_a_c_k_e_n_d/Diagnostics/get-max-barcode/': 'SD-P-BTD',
    r'^/_b_a_c_k_e_n_d/Diagnostics/save-barcodes/?(\?.*)?$': 'SD-P-BTD',
    r'^/_b_a_c_k_e_n_d/Diagnostics/get-existing-barcode/.*$': 'SD-P-BTD',
    r'^/_b_a_c_k_e_n_d/Diagnostics/patients_get_barcode/?(\?.*)?$':'SD-P-BG',
    r'^/_b_a_c_k_e_n_d/Diagnostics/get_patientsbyb2b/?(\?.*)?$': 'SD-P-GPB',


    #Sample Status: 
    r'^/_b_a_c_k_e_n_d/Diagnostics/sample_patient/?(\?.*)?$':'SD-P-SS',
    '/_b_a_c_k_e_n_d/Diagnostics/sample_status/':'SD-P-SS',
    r'^/_b_a_c_k_e_n_d/Diagnostics/test_details/?(\?.*)?$':'SD-API-TD',
    r'^/_b_a_c_k_e_n_d/Diagnostics/update_sample_status(?:/[^/]+)+/$':'SD-P-SS',
    r'^/_b_a_c_k_e_n_d/Diagnostics/check_sample_status(?:/[^/]+)+/$':'SD-P-SS',
    r'^/_b_a_c_k_e_n_d/Diagnostics/sample_statusupdate(?:/[^/]+)+/$':'SD-P-SS',

    #Sample Status Update:
    r'^/_b_a_c_k_e_n_d/Diagnostics/get_sample_collected/?(\?.*)?$':'SD-P-SSU',
    r'^/_b_a_c_k_e_n_d/Diagnostics/update_sample_collected(?:/[^/]+)+/$':'SD-P-SSU',

    #Franchise:
    '/_b_a_c_k_e_n_d/Diagnostics/franchise-batches/':'SD-P-SSU',
    r'^/_b_a_c_k_e_n_d/Diagnostics/franchise-batches/?(\?.*)?$':'SD-P-SSU',
    r'^/_b_a_c_k_e_n_d/Diagnostics/franchise-receive(?:/[^/]+)+/$':'SD-P-SSU',
    r'^/_b_a_c_k_e_n_d/Diagnostics/get_franchise_Transferred(?:/[^/]+)+/$':'SD-P-SSU',
    r'^/_b_a_c_k_e_n_d/Diagnostics/update_franchise_sample(?:/[^/]+)+/$':'SD-P-SSU',
    r'^/_b_a_c_k_e_n_d/Diagnostics/franchise_overall_report/?(\?.*)?$':'SD-API-OAR',
    r'^/_b_a_c_k_e_n_d/Diagnostics/franchise_patient_test_details/?(\?.*)?$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/Diagnostics/franchise_update_dispatch_status(?:/[^/]+)+/$':'SD-P-POV',  
    '/_b_a_c_k_e_n_d/Diagnostics/get-test-values/':'SD-P-SSU',  

    #Test Values:
    r'^/_b_a_c_k_e_n_d/Diagnostics/samplestatus-testvalue/?(\?.*)?$':'SD-P-PD',       
    r'^/_b_a_c_k_e_n_d/Diagnostics/compare_test_details/?(\?.*)?$':'SD-P-TD',
    r'^/_b_a_c_k_e_n_d/Diagnostics/hmssamplestatus-testvalue/?(\?.*)?$':'SD-P-PD',       
    r'^/_b_a_c_k_e_n_d/Diagnostics/hmscompare_test_details/?(\?.*)?$':'SD-P-TD',
    '/_b_a_c_k_e_n_d/Diagnostics/test-value/save/':'SD-P-TD',

    #Test Approval:
    r'^/_b_a_c_k_e_n_d/Diagnostics/test-values/?(\?.*)?$':'SD-API-TV',       
    r'^/_b_a_c_k_e_n_d/Diagnostics/test-approval(?:/[^/]+)+/$':'SD-P-DF',
    r'^/_b_a_c_k_e_n_d/Diagnostics/test-rerun(?:/[^/]+)+/$':'SD-P-DF',

    #Reports:

    r'^/_b_a_c_k_e_n_d/Diagnostics/overall_report/?(\?.*)?$':'SD-API-OAR',
    r'^/_b_a_c_k_e_n_d/Diagnostics/patient_test_sorting/?(\?.*)?$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/Diagnostics/get_patient_test_details/?(\?.*)?$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/Diagnostics/update_dispatch_status(?:/[^/]+)+/$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/Diagnostics/upload-pdf/?(\?.*)?$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/Diagnostics/credit_amount/?(\?.*)?$':'SD-P-POV',
    r'^/_b_a_c_k_e_n_d/Diagnostics/update-credit/?(\?.*)?$':'SD-P-POV',

    # Invoice URLs
    r'/_b_a_c_k_e_n_d/Diagnostics/get_clinicalname_invoice/': 'SD-API-IVM',
    '/_b_a_c_k_e_n_d/Diagnostics/update-invoice/.*': 'SD-API-IVM',
    '/_b_a_c_k_e_n_d/Diagnostics/delete-invoice/.*': 'SD-API-IVM',
    r'^/_b_a_c_k_e_n_d/Diagnostics/get-invoices/?(\?.*)?$': 'SD-API-IVM',
    r'^/_b_a_c_k_e_n_d/Diagnostics/generate-invoice/?(\?.*)?$': 'SD-API-IVM',
    r'^/_b_a_c_k_e_n_d/Diagnostics/all-patients/?(\?.*)?$': 'SD-API-IVM',
    r'^/_b_a_c_k_e_n_d/Diagnostics/patient_report/?(\?.*)?$': 'SD-API-IVM',

    #Hms Billing
    '/_b_a_c_k_e_n_d/Diagnostics/hms_patient_billing/':'SD-P-HMSPB',
    '/_b_a_c_k_e_n_d/Diagnostics/hms_testdetails/':'SD-P-HMSTD',
    '/_b_a_c_k_e_n_d/Diagnostics/hms-list_doctor/':'SD-P-HMSLD',

    #HMS barcode:
    r'^/_b_a_c_k_e_n_d/Diagnostics/hms_patients_get_barcode/?(\?.*)?$': 'SD-P-HMSLD',
    r'^/_b_a_c_k_e_n_d/Diagnostics/save-hms-barcodes/?(\?.*)?$': 'SD-P-HMSPB',

    # HMS Sample Status 
    r'^/_b_a_c_k_e_n_d/Diagnostics/hms_sample_patient/?(\?.*)?$':'SD-P-HMSSP',
    '/_b_a_c_k_e_n_d/Diagnostics/hms_sample_status/': 'SD-P-HMSSS',
    r'^/_b_a_c_k_e_n_d/Diagnostics/hms_check_sample_status(?:/[^/]+)+/$': 'SD-P-HMSCS',
    r'^/_b_a_c_k_e_n_d/Diagnostics/hms_sample_status_data(?:/[^/]+)+/$': 'SD-P-HMSSD',
    r'^/_b_a_c_k_e_n_d/Diagnostics/hms_patch_sample_status(?:/[^/]+)+/$': 'SD-P-HMSPS',


    r'^/_b_a_c_k_e_n_d/Diagnostics/hms_get_sample_collected/?(\?.*)?$':'SD-P-HMSGC',
    r'^/_b_a_c_k_e_n_d/Diagnostics/hms_update_sample_collected(?:/[^/]+)+/$':'SD-P-HMSUC',


    #MIS
    r'^/_b_a_c_k_e_n_d/Diagnostics/consolidated-data/?(\?.*)?$':'SD-P-MIS',
    r'^/_b_a_c_k_e_n_d/Diagnostics/hms-consolidated-data/?(\?.*)?$':'SD-P-MIS',
    r'^/_b_a_c_k_e_n_d/Diagnostics/franchise-consolidated-data/?(\?.*)?$':'SD-P-MIS',
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
}

