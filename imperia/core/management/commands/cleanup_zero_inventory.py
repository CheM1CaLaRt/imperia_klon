# core/management/commands/cleanup_zero_inventory.py
from django.core.management.base import BaseCommand
from core.models import Inventory

class Command(BaseCommand):
    help = "Удаляет записи Inventory с quantity = 0"

    def handle(self, *args, **options):
        n = Inventory.objects.filter(quantity=0).delete()[0]
        self.stdout.write(self.style.SUCCESS(f"Удалено позиций с нулём: {n}"))
