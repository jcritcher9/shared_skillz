"""WSGI config for the EasyImports web app."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "easyimports_web.settings")

application = get_wsgi_application()
