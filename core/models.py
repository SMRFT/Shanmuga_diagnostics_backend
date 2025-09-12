from django.db import models
from bson import ObjectId  # Import ObjectId from bson
class AuditModel(models.Model):
    created_by = models.CharField(max_length=100, blank=True, null=True)
    created_date = models.DateTimeField(auto_now_add=True)
    lastmodified_by = models.CharField(max_length=100, blank=True, null=True)
    lastmodified_date = models.DateTimeField(blank=True, null=True)

    class Meta:
        abstract = True


class AuditModel(models.Model):
    created_by = models.CharField(max_length=100, blank=True, null=True)
    created_date = models.DateTimeField(auto_now_add=True)
    lastmodified_by = models.CharField(max_length=100, blank=True, null=True)
    lastmodified_date = models.DateTimeField(blank=True, null=True)
    class Meta:
        abstract = True
class Patient(AuditModel):
    patient_id = models.CharField(max_length=20, primary_key=True, blank=True)
    patientname = models.CharField(max_length=100)
    age = models.IntegerField()
    age_type = models.CharField(max_length=10, blank=True)
    gender = models.CharField(max_length=10)
    phone = models.CharField(max_length=15, blank=True)
    email = models.EmailField(blank=True)
    address = models.JSONField(blank=True, null=True)
    def __str__(self):
        return self.patient_id
class Billing(AuditModel):
    patient_id = models.CharField(max_length=20)
    date = models.DateTimeField(null=True, blank=True)
    lab_id = models.CharField(max_length=50, blank=True)
    segment = models.CharField(max_length=100, blank=True)
    B2B = models.CharField(max_length=50, blank=True)
    salesMapping = models.CharField(max_length=50, blank=True)
    sample_collector = models.CharField(max_length=50, blank=True)
    refby = models.CharField(max_length=100, blank=True)
    branch = models.CharField(max_length=100, blank=True)
    bill_date = models.DateTimeField(null=True, blank=True)
    bill_no = models.CharField(max_length=20, null=True, blank=True)
    testdetails = models.JSONField(blank=True, null=True)
    totalAmount = models.CharField(max_length=50, blank=True)
    netAmount = models.CharField(max_length=50, blank=True)
    discount = models.CharField(max_length=50, blank=True)
    payment_method = models.JSONField(blank=True, null=True)
    MultiplePayment = models.JSONField(blank=True, null=True)
    credit_amount = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, default="Registered")
    def __str__(self):
        return self.bill_no if self.bill_no else f"Bill for {self.patient_id}"
    @property
    def patientname(self):
        try:
            patient = Patient.objects.get(patient_id=self.patient_id)
            return patient.patientname
        except Patient.DoesNotExist:
            return None

class ClinicalName(AuditModel):
    referrerCode = models.CharField(max_length=10, primary_key=True)
    clinicalname = models.CharField(max_length=255)
    type = models.CharField(max_length=50, blank=True, null=True)
    salesMapping = models.CharField(max_length=100, blank=True, null=True)
    reportDelivery = models.CharField(max_length=100, blank=True, null=True)
    report = models.CharField(max_length=50, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    alternateNumber = models.CharField(max_length=20, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    area = models.CharField(max_length=100, blank=True, null=True)
    state = models.CharField(max_length=100, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    pincode = models.CharField(max_length=10, blank=True, null=True)
    b2bType = models.CharField(max_length=50, blank=True, null=True)
    creditType = models.CharField(max_length=50, blank=True, null=True)
    mou_file_id = models.CharField(max_length=255, blank=True, null=True)
    creditLimit = models.CharField(max_length=10, blank=True, null=True)
    invoicePeriod = models.CharField(max_length=50, blank=True, null=True)
    # Approval fields
    created_at = models.DateTimeField(auto_now_add=True)
    first_approved = models.BooleanField(default=False)
    final_approved = models.BooleanField(default=False)
    first_approved_timestamp = models.DateTimeField(null=True, blank=True)
    final_approved_timestamp = models.DateTimeField(null=True, blank=True)
    APPROVAL_STAGES = (
        ('PENDING_APPROVAL', 'Pending Approval'),
        ('PENDING_FINAL', 'Pending Final Approval'),
        ('APPROVED', 'Fully Approved'),
    )
    status = models.CharField(
        max_length=50,
        choices=APPROVAL_STAGES,
        default='PENDING_APPROVAL'
    )
    def __str__(self):
        return f"{self.clinicalname} ({self.referrerCode})"
    

class RefBy(AuditModel):
    name = models.CharField(max_length=255)
    qualification = models.CharField(max_length=255, blank=True, null=True)
    specialization = models.CharField(max_length=255, blank=True, null=True)
    email = models.CharField(max_length=255, blank=True, null=True)
    phone = models.CharField(max_length=255, blank=True, null=True)
    def __str__(self):
        return f"{self.name}"
    


class BarcodeTestDetails(AuditModel):
    patient_id = models.CharField(max_length=50)
    patientname = models.CharField(max_length=255)   
    age = models.CharField(max_length=255)
    gender = models.CharField(max_length=50)
    segment= models.CharField(max_length=100, blank=True)
    date = models.DateField()
    bill_no= models.CharField(max_length=50, primary_key=True,unique=True)
    barcode= models.CharField(max_length=50)
    testdetails = models.JSONField()  # Store tests as a list of dictionaries
    def __str__(self):
        return f"{self.patientname} - {self.patient_id}"
    
class SampleStatus(AuditModel):
    patient_id = models.CharField(max_length=100)    
    barcode= models.CharField(max_length=50)   
    date = models.DateTimeField(null=True, blank=True)  # Use DateTimeField to store both date and time
    testdetails = models.JSONField()  # Assuming you're using Django 3.1+ for JSONField
    def __str__(self):
        return self.patientname
    

class TestValue(AuditModel):
    _id = models.CharField(max_length=50, primary_key=True)  
    date = models.DateField()
    barcode= models.CharField(max_length=50)
    locationId= models.CharField(max_length=50)
    testdetails = models.JSONField()  # Store all test details in JSON format   
    def save(self, *args, **kwargs):
        if not self._id:
            self._id = str(ObjectId())  # Convert ObjectId to string
        super().save(*args, **kwargs)


class SampleCollector(AuditModel):
    name = models.CharField(max_length=255)
    gender  = models.CharField(max_length=255, blank=True, null=True)
    phone  = models.CharField(max_length=255, blank=True, null=True)
    email =models.CharField(max_length=255, blank=True, null=True)
    def __str__(self):
        return f"{self.name}"
    
class SalesVisitLog(models.Model):
    date = models.DateField()
    time = models.CharField(max_length=255)
    clinicalname = models.CharField(max_length=255,blank=True)
    salesMapping = models.CharField(max_length=100,blank=True)
    personMet = models.CharField(max_length=100,blank=True)
    designation = models.CharField(max_length=100,blank=True)
    location = models.CharField(max_length=100,blank=True)
    phoneNumber = models.CharField(max_length=15,blank=True)
    noOfVisits=  models.CharField(max_length=15,blank=True)
    comments = models.CharField(max_length=150,blank=True)
    type = models.CharField(max_length=100,blank=True)
    created_by = models.CharField(max_length=100, blank=True, null=True)
    created_date = models.DateTimeField(auto_now_add=True)
    lastmodified_by = models.CharField(max_length=100, blank=True, null=True)
    lastmodified_date = models.DateTimeField(blank=True, null=True)

from django.db import models
class HospitalLab(models.Model):
    TYPE_CHOICES = [
        ('StandAlone', 'StandAlone'),
        ('Lab', 'Lab'),
    ]
    clinicalname = models.CharField(max_length=255, blank=True)
    type = models.CharField(max_length=50, choices=TYPE_CHOICES, default='StandAlone')
    contactPerson = models.CharField(max_length=255, blank=True)
    contactNumber = models.CharField(max_length=255, blank=True)
    emailId = models.EmailField(max_length=255, blank=True)
    salesMapping = models.CharField(max_length=255, blank=True)
    # Audit fields
    created_by = models.CharField(max_length=100, blank=True, null=True)
    created_date = models.DateTimeField(auto_now_add=True)
    def __str__(self):
        return self.clinicalname


#HMS PART
class HmspatientBilling(AuditModel):
    patient_id = models.CharField(max_length=20,  blank=True)
    ipnumber= models.CharField(max_length=100,blank=True)
    patientname = models.CharField(max_length=100)
    age = models.IntegerField()
    age_type = models.CharField(max_length=10, blank=True)
    gender = models.CharField(max_length=10)
    phone = models.CharField(max_length=15, blank=True)
    location_id=models.CharField(max_length=15, blank=True,default="hms")
    billnumber= models.CharField(max_length=15, blank=True,primary_key=True)
    date=models.DateTimeField()
    ref_doctor= models.CharField(max_length=500, blank=True)
    testdetails = models.JSONField(blank=True, null=True)


class Hmsbarcode(AuditModel):
    billnumber= models.CharField(max_length=15, blank=True,primary_key=True)
    barcode= models.CharField(max_length=50,  blank=True)
    date=models.DateField()
    testdetails = models.JSONField(blank=True, null=True)
    location_id=models.CharField(max_length=15, blank=True,default="hms")


class Hmssamplestatus(AuditModel):
    barcode= models.CharField(max_length=50,  blank=True)
    date=models.DateField()
    testdetails = models.JSONField(blank=True, null=True)
    location_id=models.CharField(max_length=15, blank=True,default="hms")