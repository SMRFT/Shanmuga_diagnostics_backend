#urls.py
from django.urls import path
from core import views
from .Views.hms import hmsbarcode,hmsbilling,hmsreport,hmssamplestatus
from .Views import whatsapp,franchise,sales,mis,dashboard,corporate,logistic,location,m_dashboard,os_management,microbiology,franchiseentrollment, expo_notifications
from .Views import patients,clinicalname,form,testdetails,barcode,sample,testvalue,testapproval,report
from core.Views.invoice import generate_invoice,get_invoices,delete_invoice,update_invoice,get_clinicalname_invoice,get_all_patients,patient_report
from core.Views.refundandcancellation import search_cancellation,verify_and_process_refund,search_refund,verify_and_process_cancellation,generate_otp_cancellation,generate_otp_refund,logs_api
from .Views import clinical_hospital_report

urlpatterns = [
    #Registration and Billing:
    path('create_patient/', patients.create_patient, name='create_patient'),
    path('create_patient/<str:patient_id>/', patients.create_patient, name='create_patient'),
    path('update_patient/<str:patient_id>/', patients.update_patient, name='update_patient'),
    path('latest-patient-id/', patients.get_latest_patient_id, name='get_latest_patient_id'),
    path('patient-get/', patients.patient_get, name='patient_get'),
    path('create_bill/', patients.create_bill, name='create_bill'),
    path('patient_list/', patients.get_patient_list, name='get_patient_list'),
    path('patient_record_dashboard/', patients.patient_record_dashboard, name='patient_record_dashboard'),
    path('patient_full_record/<str:patient_id>/', patients.get_patient_full_record, name='get_patient_full_record'),
    path('prescription_image/<str:file_id>/', patients.get_prescription_image, name='get_prescription_image'),
    path('update_bill/', patients.update_bill, name='update_bill'),
    path('latest-bill-no/', patients.get_latest_bill_no, name='get_latest_bill_no'),
    path('patients_by_date/', patients.get_patients_by_date, name='get_patients_by_date'),
    path('testdetails/', testdetails.get_test_details, name='get_test_details'),
    path('get_test_details_estimate/', testdetails.get_test_details_estimate, name='get_test_details_estimate'),
    path('send_approval_email/', testdetails.send_approval_email, name='send_approval_email'),
    path('approve_test/', testdetails.approve_test, name='approve_test'),
    path('test_details_test/', testdetails.handle_patch_request, name='get_test_details'),
    path('clinical_name/last/', clinicalname.get_last_referrer_code, name='get_last_referrer_code'),
    path('clinical_name/', clinicalname.clinical_name, name='clinical_name'),
    path('sample-collector/', form.sample_collector, name='create_sample_collector'),
    path('sales_person/', clinicalname.sales_person, name='sales_person'),
    path('dashboard-data/', patients.dashboard_data, name='sales_person'),
    path('refby/', form.refby, name='refby'),
    path("appointments/", patients.appointment_booking, name="appointment_booking"),
    path("appointments_by_date/", patients.get_appointments_by_date, name="get_appointments_by_date"),
    path("appointments/<int:appointment_id>/cancel/", patients.cancel_appointment, name="cancel_appointment"),
    path('get_clinicalname/', clinicalname.get_clinicalname, name='get_clinicalname'),
    path('b2b_packages/', clinicalname.b2b_packages, name='b2b_packages'),
    #Barcode:
    path('patients_get_barcode/', barcode.get_barcode_by_date, name='get_barcode_by_date'),
    path('get-max-barcode/', barcode.get_max_barcode, name='get_max_barcode'),
    path('save-barcodes/', barcode.save_barcodes, name='save_barcodes'),
    path('get-existing-barcode/',barcode.get_existing_barcode, name='get_latest_bill_no'),

    #sampleStatus:
    path('sample_patient/', sample.get_samplepatients_by_date, name='get_samplepatients_by_date'),       
    path('sample_status/', sample.sample_status, name='sample_status'),   
    path('sample_statusupdate/<str:barcode>/', sample.patch_sample_status, name='patch_sample_status'),
    path('check_sample_status/<str:barcode>/', sample.check_sample_status, name='check_sample_status'),

    #sample accessioning:
    path("get_sample_collected/", sample.get_sample_collected, name="get_sample_collected"),
    path('get_outsource_labs/', sample.get_outsource_labs, name='get_outsource_labs'),
    path("update_sample_collected/<str:barcode>/", sample.update_sample_collected, name="update_sample_collected"),  
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
    path('salesplan/', sales.salesplan, name='salesplan'),
    path('salesplan_summary/', sales.salesplan_summary, name='salesplan_summary'),
    path('overall_summary/', sales.overall_summary, name='overall_summary'),
    path('salesplanreport/', sales.salesplanreport, name='salesplan'),
    path('salesexecutive_report/', sales.Adminview_salesexecutive_report, name='salesexecutive_report'),
    path('clinicalname_update/', sales.update_clinicalname, name='update_clinicalname'),
    path('get_clinicalname/', clinicalname.get_clinicalname, name='get_clinicalname'),
    path('clinical-names/', clinicalname.ClinicalNameViewSet.as_view({'get': 'list'}), name='clinical-names-list'),
    path('clinical-names/<str:referrerCode>/', clinicalname.ClinicalNameViewSet.as_view({'get': 'retrieve'}), name='clinical-name-detail'),
    path('clinical-names/<str:referrerCode>/first_approve/', clinicalname.ClinicalNameViewSet.as_view({'patch': 'first_approve'}), name='clinical-name-first-approve'),
    path('clinical-names/<str:referrerCode>/final_approve/', clinicalname.ClinicalNameViewSet.as_view({'patch': 'final_approve'}), name='clinical-name-final-approve'),
    path('clinical-names/<str:referrerCode>/reject/', clinicalname.clinicalname_reject, name='clinical-name-reject'),
    path('mou-preview/<str:file_id>/',clinicalname.preview_mou_file, name='preview_mou_file'),

    #Logistics
    path('register-push-token/', expo_notifications.register_push_token, name='register_push_token'),
    path('logistics/',logistic.create_logistics, name='logistics'),
    path('logistics_by_collector/',logistic.logistics_by_collector, name='logistics_by_collector'),
    path('logistics/accept/<int:task_id>/', logistic.accept_task, name='accept_task'),
    path('logistics/reject/<int:task_id>/', logistic.reject_task, name='reject_task'),
    path('logistics/pickup/<int:task_id>/', logistic.pickup_task, name='pickup_task'),
    path('logistics/reassign/<int:task_id>/', logistic.reassign_task, name='reassign_task'),
    path('logistics-dashboard/', logistic.logistics_dashboard, name='logistics_dashboard'),
    path('logistics-tat-report/', logistic.logistics_tat_report, name='logistics_tat_report'),
    path('sample-collector-location/', location.sample_collector_location, name='sample_collector_location'),
    path('sample-collector-location-history/', location.sample_collector_location_history, name='sample_collector_location_history'),
    path('routesetup/', logistic.routesetup, name='routesetup'),
    path('routesetup/<int:route_id>/', logistic.routesetup, name='routesetup_detail'),
    path('route-analysis/start/', logistic.start_route_analysis, name='start-route-analysis'),
    path('route-analysis/end/', logistic.end_route_analysis, name='end-route-analysis'),
    path('route-analysis/mark/', logistic.mark_visit, name='mark-visit'),
    path('route-analysis/image/<str:file_id>/', logistic.get_route_image, name='get-route-image'),
    path('route-analysis/active/<int:route_id>/', logistic.get_active_route_analysis, name='get-active-route-analysis'),
    path('route-analysis/today-status/', logistic.get_todays_route_status, name='get-todays-route-status'),
    path('route-analysis/admin-report/', logistic.route_analysis_admin_report, name='route-analysis-admin-report'),

    path("bus_fare/", logistic.bus_fare, name="bus_fare"),
    path("bus_fare_photo/", logistic.bus_fare_photo, name="bus_fare_photo"),

  
    path("get_b2b_employees/", logistic.get_b2b_employees, name="get_b2b_employees"),

      # customer complaints:
    path("get_b2b_lab_employees/", logistic.get_b2b_lab_employees, name="get_b2b_lab_employees"),
    path("customer_complaints/", logistic.customer_complaints, name="customer_complaints"),

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
    path('worklist/', testvalue.worklist_view, name='worklist'),   

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
    path('test-approved-values/', testapproval.get_approved_values, name='get_approved_values'),
    path('test-edit/<str:barcode>/edit/', testapproval.edit_test_value, name='edit_test_value'),


    #Diagnostics Reports:
    path('overall_report/', report.overall_report, name='overall_report'),    
    path('shanmuga360_overall_report/', report.shanmuga360_overall_report, name='shanmuga360_overall_report'),    
    path('patient_test_sorting/', report.patient_test_sorting, name='patient_test_sorting'),
    path('get_patient_test_details/', report.get_patient_test_details, name='get_patient_test_details'),    
    path('update_dispatch_status/<str:barcode>/', report.update_dispatch_status, name='update_dispatch_status'),
    path('update_printed_status/<str:barcode>/', report.update_printed_status, name='update_printed_status'),

    #Invoice URLs
    path("generate-invoice/", generate_invoice, name="generate-invoice"),
    path("get-invoices/", get_invoices, name="get-invoices"),
    path("update-invoice/", update_invoice, name="update-invoice"),
    path("delete-invoice/", delete_invoice, name="delete-invoice"),
    path('get_clinicalname_invoice/', get_clinicalname_invoice, name='get_clinicalname_by_referrer'),
    path('all-patients/', get_all_patients, name='get_all_patients'),
    path('patient_report/', patient_report, name='patient_report'),
    path('b2b_ledger_report/', report.b2b_ledger_report, name='b2b_ledger_report'),

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
    path("whatsapp-get-file/<str:file_id>/", whatsapp.get_pdf_from_gridfs, name="whatsapp_get_pdf"),
    path("send-whatsapp/", whatsapp.send_whatsapp, name="send_whatsapp"),
    path('get_patientsbyb2b/', patients.get_patientsbyb2b, name='get_patients'),
    path('patient_overview/', patients.patient_overview, name='patient_overview'),
    path('credit_amount/', patients.update_credit_amount, name='update_credit_amount'),
    path('send-email/', whatsapp.send_email, name='send_email'),
    path('communication_logs/', whatsapp.get_communication_logs, name='get_communication_logs'),
    path('get_home_collection_report/', report.get_home_collection_report, name='get_home_collection_report'),

    #Franchise Batch and Sample Status Update:
    path('franchise-batches/', franchise.get_batch_generation_data, name='get_batch_generation_data'),
    path('franchise-receive/<str:batch_no>/', franchise.update_batch_received_status, name='update_batch_received_status'),
    path("get_franchise_Transferred/<str:batch_number>/", franchise.get_franchise_sample, name="get_franchise_sample"),
    path("update_franchise_sample/<str:barcode>/", franchise.update_franchise_sample, name="update_franchise_sample"), 

    path('get_test_value_for_franchise/', franchise.get_test_value_for_franchise, name='get_test_value_for_franchise'),
    
    #Franchise Reports:     
    path('franchise_overall_report/', franchise.franchise_overall_report, name='franchise_overall_report'),
    path('franchise_patient_test_details/', franchise.franchise_patient_test_details, name='franchise_patient_test_details'),

    #HMS Report:
    path('hms_overall_report/', hmsreport.hms_overall_report, name='overall_report'),   
    path('get_hms_patient_test_details/', hmsreport.get_hms_patient_test_details, name='get_hms_patient_test_details'),
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
    path('corporate_credit_billing/', corporate.corporate_credit_billing, name='corporate_credit_billing'),
    path('generate_corporate_invoice/', corporate.generate_corporate_invoice, name='generate_corporate_invoice'),
    path('get_corporate_invoices/', corporate.get_corporate_invoices, name='get_corporate_invoices'),
    path('update_corporate_invoice/', corporate.update_corporate_invoice, name='update_corporate_invoice'),
    path('delete_corporate_invoice/', corporate.delete_corporate_invoice, name='delete_corporate_invoice'),
    path('export_corporate_invoice_pdf/', corporate.export_corporate_invoice_pdf, name='export_corporate_invoice_pdf'),

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
    path('hms_check_sample_status/<str:barcode>/', hmssamplestatus.hms_check_sample_status, name='hms_check_sample_status'),
    

    #HMS Report:
    path('hms_overall_report/', hmsreport.hms_overall_report, name='overall_report'),   
    path('get_hms_patient_test_details/', hmsreport.get_hms_patient_test_details, name='get_hms_patient_test_details'),

    #MIS:
    path('consolidated-data/', mis.ConsolidatedDataView.as_view(), name='consolidated_data'),
    path('shanmuga360-consolidated-data/', mis.Shanmuga360ConsolidatedDataView.as_view(), name='shanmuga360_consolidated_data'),
    path('hms-consolidated-data/', mis.HMSConsolidatedDataView.as_view(), name='hms_consolidated_data'),
    path('franchise-consolidated-data/', mis.FranchiseConsolidatedDataView.as_view(), name='franchise_consolidated_data'),
    path('hms-test-count/', mis.HMSTestCountView.as_view(), name='hms_test_count'),

    #Invoice URLs
    path("generate-invoice/", generate_invoice, name="generate-invoice"),
    path("get-invoices/", get_invoices, name="get-invoices"),
    path("update-invoice/<str:invoice_number>/", update_invoice, name="update-invoice"),
    path("delete-invoice/<str:invoice_id>/", delete_invoice, name="delete-invoice"),
    path('get_clinicalname_invoice/', get_clinicalname_invoice, name='get_clinicalname_by_referrer'),
    path('all-patients/', get_all_patients, name='get_all_patients'),
    path('patient_report/', patient_report, name='patient_report'),

    path('get_devices/', testdetails.get_devices, name='get_devices'),


    path('clinical_hospital_report/', clinical_hospital_report.clinical_hospital_report, name='clinical_hospital_report'),
    path('clinical_billing_dashboard/', clinical_hospital_report.clinical_billing_dashboard, name='clinical_billing_dashboard'),
    path('get_clinicalpatient_test_details/', clinical_hospital_report.get_clinicalpatient_test_details, name='get_clinicalpatient_test_details'),
    path('clinical_hospital_ledger/', clinical_hospital_report.clinical_hospital_ledger, name='clinical_hospital_ledger'),
    
    path("test-summary/", dashboard.test_summary, name="test-summary"),

    # Franchise Enrollment & Management URLs
    path('franchiseregister/', franchiseentrollment.register_franchise, name='register_franchise'),
    path('franchise/reset-password/', franchiseentrollment.reset_franchise_password, name='reset_franchise_password'),
    path('franchise/validate-token/', franchiseentrollment.validate_reset_token, name='validate_reset_token'),
    path('toggle-franchise-status/<str:franchise_id>/', franchiseentrollment.toggle_franchise_status, name='toggle_franchise_status'),
    path('getlocations/', franchiseentrollment.get_all_franchise_locations, name='get_all_franchise_locations'),
    path('getactivelocations/', franchiseentrollment.get_inactive_franchise_locations, name='get_inactive_franchise_locations'),
    path('get-franchise/', franchiseentrollment.get_registered_franchise, name='get_registered_franchise'),
    path('get-file/<str:file_id>/', franchiseentrollment.get_file, name='get_file'),
    path('updatestatus/<str:location_id>/', franchiseentrollment.update_franchise_status, name='update_franchise_status'),
    path('get-franchise-edit/<str:franchise_id>/', franchiseentrollment.get_franchise, name='get_franchise'),
    path('update-franchise/<str:franchise_id>/', franchiseentrollment.update_franchise, name='update_franchise'),
    path('getnextfranchiseid/', franchiseentrollment.generate_next_franchise_id, name='generate_next_franchise_id'),
    path('getfranchise/', franchiseentrollment.get_franchises, name='get_franchises'),
    path('stockbarcode/', franchiseentrollment.savestockbarcode, name='savestockbarcode'),
    path('inactive-franchises/', franchiseentrollment.inactive_franchises, name='inactive_franchises'),
    path('resend-password-reset/', franchiseentrollment.resend_password_reset_email, name='resend_password_reset'),
    path('bulk-resend-password-reset/', franchiseentrollment.bulk_resend_password_reset_emails, name='bulk_resend_password_reset'),
    path('cancel-requested/', franchiseentrollment.get_cancel_requested_tests, name='cancel_requested'),
    path('update-test-status/', franchiseentrollment.update_test_status, name='update_test_status'),
    path('update_cancel_status/', franchiseentrollment.update_cancel_status, name='update_cancel_status'),
    path('monthend/', franchiseentrollment.month_end_calculation, name='month_end_calculation'),
    path('post_loaction/', franchiseentrollment.post_location, name='post_loaction'),
    path('getandupdatebarcode/', franchiseentrollment.getandupdatebarcode, name='getandupdatebarcode'),
    path('savebarcode/', franchiseentrollment.savestockbarcode, name='savebarcode'),
]


