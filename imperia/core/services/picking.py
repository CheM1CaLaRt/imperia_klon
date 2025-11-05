from decimal import Decimal
from typing import Dict, List, Tuple

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from core.models import Product, Inventory, StockMovement
from core.models_pick import PickList, PickItem

# безопасный маппинг констант движения: вернёт строки "IN", "MOVE", "OUT"
def _movement_const():
    return {"IN": "IN", "MOVE": "MOVE", "OUT": "OUT"}

def lookup_by_barcode(barcode: str) -> dict:
    barcode = (barcode or "").strip()
    if not barcode:
        raise ValidationError("Пустой штрихкод.")

    try:
        product = Product.objects.get(barcode=barcode)
    except Product.DoesNotExist:
        raise ValidationError(f"Товар с штрихкодом {barcode} не найден.")

    inv_qs = (
        Inventory.objects
        .filter(product=product, quantity__gt=0)
        .select_related("warehouse", "bin")  # ✅ исправлено
    )

    if not inv_qs.exists():
        raise ValidationError(f"Товар {product.name} отсутствует на складе.")

    inv = inv_qs.first()
    return {
        "name": product.name,
        "location": f"{inv.warehouse.code}/{inv.bin.code if inv.bin else '—'}",
        "unit": "шт",
        "qty_on_hand": inv.quantity,
    }

@transaction.atomic
def reserve_for_picking(*, request_obj, lines: List[Dict], actor) -> PickList:
    """
    Проверяем суммарный остаток по каждой позиции, списываем из ячеек (от больших к меньшим),
    создаём PickList/PickItem и движение OUT. Цены не участвуют.
    lines = [{"barcode": "...", "qty": 1.5, "unit": "шт"}, ...]
    """
    if not lines:
        raise ValidationError("Добавьте хотя бы одну строку.")

    normalized: List[Tuple[Product, Decimal, str, str]] = []
    for ln in lines:
        bc = (ln.get("barcode") or "").strip()
        if not bc:
            raise ValidationError("Пустой штрихкод.")
        try:
            qty = Decimal(str(ln.get("qty"))).quantize(Decimal("0.001"))
        except Exception:
            raise ValidationError("Некорректное количество.")
        if qty <= 0:
            raise ValidationError("Количество должно быть > 0.")

        # найдём товар
        product = Product.objects.filter(barcode=bc).first()
        if not product:
            raise ValidationError(f"Товар {bc} не найден.")

        # блокируем остатки этого товара и проверяем сумму
        inv_qs = (Inventory.objects
                  .select_for_update()
                  .filter(product=product, quantity__gt=0)
                  .select_related("bin")
                  .order_by("-quantity"))
        total = sum((r.quantity for r in inv_qs), Decimal("0"))
        if total < qty:
            raise ValidationError(f"Недостаточно на складе: {product.name} (есть {total}, нужно {qty}).")

        best_loc = inv_qs.first().bin.code if inv_qs and inv_qs.first().bin else ""
        normalized.append((product, qty, (ln.get("unit") or "шт"), best_loc))

    pl = PickList.objects.create(request=request_obj, created_by=actor, status="submitted")

    const = _movement_const()
    mov_out = const["OUT"]

    for product, qty, unit, location_hint in normalized:
        need = qty
        inv_qs = (Inventory.objects
                  .select_for_update()
                  .filter(product=product, quantity__gt=0)
                  .select_related("bin")
                  .order_by("-quantity"))
        for inv in inv_qs:
            if need <= 0:
                break
            take = min(inv.quantity, need)
            inv.quantity = inv.quantity - take
            if inv.quantity == 0:
                inv.delete()
            else:
                inv.save(update_fields=["quantity", "updated_at"])

            # движение OUT (склад обязателен; берём из записи инвентаря)
            StockMovement.objects.create(
                movement_type=mov_out,
                warehouse=inv.warehouse,
                bin_from=inv.bin,
                bin_to=None,
                product=product,
                quantity=take,
                actor=actor,
                note=f"Заявка #{getattr(request_obj, 'pk', '')}: бронь под сборку",
            )
            need -= take

        PickItem.objects.create(
            picklist=pl,
            product_id=product.id,
            barcode=product.barcode or "",
            name=product.name or "",
            location=location_hint,
            unit=unit or "шт",
            qty=qty,
        )

    return pl
