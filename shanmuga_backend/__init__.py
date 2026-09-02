import smtplib
import inspect

# Python 3.12 compatibility fix for Django 3.2 smtplib.SMTP.starttls (keyfile/certfile removed in Python 3.12)
_orig_starttls = smtplib.SMTP.starttls
if 'keyfile' not in inspect.signature(_orig_starttls).parameters:
    def _patched_starttls(self, keyfile=None, certfile=None, context=None):
        return _orig_starttls(self, context=context)
    smtplib.SMTP.starttls = _patched_starttls
