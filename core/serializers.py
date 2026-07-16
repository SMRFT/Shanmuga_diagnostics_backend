from rest_framework import serializers
from bson import ObjectId
class ObjectIdField(serializers.Field):
    def to_representation(self, value):
        return str(value)
    def to_internal_value(self, data):
        return ObjectId(data)


from .models import Appointment
class AppointmentSerializer(serializers.ModelSerializer):
    appointment_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = Appointment
        fields = '__all__'

    
from .models import Patient
class PatientSerializer(serializers.ModelSerializer):
    id = ObjectIdField(read_only=True)
    class Meta:
        model = Patient
        fields = '__all__'


from .models import Billing
class BillingSerializer(serializers.ModelSerializer):
    id = ObjectIdField(read_only=True)
    patientname = serializers.SerializerMethodField()

    class Meta:
        model = Billing
        fields = '__all__'  # keeps everything in Billing + patientname

    def get_patientname(self, obj):
        if not obj.patient_id:
            return None
        try:
            patient = Patient.objects.filter(patient_id=obj.patient_id).first()
            return patient.patientname if patient else None
        except Patient.DoesNotExist:
            return None


from .models import ClinicalName
class ClinicalNameSerializer(serializers.ModelSerializer):
    id = ObjectIdField(read_only=True)
    class Meta:
        model = ClinicalName
        fields = '__all__'


from .models import RefBy
class RefBySerializer(serializers.ModelSerializer):
    id = ObjectIdField(read_only=True)
    class Meta:
        model = RefBy
        fields = '__all__'


from .models import SampleStatus, TestValue
class SampleStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = SampleStatus
        fields = '__all__'  # Include all fields from the model

class TestValueSerializer(serializers.ModelSerializer):
    testdetails = serializers.JSONField()  # Store multiple test details as JSON
    class Meta:
        model = TestValue  # Replace with your model name
        fields = ['patient_id', 'patientname', 'age', 'date', 'testdetails']


from .models import SampleCollector
class SampleCollectorSerializer(serializers.ModelSerializer):
    id = ObjectIdField(read_only=True)
    class Meta:
        model = SampleCollector
        fields = '__all__'


#HMS PART
from .models import HmspatientBilling
class HmspatientBillingRegistrationSerializer(serializers.ModelSerializer):
    id = ObjectIdField(read_only=True)
    class Meta:
        model = HmspatientBilling
        fields = '__all__'


from .models import Hmsbarcode
class HmsbarcodeRegistrationSerializer(serializers.ModelSerializer):
    id = ObjectIdField(read_only=True)
    class Meta:
        model = Hmsbarcode
        fields = '__all__'


from .models import Hmssamplestatus
class HmssamplestatusSerializer(serializers.ModelSerializer):
    id = ObjectIdField(read_only=True)
    class Meta:
        model = Hmssamplestatus
        fields = '__all__'


from .models import HospitalLab
class HospitalLabSerializer(serializers.ModelSerializer):
    class Meta:
        model = HospitalLab
        fields = '__all__'


from .models import SalesVisitLog
class SalesVisitLogSerializer(serializers.ModelSerializer):
    id = ObjectIdField(read_only=True)
    class Meta:
        model = SalesVisitLog
        fields = "__all__"


from .models import Logistics
class LogisticsSerializer(serializers.ModelSerializer):
    task_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = Logistics
        fields = "__all__"


from .models import RouteSetup, RouteAnalysis, B2BPackage

class RouteSetupSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(read_only=True)
    clinical_name_display = serializers.SerializerMethodField()

    class Meta:
        model = RouteSetup
        fields = "__all__"

    def get_clinical_name_display(self, obj):
        from .Views.logistic import get_clinical_name_map, _as_list
        # clinical_name may be stored as a JSON string by djongo — parse defensively
        codes = _as_list(obj.clinical_name) if obj.clinical_name else []
        if not codes:
            return []
        name_map = get_clinical_name_map(codes)
        return [{"referrerCode": c, "clinicalname": name_map.get(c)} for c in codes]


class RouteAnalysisSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    route_name = serializers.SerializerMethodField()

    class Meta:
        model = RouteAnalysis
        fields = "__all__"

    def get_route_name(self, obj):
        route = RouteSetup.objects.filter(id=obj.route_id).first()
        return route.route_name if route else None

from .models import B2BPackage

class B2BPackageSerializer(serializers.ModelSerializer):
    package_id = serializers.IntegerField(read_only=True)
    class Meta:
        model = B2BPackage
        fields = "__all__"

from .models import Busfare
class BusfareSerializer(serializers.ModelSerializer):
    busfare_id = serializers.IntegerField(read_only=True)
    class Meta:
        model = Busfare
        fields = "__all__"




from .models import CustomerComplaint
class CustomerComplaintSerializer(serializers.ModelSerializer):
    complaint_id= serializers.IntegerField(read_only=True)
    class Meta:
        model = CustomerComplaint
        fields = "__all__"



from rest_framework import serializers
from .models import SalesPlan


class SalesPlanSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    sales_plan_id = serializers.IntegerField(read_only=True)  # auto-assigned in model.save()

    class Meta:
        model = SalesPlan
        fields = '__all__'