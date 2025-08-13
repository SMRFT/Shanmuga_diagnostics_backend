from django.db import models
from datetime import datetime
from django.utils.timezone import now


class AuditModel(models.Model):
    created_by = models.CharField(max_length=100, blank=True, null=True)
    created_date = models.DateTimeField(auto_now_add=True)
    lastmodified_by = models.CharField(max_length=100, blank=True, null=True)
    lastmodified_date = models.DateTimeField(auto_now=True)
    class Meta:
        abstract = True
    def save(self, *args, **kwargs):
        if not self.created_by:
            self.created_by = "system"
        self.lastmodified_by = self.lastmodified_by or "system"
        super().save(*args, **kwargs)


class Patient(AuditModel):
    patient_id = models.CharField(max_length=20, primary_key=True, blank=True)
    patientname = models.CharField(max_length=100)
    age = models.IntegerField()
    age_type = models.CharField(max_length=10, blank=True)
    gender = models.CharField(max_length=10)
    phone = models.CharField(max_length=15, blank=True)
    email = models.EmailField(blank=True)
    address = models.JSONField(blank=True, null=True)
    branch = models.CharField(max_length=100, blank=True)
    def __str__(self):
        return self.patient_id
class Billing(AuditModel):
    patient_id = models.CharField(max_length=10)
    date = models.DateTimeField(null=True, blank=True)
    lab_id = models.CharField(max_length=50, blank=True)
    segment = models.CharField(max_length=100, blank=True)  # Referring doctor or reference
    ClinicalName = models.CharField(max_length=50, blank=True)
    sales_person = models.CharField(max_length=50, blank=True)
    sample_collector = models.CharField(max_length=50, blank=True)
    refby = models.CharField(max_length=100, blank=True)  # Referring doctor or reference
    testdetails = models.JSONField(blank=True)
    bill_no = models.CharField(max_length=20, primary_key=True, blank=True)  # New field for bill number
    total = models.CharField(max_length=50, blank=True)
    discount= models.CharField(max_length=100, blank=True)
    payment_method = models.JSONField(blank=True)
    MultiplePayment = models.JSONField(max_length=150, blank=True)
    credit_amount = models.CharField(max_length=100, blank=True)
    def save(self, *args, **kwargs):
        # Generate the billno if not already set
        self.credit_amount = self.credit_amount if self.credit_amount else "0"
        if not self.bill_no:
            today = datetime.now().strftime('%Y%m%d')  # Current year, month, date
            last_bill = Billing.objects.filter(bill_no__startswith=today).order_by('-bill_no').first()
            if last_bill:
                # Increment the last bill number
                last_id = int(last_bill.bill_no[-4:])  # Extract the last 4 digits
                next_id = last_id + 1
            else:
                next_id = 1  # Start with 1 if no bills exist for the day
            self.bill_no = f"{today}{next_id:04d}"  # Generate billno in YYYYMMDD0001 format
        super().save(*args, **kwargs)  # Call the parent save method