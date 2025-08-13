#urls.py
from django.urls import path
from .Views import patients

urlpatterns = [
    path('create_patient/', patients.create_patient, name='create_patient'),
    path('create_patient/<str:patient_id>/', patients.create_patient, name='create_patient'),
    path('latest-patient-id/', patients.get_latest_patient_id, name='get_latest_patient_id'),
    path('create_bill/', patients.create_bill, name='create_bill'),
    path('latest-bill-no/', patients.get_latest_bill_no, name='get_latest_bill_no'),
]