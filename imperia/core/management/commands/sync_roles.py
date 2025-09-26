from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from core.models import Product

class Command(BaseCommand):
    help = "Sync: view_product for Director/Operator/Manager/Warehouse; full access only Director"

    def handle(self, *args, **options):
        ct = ContentType.objects.get_for_model(Product)
        view_p   = Permission.objects.get(codename="view_product",   content_type=ct)
        add_p    = Permission.objects.get(codename="add_product",    content_type=ct)
        change_p = Permission.objects.get(codename="change_product", content_type=ct)
        delete_p = Permission.objects.get(codename="delete_product", content_type=ct)

        director, _ = Group.objects.get_or_create(name="director")
        operator, _ = Group.objects.get_or_create(name="operator")
        manager,  _ = Group.objects.get_or_create(name="manager")
        warehouse,_ = Group.objects.get_or_create(name="warehouse")

        # Просмотр всем
        for g in (director, operator, manager, warehouse):
            g.permissions.add(view_p)

        # Полные права только директору
        director.permissions.add(add_p, change_p, delete_p)

        # Забираем лишнее у остальных
        for g in (operator, manager, warehouse):
            g.permissions.remove(add_p, change_p, delete_p)

        self.stdout.write(self.style.SUCCESS("Synced permissions."))
