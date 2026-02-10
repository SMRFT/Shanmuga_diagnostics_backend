#urls.py
from django.urls import path
from core import views
from .Views.hms import hmsbarcode,hmsbilling,hmsreport,hmssamplestatus
from .Views import whatsapp,franchise,sales,mis,dashboard,corporate,logistic,location,m_dashboard,os_management,microbiology
from .Views import patients,clinicalname,form,testdetails,barcode,sample,testvalue,testapproval,report
from core.Views.invoice import generate_invoice,get_invoices,delete_invoice,update_invoice,get_clinicalname_invoice,get_all_patients,patient_report
from core.Views.refundandcancellation import search_cancellation,verify_and_process_refund,search_refund,verify_and_process_cancellation,generate_otp_cancellation,generate_otp_refund,logs_api
from .Views import preetham_hospital_report

urlpatterns = [
    #Registration and Billing:
    path('create_patient/', patients.create_patient, name='create_patient'),
    path('create_patient/<str:patient_id>/', patients.create_patient, name='create_patient'),
    path('latest-patient-id/', patients.get_latest_patient_id, name='get_latest_patient_id'),
    path('patient-get/', patients.patient_get, name='patient_get'),
    path('create_bill/', patients.create_bill, name='create_bill'),
    path('update_bill/', patients.update_bill, name='update_bill'),
    path('latest-bill-no/', patients.get_latest_bill_no, name='get_latest_bill_no'),
    path('patients_by_date/', patients.get_patients_by_date, name='get_patients_by_date'),
    path('testdetails/', testdetails.get_test_details, name='get_test_details'),
    path('send_approval_email/', testdetails.send_approval_email, name='send_approval_email'),
    path('approve_test/', testdetails.approve_test, name='approve_test'),
    path('test_details_test/', testdetails.handle_patch_request, name='get_test_details'),
    path('clinical_name/last/', clinicalname.get_last_referrer_code, name='get_last_referrer_code'),
    path('clinical_name/', clinicalname.clinical_name, name='create_organisation'),
    path('sample-collector/', form.sample_collector, name='create_sample_collector'),
    path('sales_person/', clinicalname.sales_person, name='sales_person'),
    path('dashboard-data/', patients.dashboard_data, name='sales_person'),
    path('refby/', form.refby, name='refby'),
    path("appointments/", patients.appointment_booking, name="appointment_booking"),
    path('get_clinicalname/', clinicalname.get_clinicalname, name='get_clinicalname'),


    #Barcode:
    path('patients_get_barcode/', barcode.get_barcode_by_date, name='get_barcode_by_date'),
    path('get-max-barcode/', barcode.get_max_barcode, name='get_max_barcode'),
    path('save-barcodes/', barcode.save_barcodes, name='save_barcodes'),
    path('get-existing-barcode/',barcode.get_existing_barcode, name='get_latest_bill_no'),

    #sampleStatus:
    path('sample_patient/', sample.get_samplepatients_by_date, name='get_samplepatients_by_date'),       
    path('sample_status/', sample.sample_status, name='sample_status'),   
    path('sample_statusupdate/<str:barcode>/', sample.patch_sample_status, name='patch_sample_status'),

    #sample accessioning:
    path("get_sample_collected/", sample.get_sample_collected, name="get_sample_collected"),
    path('get_outsource_labs/', sample.get_outsource_labs, name='get_outsource_labs'),
    path("update_sample_collected/<str:patient_id>/", sample.update_sample_collected, name="update_sample_collected"),  
    path("get_rejected_samples/", sample.get_rejected_samples, name="get_rejected_samples"),
    path('communication_logs/', whatsapp.get_communication_logs, name='get_communication_logs'),
    path('get_clinicalname/', clinicalname.get_clinicalname, name='get_clinicalname'),

    #sales
    path('hospitallabform/', sales.hospitallabform, name='hospitallabform'),
    path('get_all_clinicalnames/',sales.get_all_clinicalnames, name='get_all_clinicalnames'),
    path('SalesVisitLog/', sales.salesvisitlog, name='salesvisitlog'),
    path('get_sales_executives/', sales.get_sales_executives, name='get_sales_executives'),
    path('getsalesindividual/', sales.get_sales_individual_report, name='get_sales_individual_report'),
    path('salesdashboard/', sales.salesdashboard, name='salesdashboard'),
    path('salesexecutive_report/', sales.Adminview_salesexecutive_report, name='salesexecutive_report'),
    path('update_dispatch_status/<str:barcode>/', report.update_dispatch_status, name='update_dispatch_status'),
    path('clinicalname_update/', sales.update_clinicalname, name='update_clinicalname'),
    path('get_clinicalname/', clinicalname.get_clinicalname, name='get_clinicalname'),
    path('clinical-names/', clinicalname.ClinicalNameViewSet.as_view({'get': 'list'}), name='clinical-names-list'),
    path('clinical-names/<str:referrerCode>/', clinicalname.ClinicalNameViewSet.as_view({'get': 'retrieve'}), name='clinical-name-detail'),
    path('clinical-names/<str:referrerCode>/first_approve/', clinicalname.ClinicalNameViewSet.as_view({'patch': 'first_approve'}), name='clinical-name-first-approve'),
    path('clinical-names/<str:referrerCode>/final_approve/', clinicalname.ClinicalNameViewSet.as_view({'patch': 'final_approve'}), name='clinical-name-final-approve'),
    path('mou-preview/<str:file_id>/',clinicalname.preview_mou_file, name='preview_mou_file'),

    #Logistics
    path('logistics/',logistic.create_logistics, name='logistics'),
    path('logistics_by_collector/',logistic.logistics_by_collector, name='logistics_by_collector'),
    path('logistics/<int:task_id>/accept/', logistic.accept_task, name='accept_task'),
    path('logistics/<int:task_id>/reject/', logistic.reject_task, name='reject_task'),
    path('logistics/<int:task_id>/pickup/', logistic.pickup_task, name='pickup_task'),
    path('logistics-dashboard/', logistic.logistics_dashboard, name='logistics_dashboard'),
    path('logistics-tat-report/', logistic.logistics_tat_report, name='logistics_tat_report'),
    # path('sample-collector-location/', location.create_sample_collector_location, name='create_sample_collector_location'),
    # path('collector-route-stats/', location.collector_route_stats, name='collector_route_stats'),

    #Barcode:
    path('patients_get_barcode/', barcode.get_barcode_by_date, name='get_barcode_by_date'),
    path('get-max-barcode/', barcode.get_max_barcode, name='get_max_barcode'),
    path('save-barcodes/', barcode.save_barcodes, name='save_barcodes'),
    path('get-existing-barcode/',barcode.get_existing_barcode, name='get_latest_bill_no'),
    path("get_outsourced_samples/", sample.get_outsourced_samples, name="get_outsourced_samples"),
    #Test Values:
    path('samplestatus-testvalue/', testvalue.get_samplestatus_testvalue, name='sample-status-list'), 
    path('compare_test_details/', testvalue.compare_test_details, name='compare_test_details'),
    path('test-value/save/', testvalue.save_test_value, name='save_test_value'),   

    #M/B Test Values:
    path('micro_biology_testvalue/', microbiology.micro_biology_testvalue, name='micro_biology_testvalue'),  
    path('mb-compare_test_details/', microbiology.mb_compare_test_details, name='mb_compare_test_details'),
    path('mb-test-value/save/', microbiology.mb_save_test_value, name='mb_save_test_value'),

    #M/B Test Approval:y
    path('mb-test-values/', microbiology.mb_get_test_values, name='mb_get_test_values'),
    path('mb-test-approval/<str:barcode>/approve/', microbiology.mb_approve_test_detail, name='mb_approve_test_detail'),
    path('mb-test-rerun/<str:barcode>/rerun/', microbiology.mb_rerun_test_detail, name='mb_rerun_test_detail'),

    #M/B Reports:   
    path('mb_patient_test_sorting/', microbiology.mb_patient_test_sorting, name='mb_patient_test_sorting'),
    path('mb_get_patient_test_details/', microbiology.mb_get_patient_test_details, name='mb_get_patient_test_details'),
    path('hms_mb_get_patient_test_details/', microbiology.hms_mb_get_patient_test_details, name='hms_mb_get_patient_test_details'),
    path('franchise_mb_get_patient_test_details/', microbiology.franchise_mb_get_patient_test_details, name='franchise_mb_get_patient_test_details'),
    path('mb_update_dispatch_status/<str:barcode>/', microbiology.mb_update_dispatch_status, name='mb_update_dispatch_status'),


    #Out source Test Values:
    path('os-samplestatus-testvalue/', os_management.get_os_samplestatus_testvalue, name='os_sample-status-list'), 
    path('os-compare_test_details/', os_management.os_compare_test_details, name='os_compare_test_details'),

    #Test Approval:y
    path('test-values/', testapproval.get_test_values, name='get_test_values'),
    path('test-approval/<str:barcode>/approve/', testapproval.approve_test_detail, name='approve_test_detail'),
    path('test-rerun/<str:barcode>/rerun/', testapproval.rerun_test_detail, name='rerun_test_detail'),

    #Diagnostics Reports:
    path('overall_report/', report.overall_report, name='overall_report'),    
    path('patient_test_sorting/', report.patient_test_sorting, name='patient_test_sorting'),
    path('get_patient_test_details/', report.get_patient_test_details, name='get_patient_test_details'),

    #Invoice URLs
    path("generate-invoice/", generate_invoice, name="generate-invoice"),
    path("get-invoices/", get_invoices, name="get-invoices"),
    path("update-invoice/", update_invoice, name="update-invoice"),
    path("delete-invoice/", delete_invoice, name="delete-invoice"),
    path('get_clinicalname_invoice/', get_clinicalname_invoice, name='get_clinicalname_by_referrer'),
    path('all-patients/', get_all_patients, name='get_all_patients'),
    path('patient_report/', patient_report, name='patient_report'),
    path('b2b_ledger_report/', report.b2b_ledger_report, name='b2b_ledger_report'),

    path('preetham_hospital_report/', preetham_hospital_report.preetham_hospital_report, name='preetham_hospital_report'),


    # Refund and Cancellation URLs
    path('search_refund/', search_refund, name='search_refund'),
    path('verify_and_process_refund/', verify_and_process_refund, name='verify_and_process_refund'),
    path('search_cancellation/', search_cancellation, name='search_cancellation'),
    path('generate_otp_refund/', generate_otp_refund, name='generate_otp_refund'),
    path('generate_otp_cancellation/', generate_otp_cancellation, name='generate_otp_cancellation'),
    path('verify_and_process_cancellation/',verify_and_process_cancellation, name='verify_and_process_cancellation'),
    path('refund_cancellation_logs/', logs_api, name='refund_cancellation_logs'),
    path('patient-get/', patients.patient_get, name='patient_get'),
    path("upload-pdf/", whatsapp.upload_pdf_to_gridfs, name="upload_pdf"),
    path("get-file/<str:file_id>/", whatsapp.get_pdf_from_gridfs, name="get_pdf"),
    path("send-whatsapp/", whatsapp.send_whatsapp, name="send_whatsapp"),
    path('get_patientsbyb2b/', patients.get_patientsbyb2b, name='get_patients'),
    path('patient_overview/', patients.patient_overview, name='patient_overview'),
    path('credit_amount/', patients.update_credit_amount, name='update_credit_amount'),
    path('send-email/', whatsapp.send_email, name='send_email'),
    path('communication_logs/', whatsapp.get_communication_logs, name='get_communication_logs'),

    #Franchise Batch and Sample Status Update:
    path('franchise-batches/', franchise.get_batch_generation_data, name='get_batch_generation_data'),
    path('franchise-receive/<str:batch_no>/', franchise.update_batch_received_status, name='update_batch_received_status'),
    path("get_franchise_Transferred/<str:batch_number>/", franchise.get_franchise_sample, name="get_franchise_sample"),
    path("update_franchise_sample/<str:barcode>/", franchise.update_franchise_sample, name="update_franchise_sample"), 
    
    #Franchise Reports:     
    path('franchise_overall_report/', franchise.franchise_overall_report, name='franchise_overall_report'),
    path('franchise_patient_test_details/', franchise.franchise_patient_test_details, name='franchise_patient_test_details'),

    #HMS Report:
    path('hms_overall_report/', hmsreport.hms_overall_report, name='overall_report'),   
    path('get_hms_patient_test_details/', hmsreport.get_hms_patient_test_details, name='get_hms_patient_test_details'),
    path('hms_update_dispatch_status/<str:barcode>/', hmsreport.hms_update_dispatch_status, name='update_dispatch_status'),
    path("test-summary/", dashboard.test_summary, name="test-summary"),
    path("m-dashboard-stats/", m_dashboard.m_dashboard_stats, name="m_dashboard_stats"),
    
    #HMS Billing:
    path("hms_list_doctor/",hmsbilling.hms_get_doctor_list,name="doctor_list"),
    path("hms_testdetails/", hmsbilling.hms_get_test_details, name="hms_get_test_details"),
    path("hms_patient_billing/", hmsbilling.hms_patient_billing, name="hms_patient_billing"),

    #HMS Barcode:
    path('hms_patients_get_barcode/', hmsbarcode.get_hms_barcode_by_date, name='get_barcode_by_date'),    
    path('save-hms-barcodes/', hmsbarcode.save_hms_barcodes, name='save_barcodes'),


    #Corporate Batch and Sample Status Update:
    path('corporate-batches/', corporate.get_corporate_batch_generation_data, name='get_corporate_batch_generation_data'),
    path('corporate-receive/<str:batch_no>/', corporate.update_corporate_batch_received_status, name='update_corporate_batch_received_status'),
    path("get_corporate_Transferred/<str:batch_number>/", corporate.get_corporate_sample, name="get_corporate_sample"),
    path("update_corporate_sample/<str:barcode>/", corporate.update_corporate_sample, name="update_corporate_sample"),
    #Corporate Reports:    
    path('corporate_overall_report/', corporate.corporate_overall_report, name='corporate_overall_report'),
    path('corporate_patient_test_details/', corporate.corporate_patient_test_details, name='corporate_patient_test_details'),
    path('corporate_approval_report/', corporate.corporate_approval_report, name='corporate_approval_report'),
    path('corporate_health_report/', corporate.corporate_health_report, name='corporate_health_report'),
    path('get_investigation_file/', corporate.get_investigation_file, name='get_investigation_file'),
    path('get_investigation_status/', corporate.get_investigation_status, name='get_investigation_status'),
    path('save_overall_approval/', corporate.save_overall_approval, name='save_overall_approval'),
    path('get_batch_investigation_status/', corporate.get_batch_investigation_status, name='get_batch_investigation_status'),
    path('get_batch_corporate_health_reports/', corporate.get_batch_corporate_health_reports, name='get_batch_corporate_health_reports'),
    

    #HMS Billing:
    path("hms_list_doctor/",hmsbilling.hms_get_doctor_list,name="doctor_list"),
    path("hms_testdetails/", hmsbilling.hms_get_test_details, name="hms_get_test_details"),
    path("hms_patient_billing/", hmsbilling.hms_patient_billing, name="hms_patient_billing"),

    #HMS Barcode:
    path('hms_patients_get_barcode/', hmsbarcode.get_hms_barcode_by_date, name='get_barcode_by_date'),    
    path('save-hms-barcodes/', hmsbarcode.save_hms_barcodes, name='save_barcodes'),

    #HMS Sample:
    path('hms_sample_patient/', hmssamplestatus.hms_get_samplepatients_by_date, name='hms_get_samplepatients_by_date'),
    path('hms_sample_status/', hmssamplestatus.hms_sample_status, name='hms_sample_status'),
    path('hms_patch_sample_status/<str:barcode>/', hmssamplestatus.hms_patch_sample_status, name='hms_patch_sample_status'),
    path('hms_get_sample_collected/', hmssamplestatus.hms_get_sample_collected, name='hms_get_sample_collected'),
    path('hms_update_sample_collected/<str:barcode>/', hmssamplestatus.hms_update_sample_collected, name='hms_update_sample_collected'),
    

    #HMS Report:
    path('hms_overall_report/', hmsreport.hms_overall_report, name='overall_report'),   
    path('get_hms_patient_test_details/', hmsreport.get_hms_patient_test_details, name='get_hms_patient_test_details'),
    path('hms_update_dispatch_status/<str:barcode>/', hmsreport.hms_update_dispatch_status, name='update_dispatch_status'),

    #MIS:
    path('consolidated-data/', mis.ConsolidatedDataView.as_view(), name='consolidated_data'),
    path('hms-consolidated-data/', mis.HMSConsolidatedDataView.as_view(), name='hms_consolidated_data'),
    path('franchise-consolidated-data/', mis.FranchiseConsolidatedDataView.as_view(), name='franchise_consolidated_data'),

    #sales
    path('hospitallabform/', sales.hospitallabform, name='hospitallabform'),
    path('get_all_clinicalnames/',sales.get_all_clinicalnames, name='get_all_clinicalnames'),
    path('SalesVisitLog/', sales.salesvisitlog, name='salesvisitlog'),
    path('get_sales_executives/', sales.get_sales_executives, name='get_sales_executives'),
    path('getsalesindividual/', sales.get_sales_individual_report, name='get_sales_individual_report'),
    path('salesdashboard/', sales.salesdashboard, name='salesdashboard'),
    path('Adminview_salesexecutive_report/', sales.Adminview_salesexecutive_report, name='Adminview_salesexecutive_report'),
    path('clinicalname_update/', sales.update_clinicalname, name='update_clinicalname'),
    path('serve_sales_image/<str:file_id>/', sales.serve_sales_image, name='serve_sales_image'),

    #Invoice URLs
    path("generate-invoice/", generate_invoice, name="generate-invoice"),
    path("get-invoices/", get_invoices, name="get-invoices"),
    path("update-invoice/<str:invoice_number>/", update_invoice, name="update-invoice"),
    path("delete-invoice/<str:invoice_id>/", delete_invoice, name="delete-invoice"),
    path('get_clinicalname_invoice/', get_clinicalname_invoice, name='get_clinicalname_by_referrer'),
    path('all-patients/', get_all_patients, name='get_all_patients'),
    path('patient_report/', patient_report, name='patient_report'),

    path('get_devices/', testdetails.get_devices, name='get_devices'),
    path('preetham_hospital_report/', preetham_hospital_report.preetham_hospital_report, name='preetham_hospital_report'),
    path('preetham_billing_dashboard/', preetham_hospital_report.preetham_billing_dashboard, name='preetham_billing_dashboard'),
    path('get_preethampatient_test_details/', preetham_hospital_report.get_preethampatient_test_details, name='get_preethampatient_test_detailss'),
    
    path("test-summary/", dashboard.test_summary, name="test-summary"),
    
]
