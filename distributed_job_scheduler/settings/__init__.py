"""
Settings package initialization - auto-selects settings module based on ENV
"""
import os

# Default to local settings if not specified
settings_module = os.getenv('DJANGO_SETTINGS_MODULE', 'distributed_job_scheduler.settings.local')

# If the env var points to this package itself, fall back to local settings
# to avoid circular import (manage.py sets DJANGO_SETTINGS_MODULE='distributed_job_scheduler.settings')
if settings_module == 'distributed_job_scheduler.settings':
    settings_module = 'distributed_job_scheduler.settings.local'

from importlib import import_module
_settings = import_module(settings_module)

for _setting in dir(_settings):
    if _setting.isupper():
        globals()[_setting] = getattr(_settings, _setting)
