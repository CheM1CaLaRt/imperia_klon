# core/management/commands/init_roles.py
from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from core.models import Product, Supplier, ProductImage, ProductCertificate, ProductPrice

class Command(BaseCommand):
    help = "Создать группы director и warehouse с нужными правами"

    def handle(self, *args, **opts):
        director, _ = Group.objects.get_or_create(name="director")
        warehouse, _ = Group.objects.get_or_create(name="warehouse")

        # Полные права директору на core.*
        for model in [Product, Supplier, ProductImage, ProductCertificate, ProductPrice]:
            ct = ContentType.objects.get_for_model(model)
            perms = Permission.objects.filter(content_type=ct)
            director.permissions.add(*perms)

        # Складу — только view.*
        for model in [Product, Supplier, ProductImage, ProductCertificate, ProductPrice]:
            ct = ContentType.objects.get_for_model(model)
            view_perm = Permission.objects.get(content_type=ct, codename=f"view_{model._meta.model_name}")
            warehouse.permissions.add(view_perm)

        self.stdout.write(self.style.SUCCESS("Группы настроены."))
