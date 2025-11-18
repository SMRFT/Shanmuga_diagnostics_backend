PAGE_MAPPING = {

    '/_b_a_c_k_e_n_d/LIS/create_patient/': 'SD-P-PF',
    r'^/_b_a_c_k_e_n_d/LIS/create_patient/?(\?.*)?$': 'SD-P-PG',
    '/_b_a_c_k_e_n_d/LIS/latest-patient-id/': 'SD-P-LPI',
    r'^/_b_a_c_k_e_n_d/LIS/patient-get/?(\?.*)?$': 'SD-P-SP',
    '/_b_a_c_k_e_n_d/LIS/latest-bill-no/': 'SD-P-LBN',
    '/_b_a_c_k_e_n_d/LIS/create_bill/': 'SD-P-PB',
    '/_b_a_c_k_e_n_d/LIS/update_bill/': 'SD-P-UPB',
    r'^/_b_a_c_k_e_n_d/LIS/patients_by_date/?(\?.*)?$': 'SD-P-GPD',
    '/_b_a_c_k_e_n_d/LIS/testdetails/':'SD-P-TM',
    '/_b_a_c_k_e_n_d/LIS/sample-collector/': 'SD-P-SC',

    '/_b_a_c_k_e_n_d/LIS/refby/':'SD-API-RB',
    '/_b_a_c_k_e_n_d/LIS/clinical_name/':'SD-API-CN',

    #barcode:
    r'^/_b_a_c_k_e_n_d/LIS/patients_get_barcode/?(\?.*)?$': 'SD-P-BG',
    '/_b_a_c_k_e_n_d/LIS/get-max-barcode/': 'SD-P-BTD',
    r'^/_b_a_c_k_e_n_d/LIS/save-barcodes/?(\?.*)?$': 'SD-P-BTD',
    r'^/_b_a_c_k_e_n_d/LIS/get-existing-barcode/.*$': 'SD-P-BTD',
    r'^/_b_a_c_k_e_n_d/LIS/patients_get_barcode/?(\?.*)?$':'SD-P-BG',
    r'^/_b_a_c_k_e_n_d/LIS/get_patientsbyb2b/?(\?.*)?$': 'SD-P-GPB',


    #Sample Status: 
    r'^/_b_a_c_k_e_n_d/LIS/sample_patient/?(\?.*)?$':'SD-P-SS',
    '/_b_a_c_k_e_n_d/LIS/sample_status/':'SD-P-SS',
    r'^/_b_a_c_k_e_n_d/LIS/test_details/?(\?.*)?$':'SD-API-TD',
    r'^/_b_a_c_k_e_n_d/LIS/update_sample_status(?:/[^/]+)+/$':'SD-P-SS',
    r'^/_b_a_c_k_e_n_d/LIS/check_sample_status(?:/[^/]+)+/$':'SD-P-SS',
    r'^/_b_a_c_k_e_n_d/LIS/sample_statusupdate(?:/[^/]+)+/$':'SD-P-SS',

    #Sample Status Update:
    r'^/_b_a_c_k_e_n_d/LIS/get_sample_collected/?(\?.*)?$':'SD-P-SSU',
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

    #Test Values:
    r'^/_b_a_c_k_e_n_d/LIS/samplestatus-testvalue/?(\?.*)?$':'SD-P-PD',       
    r'^/_b_a_c_k_e_n_d/LIS/compare_test_details/?(\?.*)?$':'SD-P-TD',
    r'^/_b_a_c_k_e_n_d/LIS/hmssamplestatus-testvalue/?(\?.*)?$':'SD-P-PD',       
    r'^/_b_a_c_k_e_n_d/LIS/hmscompare_test_details/?(\?.*)?$':'SD-P-TD',
    '/_b_a_c_k_e_n_d/LIS/test-value/save/':'SD-P-TD',

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

    # Invoice URLs
    r'/_b_a_c_k_e_n_d/LIS/get_clinicalname_invoice/': 'SD-API-IVM',
    '/_b_a_c_k_e_n_d/LIS/update-invoice/.*': 'SD-API-IVM',
    '/_b_a_c_k_e_n_d/LIS/delete-invoice/.*': 'SD-API-IVM',
    r'^/_b_a_c_k_e_n_d/LIS/get-invoices/?(\?.*)?$': 'SD-API-IVM',
    r'^/_b_a_c_k_e_n_d/LIS/generate-invoice/?(\?.*)?$': 'SD-API-IVM',
    r'^/_b_a_c_k_e_n_d/LIS/all-patients/?(\?.*)?$': 'SD-API-IVM',
    r'^/_b_a_c_k_e_n_d/LIS/patient_report/?(\?.*)?$': 'SD-API-IVM',

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
    r'^/_b_a_c_k_e_n_d/LIS/save_overall_approval/?(\?.*)?$':'SD-P-CHC',

    r'^/_b_a_c_k_e_n_d/LIS/hms_get_sample_collected/?(\?.*)?$':'SD-P-HMSGC',
    r'^/_b_a_c_k_e_n_d/LIS/hms_update_sample_collected(?:/[^/]+)+/$':'SD-P-HMSUC',


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
}




# {
#   "_id": {
#     "$oid": "67dd1816e0630bf9c06b0fc3"
#   },
#   "role_code": "SD-R-DOC",
#   "role_name": "Doctor",
#   "role_description": "Doctor",
#   "permissions": {
#     "allowed": [
#       "SD-P-RA-RW",
#       "SD-P-RD-RW",
#       "SD-API-TV-R",
#       "SD-P-DF-R",
#       "SD-P-DF-RW",
#       "SD-API-OAR-R",
#       "SD-P-POV-R",
#       "SD-P-POV-RW",
#       "SD-P-MIS-R",
#       "SD-R-DOC",
#       "SD-P-PF-RW",
#       "SD-P-PG-RW",
#       "SD-API-CN-R",
#       "SD-API-RB-R",
#       "SD-P-RB-RW",
#       "SD-P-SC-R",
#       "SD-P-LPI-R",
#       "SD-P-LBN-R",
#       "SD-P-TM-R",
#       "SD-P-SP-R",
#       "SD-P-PB-RW",
#       "SD-P-GPD-R",
#       "SD-P-UPB-RW",
#       "SD-P-BG-RW",
#       "SD-P-SS-R",
#       "SD-P-SS-RW",
#       "SD-P-GPB-R",
#       "SD-API-BTD-RW",
#       "SD-API-SS-RW",
#       "SD-API-TD-R",
#             "SD-P-SA-RW",
#       "SD-P-RG-RW",
#       "SD-P-TDE-RW",
#       "SD-P-RD-RW",
#       "SD-P-HMSPB-RW",
#       "SD-P-HMSTD-R",
#       "SD-P-HMSBD-RW",
#       "SD-P-HMSGP-R",
#       "SD-P-HMSLD-R",
#       "SD-P-HMSSP-R",
#       "SD-P-HMSSS-RW",
#       "SD-P-HMSCS-R",
#       "SD-P-HMSSD-R",
#       "SD-P-HMSPS-RW",
#       "SD-P-HMSGC-R",
#       "SD-P-HMSUC-RW",
#       "SD-P-TE-RW",
#             "SD-P-LBN-R",
#       "SD-P-TM-R",
#       "SD-P-GPB-R",
#       "SD-P-GPD-R",
#       "SD-P-UPB-RW",
#       "SD-P-HMSPB-RW",
#       "SD-API-HMSTD-R",
#       "SD-P-HMSLD-R",
#       "SD-P-HMSGP-R",
#       "SD-P-HMSSP-R",
#       "SD-P-HMSSS-RW",
#       "SD-P-HMSCS-R",
#       "SD-P-HMSSD-R",
#       "SD-P-HMSPS-RW",
#     "SD-P-BG-R",
#       "SD-P-BTD-R",
#       "SD-P-BTD-RW",
#       "SD-API-TD-R",
#       "SD-P-SSU-R",
#       "SD-P-SSU-RW",
#       "SD-P-SS-R",
#       "SD-P-SS-RW",
#       "SD-P-PD-R",
#       "SD-API-TV-R",
#       "SD-P-TD-R",
#       "SD-P-TD-RW",
#       "SD-P-DF-R",
#       "SD-P-DF-RW",
#       "SD-P-PL-R",
#       "SD-API-RB-R",
#       "SD-API-CN-R",
#       "SD-P-POV-R",
#       "SD-P-POV-RW",
#       "SD-P-GPD-R",
#       "SD-P-MIS-R",
#       "SD-P-CHC-R",
#       "SD-P-CHC-RW",
#     ]
#   },
#   "is_active": true,
#   "created_by": "system",
#   "created_date": {
#     "$date": "2025-11-02T18:30:00.000Z"
#   },
#   "last_modified_by": "system",
#   "last_modified_date": {
#     "$date": "2025-11-02T18:30:00.000Z"
#   }
# }




# {
#   "_id": {
#     "$oid": "67dd1816e0630bf9c06b0fc3"
#   },
#   "role_code": "SD-R-DOC",
#   "role_name": "Doctor",
#   "role_description": "Doctor",
#   "permissions": {
#     "allowed": [
#       "SD-P-BG-R",
#       "SD-P-BTD-R",
#       "SD-P-BTD-RW",
#       "SD-API-TD-R",
#       "SD-P-SSU-R",
#       "SD-P-SSU-RW",
#       "SD-P-SS-R",
#       "SD-P-SS-RW",
#       "SD-P-PD-R",
#       "SD-API-TV-R",
#       "SD-P-TD-R",
#       "SD-P-TD-RW",
#       "SD-P-DF-R",
#       "SD-P-DF-RW",
#       "SD-P-PL-R",
#       "SD-API-RB-R",
#       "SD-API-CN-R",
#       "SD-P-POV-R",
#       "SD-P-POV-RW",
#       "SD-P-GPD-R",
#       "SD-P-MIS-R",
#       "SD-P-CHC-R",
#       "SD-P-CHC-RW",
#       "SD-R-CEO"
#     ]
#   },
#   "is_active": true,
#   "created_by": "system",
#   "created_date": {
#     "$date": "2025-11-02T18:30:00.000Z"
#   },
#   "last_modified_by": "system",
#   "last_modified_date": {
#     "$date": "2025-11-02T18:30:00.000Z"
#   }
# }



# {
#   "_id": {
#     "$oid": "68ef133dce6c5346ade4e8da"
#   },
#   "role_code": "SD-R-CEO",
#   "role_name": "CEO",
#   "role_description": "CEO",
#   "permissions": {
#     "allowed": [
#       "SD-P-BG-R",
#       "SD-P-BTD-R",
#       "SD-P-BTD-RW",
#       "SD-API-TD-R",
#       "SD-P-SSU-R",
#       "SD-P-SSU-RW",
#       "SD-P-SS-R",
#       "SD-P-SS-RW",
#       "SD-P-PD-R",
#       "SD-API-TV-R",
#       "SD-P-TD-R",
#       "SD-P-TD-RW",
#       "SD-P-DF-R",
#       "SD-P-DF-RW",
#       "SD-P-PL-R",
#       "SD-API-RB-R",
#       "SD-API-CN-R",
#       "SD-P-POV-R",
#       "SD-P-POV-RW",
#       "SD-P-GPD-R",
#       "SD-P-MIS-R",
#       "SD-P-CHC-R",
#       "SD-P-CHC-RW",
#       "SD-R-CEO"
#     ]
#   },
#   "is_active": true,
#   "created_by": "system",
#   "created_date": {
#     "$date": "2025-11-02T18:30:00.000Z"
#   },
#   "last_modified_by": "system",
#   "last_modified_date": {
#     "$date": "2025-11-02T18:30:00.000Z"
#   }
# }