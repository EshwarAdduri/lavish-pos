from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin (owner)"
        STAFF = "staff", "Staff"

    role = models.CharField(max_length=10, choices=Role.choices, default=Role.STAFF)
    display_name = models.CharField(max_length=60, blank=True)

    class Meta:
        ordering = ["username"]

    def __str__(self):
        return self.display_name or self.get_full_name() or self.username

    @property
    def is_owner(self) -> bool:
        """Admins (and Django superusers) get full access."""
        return self.role == self.Role.ADMIN or self.is_superuser
