"""
Create the owner (admin) account from environment variables, if it doesn't exist yet.

    OWNER_USERNAME=owner OWNER_PASSWORD='a-strong-password' python manage.py ensure_owner

Used on the server where there is no terminal. Never changes an existing password.
"""

import os

from django.core.management.base import BaseCommand

from accounts.models import User


class Command(BaseCommand):
    help = "Create the owner account from OWNER_USERNAME / OWNER_PASSWORD if missing."

    def handle(self, *args, **opts):
        username = os.getenv("OWNER_USERNAME", "").strip()
        password = os.getenv("OWNER_PASSWORD", "")
        if not username or not password:
            self.stdout.write("OWNER_USERNAME / OWNER_PASSWORD not set — skipping.")
            return
        if User.objects.filter(username=username).exists():
            self.stdout.write(f"Owner '{username}' already exists.")
            return
        if len(password) < 8:
            self.stderr.write("OWNER_PASSWORD must be at least 8 characters.")
            return
        User.objects.create_superuser(username=username, password=password, role=User.Role.ADMIN, display_name="Owner")
        self.stdout.write(self.style.SUCCESS(f"Owner '{username}' created."))
