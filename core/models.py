from django.db import models
from bson import ObjectId  
from django.utils import timezone
import json
from django.db.models import Max


class AuditModel(models.Model):
    created_by = models.CharField(max_length=100, blank=True, null=True)
    created_date = models.DateTimeField(auto_now_add=True)
    lastmodified_by = models.CharField(max_length=100, blank=True, null=True)
    lastmodified_date = models.DateTimeField(blank=True, null=True)
    class Meta:
        abstract = True


class Appointment(AuditModel):
    GENDER_CHOICES = [
        ("Male", "Male"),
        ("Female", "Female"),
        ("Other", "Other")
    ]

    appointment_date = models.DateField()
    patient_name = models.CharField(max_length=150)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES)
    age = models.PositiveIntegerField()
    mobile_number = models.CharField(max_length=10)
    sample_collector = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(max_length=20, default="Active")
    appointment_id = models.IntegerField(primary_key=True)

    def save(self, *args, **kwargs):
        if self.appointment_id is None:
            last = Appointment.objects.order_by('-appointment_id').first()
            self.appointment_id = (last.appointment_id + 1) if last and last.appointment_id else 1
        super(Appointment, self).save(*args, **kwargs)

    def __str__(self):
        return f"{self.patient_name} - {self.appointment_date}"    
    

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
    bill_no = models.CharField(max_length=20, blank=True)
    testdetails = models.JSONField(blank=True, null=True)
    totalAmount = models.CharField(max_length=50, blank=True)
    netAmount = models.CharField(max_length=50, blank=True)
    discount = models.CharField(max_length=50, blank=True)
    payment_method = models.JSONField(blank=True, null=True)
    MultiplePayment = models.JSONField(blank=True, null=True)
    is_emergency = models.BooleanField(default=False)
    patient_history = models.CharField(max_length=100, blank=True, null=True)
    prescription_file_id = models.CharField(max_length=255, blank=True, null=True)
    credit_amount = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, default="Registered")
    order_id = models.CharField(max_length=100, blank=True, null=True)
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
    # Reject fields
    rejected_id = models.CharField(max_length=50, blank=True, null=True)
    rejected_date = models.DateTimeField(null=True, blank=True)
    rejected_reason = models.TextField(blank=True, null=True)

    APPROVAL_STAGES = (
        ('PENDING_APPROVAL', 'Pending Approval'),
        ('PENDING_FINAL', 'Pending Final Approval'),
        ('APPROVED', 'Fully Approved'),
        ('REJECTED', 'Rejected'),
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
    sample_collector = models.CharField(max_length=50, blank=True)
    date = models.DateField()
    bill_no= models.CharField(max_length=50, primary_key=True,unique=True)
    barcode= models.CharField(max_length=50)
    testdetails = models.JSONField()  # Store tests as a list of dictionaries
    is_emergency = models.BooleanField(default=False)
    patient_history = models.CharField(max_length=100, blank=True)
    def __str__(self):
        return f"{self.patientname} - {self.patient_id}"
    
    
class SampleStatus(AuditModel):
    patient_id = models.CharField(max_length=100)    
    barcode= models.CharField(max_length=50)   
    date = models.DateTimeField(null=True, blank=True)  # Use DateTimeField to store both date and time
    testdetails = models.JSONField()  # Assuming you're using Django 3.1+ for JSONField
    def __str__(self):
        return self.patient_id
    

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
    visit_image_id = models.CharField(max_length=255, blank=True, null=True)
    latitude = models.CharField(max_length=50, blank=True, null=True)
    longitude = models.CharField(max_length=50, blank=True, null=True)


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

class Logistics(AuditModel):
    task_id = models.IntegerField(primary_key=True)
    date = models.DateField()
    sample_collector = models.CharField(max_length=255)
    clinicalname = models.CharField(max_length=255)
    sales_person = models.CharField(max_length=255)
    sampleordertime = models.CharField(max_length=255)
    sampleacceptedtime = models.CharField(max_length=255, blank=True, null=True)
    samplepickeduptime = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(max_length=255, blank=True, null=True)
    remarks = models.TextField(blank=True, null=True)
    reassigned_to = models.CharField(max_length=255, blank=True, null=True)

    def save(self, *args, **kwargs):
        if self.task_id is None:
            last = Logistics.objects.order_by('-task_id').first()
            self.task_id = (last.task_id + 1) if last else 1
        super().save(*args, **kwargs)
    

class SampleCollectorLocation(models.Model):
    location_id = models.IntegerField(primary_key=True)
    sampleCollector = models.CharField(max_length=255)
    date = models.DateField()
    startTime = models.DateTimeField(null=True, blank=True)
    endTime = models.DateTimeField(null=True, blank=True)
    is_location_active = models.IntegerField(default=1) # 1 = Active, 0 = Inactive
    location_history = models.JSONField(default=list, blank=True)  # To store multiple points as a list
    distance_travelled = models.CharField(max_length=255, null=True, blank=True)

    def save(self, *args, **kwargs):
        if self.location_id is None:
            last = SampleCollectorLocation.objects.order_by('-location_id').first()
            self.location_id = (last.location_id + 1) if last else 1
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.sampleCollector} - {self.date} - ID: {self.location_id}"

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
    IPOPType=models.CharField(max_length=15, blank=True)
    date=models.DateTimeField()
    ref_doctor= models.CharField(max_length=500, blank=True)
    testdetails = models.JSONField(blank=True, null=True)


class Hmsbarcode(AuditModel):
    patient_id = models.CharField(max_length=20,  blank=True)
    ipnumber= models.CharField(max_length=100,blank=True)
    patientname =models.CharField(max_length=50,  blank=True)
    patientname = models.CharField(max_length=100)
    age = models.IntegerField()
    age_type = models.CharField(max_length=10, blank=True)
    billnumber= models.CharField(max_length=15, blank=True,primary_key=True)
    phone = models.CharField(max_length=15, blank=True)
    gender = models.CharField(max_length=10)
    barcode= models.CharField(max_length=50,  blank=True)
    IPOPType=models.CharField(max_length=15, blank=True)
    ref_doctor= models.CharField(max_length=500, blank=True)
    date=models.DateField()
    testdetails = models.JSONField(blank=True, null=True)
    location_id=models.CharField(max_length=15, blank=True,default="hms")


    
    
class Hmssamplestatus(AuditModel):
    barcode= models.CharField(max_length=50,  blank=True)
    date=models.DateField()
    testdetails = models.JSONField(blank=True, null=True)
    location_id=models.CharField(max_length=15, blank=True,default="hms")


class CommunicationLog(AuditModel):
    patient_id = models.CharField(max_length=50, blank=True, null=True)
    patient_name = models.CharField(max_length=255, blank=True, null=True)
    type = models.CharField(max_length=20) # 'Email' or 'WhatsApp'
    recipient = models.CharField(max_length=255) # Phone or Email
    status = models.CharField(max_length=50) # 'Success', 'Failed'
    details = models.TextField(blank=True, null=True) # Error message or success details
    
    def __str__(self):
        return f"{self.type} to {self.recipient}"
    
class MBTestValue(AuditModel):
    _id = models.CharField(max_length=50, primary_key=True)  
    date = models.DateField()
    barcode= models.CharField(max_length=50)
    locationId= models.CharField(max_length=50)
    is_preliminary= models.BooleanField(default=False)
    testdetails = models.JSONField()  # Store all test details in JSON format   
    def save(self, *args, **kwargs):
        if not self._id:
            self._id = str(ObjectId())  # Convert ObjectId to string
        super().save(*args, **kwargs)


from bson import ObjectId
from django.db import models

class RouteSetup(AuditModel):
    id = models.IntegerField(primary_key=True)
    route_name = models.CharField(max_length=255)
    logistics_mapping = models.CharField(max_length=255)
    start_time = models.TimeField()
    end_time = models.TimeField()
    processing_lab = models.CharField(max_length=255, default="Shanmuga Mother Lab")
    clinical_name = models.JSONField(default=list)  # must be a real Mongo array

    def save(self, *args, **kwargs):
        if self.id is None:
            last = RouteSetup.objects.order_by('-id').first()
            self.id = (last.id + 1) if last else 1
        super().save(*args, **kwargs)

    def __str__(self):
        return self.route_name


class RouteAnalysis(AuditModel):
    STATUS_CHOICES = (
        ("in_progress", "In Progress"),
        ("completed", "Completed"),
    )

    id = models.CharField(max_length=50, primary_key=True)  # ObjectId string, matches _id
    route_id = models.IntegerField()  # plain FK value, not a Django ForeignKey
    logistics_mapping = models.CharField(max_length=255)
    start_time = models.DateTimeField(null=True, blank=True)
    end_time = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="in_progress")
    visits = models.JSONField(default=list)

    def save(self, *args, **kwargs):
        if not self.id:
            self.id = str(ObjectId())
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Route {self.route_id} - {self.start_time}"


class B2BPackage(AuditModel):
    package_id = models.IntegerField(primary_key=True)
    packageName = models.CharField(max_length=255)
    referrerCode = models.CharField(max_length=255)
    mrptotal = models.CharField(max_length=50)
    l2ltotal = models.CharField(max_length=50)
    rate = models.CharField(max_length=50)
    testNames = models.JSONField(default=list)
    status = models.CharField(max_length=50, default="pending")
    approved_by = models.CharField(max_length=255, null=True, blank=True)
    approved_date = models.DateTimeField(null=True, blank=True)
    rejected_by = models.CharField(max_length=255, null=True, blank=True)
    rejected_date = models.DateTimeField(null=True, blank=True)
    rejected_Reason = models.TextField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if self.package_id is None:
            last = B2BPackage.objects.order_by('-package_id').first()
            self.package_id = (last.package_id + 1) if last else 1
        super().save(*args, **kwargs)

    def __str__(self):
        return self.packageName
    


from django.db import models
from django.db import transaction

class Busfare(AuditModel):
    busfare_id = models.IntegerField(primary_key=True)

    date = models.DateField()
    location = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    collectedby = models.CharField(max_length=255)
    pickedupby = models.CharField(max_length=255, blank=True, null=True)
    pickuptime = models.TimeField()
    busreachedtime = models.TimeField()
    bustphoto = models.CharField(max_length=255, blank=True, null=True)

    def save(self, *args, **kwargs):
        if not self.busfare_id:
            with transaction.atomic():
                last = Busfare.objects.select_for_update().order_by('-busfare_id').first()
                self.busfare_id = (last.busfare_id + 1) if last else 1
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Busfare {self.busfare_id} - {self.amount}"



from django.db import models

class CustomerComplaint(AuditModel):
    complaint_id = models.IntegerField(primary_key=True)
    patient_id = models.CharField(max_length=255,blank=True, null=True)
    labcode  = models.CharField(max_length=255)
    issuetype = models.CharField(max_length=255)
    comments = models.TextField()
    assignedby = models.CharField(max_length=255,blank=True, null=True)
    completion_comments = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, default="pending") 
   
    def save(self, *args, **kwargs):
        if not self.complaint_id:
            last = CustomerComplaint.objects.order_by('-complaint_id').first()
            if last:
                self.complaint_id = last.complaint_id + 1
            else:
                self.complaint_id = 1
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.labname} - {self.status}"
    



# ---------------- shared rollup helpers ----------------

def get_week_of_year(d):
    """Return standard ISO week number (1-53), matching ISO calendar (Monday to Sunday)."""
    return d.isocalendar()[1]


def recompute_entries_and_rollups(entries, avg_revenue_per_prescription, year, month):
    """
    entries: [{'date': day, 'volume': v}, ...]
    Recomputes revenue on every entry, buckets into weekly totals, and
    returns (normalized_entries, weekly_totals, total_revenue).
    """
    normalized_entries = []
    weekly_map = {}
    total_revenue = 0.0

    for entry in entries:
        day = int(entry.get('date'))
        volume = float(entry.get('volume') or 0)
        revenue = volume * avg_revenue_per_prescription

        normalized_entries.append({'date': day, 'volume': volume, 'revenue': revenue})
        total_revenue += revenue

        week = get_week_of_year(date_cls(year, month, day))
        weekly_map[week] = weekly_map.get(week, 0) + revenue

    weekly_totals = [{'week': w, 'total': weekly_map[w]} for w in sorted(weekly_map)]
    return normalized_entries, weekly_totals, total_revenue


def recompute_total_row(category, month, year, actor_employee_id):
    """
    Remove any legacy aggregate row with employee_id='ALL' if it exists.
    Only real employee rows are now stored for sales plans; totals are
    computed from those employee rows when needed.
    """
    SalesPlan.objects.filter(
        employee_id=TOTAL_ROW_EMPLOYEE_ID,
        category=category,
        month=month,
        year=year,
    ).delete()
    return


class RawJSONField(models.JSONField):
    """
    Django's JSONField always runs json.dumps() in get_prep_value(),
    which djongo then persists as a plain string instead of a native
    BSON array/document. This override skips that step so lists/dicts
    are stored natively in Mongo, and defensively parses back to
    Python on read in case any existing rows were saved as strings.
    """
    def get_prep_value(self, value):
        return value
 
    def from_db_value(self, value, expression, connection):
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                return value
        return value
 
 
import math
from datetime import date as date_cls
from django.db import models
from django.db.models import Max

# Sentinel employee_id used for the one aggregate row per category/month/year
# that holds the overall (all-sales-executives) total and its weekly
# breakdown — lives in the same table as every executive's own row instead
# of a separate model.
TOTAL_ROW_EMPLOYEE_ID = "ALL"


class SalesPlan(AuditModel):

    sales_plan_id = models.IntegerField(primary_key=True)
    employee_id = models.CharField(max_length=100)
    category = models.CharField(max_length=100)
    month = models.IntegerField()
    year = models.IntegerField()
    date = models.DateTimeField(auto_now=True)

    # One-time-per-month values — entered once per employee/category/month/year,
    # not per day. Blank/0 on the aggregate row.
    working_days = models.IntegerField(default=0, blank=True, null=True)
    avg_revenue_per_prescription = models.FloatField(default=0, blank=True, null=True)

    # Each entry: {'date': <day int>, 'volume': <float>,
    #              'revenue': <float, volume * avg_revenue_per_prescription>}
    # On the aggregate row, entries stays empty — only weekly_totals/total_revenue
    # are populated there.
    entries = RawJSONField(default=list, blank=True)

    # Stored rollups, recomputed server-side on every POST/PATCH.
    # weekly_totals: [{'week': <int>, 'total': <float>}, ...]
    weekly_totals = RawJSONField(default=list, blank=True)
    total_revenue = models.FloatField(default=0, blank=True, null=True)

    def save(self, *args, **kwargs):
        if not self.sales_plan_id:
            last_id = SalesPlan.objects.aggregate(
                Max('sales_plan_id')
            )['sales_plan_id__max']
            self.sales_plan_id = (last_id + 1) if last_id else 1
        super().save(*args, **kwargs)





