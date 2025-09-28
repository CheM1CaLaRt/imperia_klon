# core/management/commands/fix_inventory_duplicates.py
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, Sum
from core.models import Inventory

class Command(BaseCommand):
    help = "Консолидирует дубли Inventory по (warehouse, product, bin)"

    def add_arguments(self, parser):
        parser.add_argument("--delete-zeros", action="store_true",
                            help="Дополнительно удалить строки с количеством 0")

    def handle(self, *args, **opts):
        dups = (
            Inventory.objects
            .values("warehouse_id", "product_id", "bin_id")
            .annotate(cnt=Count("id"), total=Sum("quantity"))
            .filter(cnt__gt=1)
        )

        fixed_groups = 0
        deleted_rows = 0

        with transaction.atomic():
            for g in dups:
                w_id, p_id, b_id = g["warehouse_id"], g["product_id"], g["bin_id"]
                total = g["total"] or Decimal("0")

                qs = (Inventory.objects
                      .select_for_update()
                      .filter(warehouse_id=w_id, product_id=p_id, bin_id=b_id)
                      .order_by("pk"))

                keep = qs.first()
                others = list(qs.exclude(pk=keep.pk))
                keep.quantity = total
                keep.save(update_fields=["quantity", "updated_at"])

                deleted_rows += Inventory.objects.filter(
                    pk__in=[o.pk for o in others]
                ).delete()[0]
                fixed_groups += 1

            if opts["delete_zeros"]:
                from decimal import Decimal
                deleted_rows += Inventory.objects.filter(quantity=Decimal("0")).delete()[0]

        self.stdout.write(self.style.SUCCESS(
            f"Групп объединено: {fixed_groups}, строк удалено: {deleted_rows}"
        ))
