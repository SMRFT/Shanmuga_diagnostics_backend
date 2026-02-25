import os
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings") # Adjust if settings are elsewhere (e.g., Shanmuga_diagnostics_backend.settings)
os.environ['DJANGO_SETTINGS_MODULE'] = 'Shanmuga_diagnostics_backend.settings' # Let's assume this based on typical Django. I will actually just use python manage.py shell.
