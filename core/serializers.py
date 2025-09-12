from rest_framework import serializers
from bson import ObjectId

class ObjectIdField(serializers.Field):
    def to_representation(self, value):
        return str(value)
    def to_internal_value(self, data):
        return ObjectId(data)
    
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
