"""
Settings package initialization - auto-selects settings module based on ENV
"""
import os

# Default to local settings if not specified
settings_module = os.getenv('DJANGO_SETTINGS_MODULE', 'distributed_job_scheduler.settings.local')

# This allows importing settings directly
from importlib import import_module
_settings = import_module(settings_module)

# Export all settings
for _setting in dir(_settings):
    if _setting.isupper():
        globals()[_setting] = getattr(_settings, _setting)