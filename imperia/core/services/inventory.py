from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError
from core.models import Warehouse, StorageBin, Inventory, StockMovement, Product


@transaction.atomic
def put_away(
    *,
    warehouse: Warehouse,
    bin_code: str,
    barcode: str,
    qty: Decimal,
    actor=None,
    create_bin_if_missing: bool = True,
    note: str = ""
):
    if qty <= 0:
        raise ValidationError("Количество должно быть > 0")

    product = Product.objects.select_for_update().get(barcode=barcode)

    bin_obj = None
    if bin_code:
        try:
            bin_obj = StorageBin.objects.select_for_update().get(
                warehouse=warehouse, code=bin_code
            )
        except StorageBin.DoesNotExist:
            if create_bin_if_missing:
                bin_obj = StorageBin.objects.create(
                    warehouse=warehouse, code=bin_code, is_active=True
                )
            else:
                raise ValidationError("Ячейка не найдена")

    inv, _ = Inventory.objects.select_for_update().get_or_create(
        warehouse=warehouse,
        bin=bin_obj,
        product=product,
        defaults={"quantity": Decimal("0")},
    )
    inv.quantity = inv.quantity + qty
    inv.save()

    StockMovement.objects.create(
        movement_type=StockMovement.INBOUND,
        warehouse=warehouse,
        bin_to=bin_obj,
        product=product,
        quantity=qty,
        actor=actor,
        note=note,
    )
    return inv


@transaction.atomic
def move_between_bins(
    *,
    warehouse: Warehouse,
    barcode: str,
    qty: Decimal,
    bin_from_code: str,
    bin_to_code: str,
    actor=None,
    create_bin_if_missing: bool = True,
    note: str = ""
):
    if qty <= 0:
        raise ValidationError("Количество должно быть > 0")

    product = Product.objects.select_for_update().get(barcode=barcode)
    bin_from = StorageBin.objects.select_for_update().get(
        warehouse=warehouse, code=bin_from_code
    )
    try:
        bin_to = StorageBin.objects.select_for_update().get(
            warehouse=warehouse, code=bin_to_code
        )
    except StorageBin.DoesNotExist:
        if create_bin_if_missing:
            bin_to = StorageBin.objects.create(
                warehouse=warehouse, code=bin_to_code, is_active=True
            )
        else:
            raise ValidationError("Ячейка-получатель не найдена")

    inv_from = Inventory.objects.select_for_update().get(
        warehouse=warehouse, bin=bin_from, product=product
    )
    if inv_from.quantity < qty:
        raise ValidationError("Недостаточно товара в ячейке-источнике")
    inv_from.quantity -= qty
    inv_from.save()

    inv_to, _ = Inventory.objects.select_for_update().get_or_create(
        warehouse=warehouse, bin=bin_to, product=product, defaults={"quantity": Decimal("0")}
    )
    inv_to.quantity += qty
    inv_to.save()

    StockMovement.objects.create(
        movement_type=StockMovement.MOVE,
        warehouse=warehouse,
        bin_from=bin_from,
        bin_to=bin_to,
        product=product,
        quantity=qty,
        actor=actor,
        note=note,
    )
    return inv_from, inv_to
