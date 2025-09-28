from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError
from core.models import Warehouse, StorageBin, Inventory, Product, StockMovement


@transaction.atomic
def put_away(*, warehouse: Warehouse, product: Product,
             bin_to: StorageBin | None, qty: Decimal,
             actor=None, note: str = ""):
    if qty <= 0:
        raise ValidationError("Количество должно быть > 0")

    inv, _ = Inventory.objects.select_for_update().get_or_create(
        warehouse=warehouse, bin=bin_to, product=product,
        defaults={"quantity": Decimal("0")}
    )
    inv.quantity += qty
    inv.save(update_fields=["quantity", "updated_at"])

    StockMovement.objects.create(
        movement_type=StockMovement.INBOUND, warehouse=warehouse,
        bin_to=bin_to, product=product, quantity=qty, actor=actor, note=note
    )


@transaction.atomic
def move_between_bins(*, warehouse: Warehouse, product: Product,
                      bin_from: StorageBin | None,
                      bin_to: StorageBin | None,
                      qty: Decimal,
                      actor=None, note: str = ""):
    if qty <= 0:
        raise ValidationError("Количество должно быть > 0")

    # источник
    src = (Inventory.objects.select_for_update()
           .filter(warehouse=warehouse, bin=bin_from, product=product)
           .first())
    if not src or src.quantity < qty:
        raise ValidationError("Недостаточно товара в исходной ячейке")

    src.quantity -= qty
    if src.quantity == 0:
        src.delete()                  # ← удаляем пустую позицию
    else:
        src.save(update_fields=["quantity", "updated_at"])

    # приёмник
    dst, _ = Inventory.objects.select_for_update().get_or_create(
        warehouse=warehouse, bin=bin_to, product=product,
        defaults={"quantity": Decimal("0")}
    )
    dst.quantity += qty
    dst.save(update_fields=["quantity", "updated_at"])

    StockMovement.objects.create(
        movement_type=StockMovement.MOVE, warehouse=warehouse,
        bin_from=bin_from, bin_to=bin_to, product=product,
        quantity=qty, actor=actor, note=note
    )

