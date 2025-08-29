# core/management/commands/ensure_roles.py
from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group

ROLES = ["warehouse", "operator", "manager", "director"]

class Command(BaseCommand):
    help = "Создаёт базовые роли (группы), если их ещё нет."

    def handle(self, *args, **kwargs):
        created = []
        for name in ROLES:
            obj, was_created = Group.objects.get_or_create(name=name)
            if was_created:
                created.append(name)
        if created:
            self.stdout.write(self.style.SUCCESS(f"Созданы группы: {', '.join(created)}"))
        else:
            self.stdout.write("Все группы уже существуют.")
