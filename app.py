"""WSGI entry point for hosting (Render, gunicorn):   gunicorn app:app

On your own computer use run.py or launch.py instead. A hosted copy has no login: anyone with the link can see and change its data,
so only ever load the demo data into it, never a real statement.
"""
from hisaab import config
from hisaab.web.app import create_app

config.ensure_dirs()
app = create_app()
