#urls.py
from django.urls import path
from core import views
from .Views.hms import hmsbarcode,hmsbilling,hmsreport,hmssamplestatus,hmstestvalue
from .Views import whatsapp,franchise,sales,mis,dashboard
from .Views import patients,clinicalname,form,testdetails,barcode,sample,testvalue,testapproval,report
from core.Views.invoice import generate_invoice,get_invoices,delete_invoice,update_invoice,get_clinicalname_invoice,get_all_patients,patient_report
from core.Views.refundandcancellation import search_cancellation,verify_and_process_refund,search_refund,verify_and_process_cancellation,generate_otp_cancellation,generate_otp_refund,logs_api,dashboard_data


urlpatterns = [
    path('create_patient/', patients.create_patient, name='create_patient'),
    path('create_patient/<str:patient_id>/', patients.create_patient, name='create_patient'),
    path('latest-patient-id/', patients.get_latest_patient_id, name='get_latest_patient_id'),
    path('patient-get/', patients.patient_get, name='patient_get'),
    path('create_bill/', patients.create_bill, name='create_bill'),
    path('update_bill/', patients.update_bill, name='update_bill'),
    path('latest-bill-no/', patients.get_latest_bill_no, name='get_latest_bill_no'),
    path('patients_by_date/', patients.get_patients_by_date, name='get_patients_by_date'),
    path('testdetails/', testdetails.get_test_details, name='create_sample_collector'),
    path('clinical_name/', clinicalname.clinical_name, name='create_organisation'),
    path('sample-collector/', form.sample_collector, name='create_sample_collector'),
    path('refby/', form.refby, name='refby'),


    path('sample_patient/', sample.get_samplepatients_by_date, name='get_samplepatients_by_date'),       
    path('sample_status/', sample.sample_status, name='sample_status'),
    path('test_details/', views.get_test_details, name='get_test_details'),   
    path('check_sample_status/<str:patient_id>/', sample.check_sample_status, name='check_sample_status'),
    path('sample_statusupdate/<str:patient_id>/', sample.patch_sample_status, name='patch_sample_status'),


    path("get_sample_collected/", sample.get_sample_collected, name="get_sample_collected"),
    path("update_sample_collected/<str:patient_id>/", sample.update_sample_collected, name="update_sample_collected"),  

    #Barcode:
    path('patients_get_barcode/', barcode.get_barcode_by_date, name='get_barcode_by_date'),
    path('get-max-barcode/', barcode.get_max_barcode, name='get_max_barcode'),
    path('save-barcodes/', barcode.save_barcodes, name='save_barcodes'),
    path('get-existing-barcode/',barcode.get_existing_barcode, name='get_latest_bill_no'),
    #Test Values:
    path('samplestatus-testvalue/', testvalue.get_samplestatus_testvalue, name='sample-status-list'), 
    path('compare_test_details/', testvalue.compare_test_details, name='compare_test_details'),
    path('test-value/save/', testvalue.save_test_value, name='save_test_value'),    
    #Test Approval:
    path('test-values/', testapproval.get_test_values, name='get_test_values'),
    path("test-approval/<path:patient_id>/<int:test_index>/approve/",testapproval.approve_test_detail,name="approve_test_detail"),
    path('test-rerun/<str:patient_id>/<int:test_index>/rerun/', testapproval.rerun_test_detail, name='rerun_test_detail'),
    path('patient_test_sorting/', report.patient_test_sorting, name='patient_test_sorting'),
    path('get_patient_test_details/', report.get_patient_test_details, name='get_patient_test_details'),
    #Invoice URLs
    path("generate-invoice/", generate_invoice, name="generate-invoice"),
    path("get-invoices/", get_invoices, name="get-invoices"),
    path("update-invoice/<str:invoice_number>/", update_invoice, name="update-invoice"),
    path("delete-invoice/<str:invoice_id>/", delete_invoice, name="delete-invoice"),
    path('get_clinicalname_invoice/', get_clinicalname_invoice, name='get_clinicalname_by_referrer'),
    path('all-patients/', get_all_patients, name='get_all_patients'),
    path('patient_report/', patient_report, name='patient_report'),

    path('overall_report/', report.overall_report, name='overall_report'),

    # Refund and Cancellation URLs
    path('search_refund/', search_refund, name='search_refund'),
    path('verify_and_process_refund/', verify_and_process_refund, name='verify_and_process_refund'),
    path('search_cancellation/', search_cancellation, name='search_cancellation'),
    path('search_refund/', search_refund, name='search_refund'),
    path('verify_and_process_refund/', verify_and_process_refund, name='verify_and_process_refund'),
    path('generate_otp_refund/', generate_otp_refund, name='generate_otp_refund'),
    path('generate_otp_cancellation/', generate_otp_cancellation, name='generate_otp_cancellation'),
    path('search_cancellation/', search_cancellation, name='search_cancellation'),
    path('verify_and_process_cancellation/',verify_and_process_cancellation, name='verify_and_process_cancellation'),
    path('refund_cancellation_logs/', logs_api, name='refund_cancellation_logs'),
    path('patient-get/', patients.patient_get, name='patient_get'),
    path("upload-pdf/", whatsapp.upload_pdf_to_gridfs, name="upload_pdf"),
    path("get-file/<str:file_id>/", whatsapp.get_pdf_from_gridfs, name="get_pdf"),
    path("send-whatsapp/", whatsapp.send_whatsapp, name="send_whatsapp"),
    path('get_patientsbyb2b/', patients.get_patientsbyb2b, name='get_patients'),
    path('patient_overview/', patients.patient_overview, name='patient_overview'),
    path('send-email/', whatsapp.send_email, name='send_email'),


    path('hospitallabform/', sales.hospitallabform, name='hospitallabform'),
    path('get_all_clinicalnames/',sales.get_all_clinicalnames, name='get_all_clinicalnames'),
    path('SalesVisitLog/', sales.salesvisitlog, name='salesvisitlog'),

    path('update_dispatch_status/<str:barcode>/', report.update_dispatch_status, name='update_dispatch_status'),
    #Franchise Batch and Sample Status Update:
    path('franchise-batches/', franchise.get_batch_generation_data, name='get_batch_generation_data'),
    path('franchise-receive/<str:batch_no>/', franchise.update_batch_received_status, name='update_batch_received_status'),
    path("get_franchise_Transferred/<str:batch_number>/", franchise.get_franchise_sample, name="get_franchise_sample"),
    path("update_franchise_sample/<str:barcode>/", franchise.update_franchise_sample, name="update_franchise_sample"), 
    
    #Franchise Reports:     
    path('franchise_overall_report/', franchise.franchise_overall_report, name='franchise_overall_report'),
    path('franchise_patient_test_details/', franchise.franchise_patient_test_details, name='franchise_patient_test_details'),
    path('get-test-values/', franchise.get_test_value_for_franchise, name='get_test_values_franchise'),

    #HMS Report:
    path('hms_overall_report/', hmsreport.hms_overall_report, name='overall_report'),   
    path('get_hms_patient_test_details/', hmsreport.get_hms_patient_test_details, name='get_patient_test_details'),
    path('hms_send-email/', hmsreport.hms_send_email, name='send_email'),
    path('hms_update_dispatch_status/<str:barcode>/', hmsreport.hms_update_dispatch_status, name='update_dispatch_status'),
    path("test-summary/", dashboard.test_summary, name="test-summary"),
    
    #HMS Billing:
    path("hms_list_doctor/",hmsbilling.hms_get_doctor_list,name="doctor_list"),
    path("hms_testdetails/", hmsbilling.hms_get_test_details, name="hms_get_test_details"),
    path("hms_patient_billing/", hmsbilling.hms_patient_billing, name="hms_patient_billing"),

    #HMS Barcode:
    path('hms_patients_get_barcode/', hmsbarcode.get_hms_barcode_by_date, name='get_barcode_by_date'),    
    path('save-hms-barcodes/', hmsbarcode.save_hms_barcodes, name='save_barcodes'),

    path('get_hmssamplestatus_testvalue/',hmstestvalue.get_hmssamplestatus_testvalue, name='get_hmssamplestatus_testvalue'),
    path('hmscompare_test_details/',hmstestvalue.hmscompare_test_details, name='hmscompare_test_details'),

    #HMS Sample:
    path('hms_sample_patient/', hmssamplestatus.hms_get_samplepatients_by_date, name='hms_get_samplepatients_by_date'),
    path('hms_sample_status/', hmssamplestatus.hms_sample_status, name='hms_sample_status'),
    path('hms_check_sample_status/<str:barcode>/', hmssamplestatus.hms_check_sample_status, name='hms_check_sample_status'),
    path('hms_sample_status_data/<str:barcode>/', hmssamplestatus.hms_get_sample_status_data, name='hms_get_sample_status_data'),
    path('hms_patch_sample_status/<str:barcode>/', hmssamplestatus.hms_patch_sample_status, name='hms_patch_sample_status'),
    path('hms_get_sample_collected/', hmssamplestatus.hms_get_sample_collected, name='hms_get_sample_collected'),
    path('hms_update_sample_collected/<str:barcode>/', hmssamplestatus.hms_update_sample_collected, name='hms_update_sample_collected'),

    #MIS:
    path('consolidated-data/', mis.ConsolidatedDataView.as_view(), name='consolidated_data'),
    path('hms-consolidated-data/', mis.HMSConsolidatedDataView.as_view(), name='hms_consolidated_data'),
    path('franchise-consolidated-data/', mis.FranchiseConsolidatedDataView.as_view(), name='franchise_consolidated_data'),
]