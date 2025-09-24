# core/management/commands/import_samson.py
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Optional

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, models
from django.utils import timezone

from core.models import (
    Supplier,
    ImportBatch,
    Product,
    ProductImage,
    ProductCertificate,
    ProductPrice,
)

# ---------------------------
# Helpers
# ---------------------------

def detect_and_read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp1251", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise CommandError("Не удалось определить кодировку файла. Перекодируй в UTF-8.")

def safe_decimal(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None

def norm_barcode(s: Optional[str | int]) -> Optional[str]:
    if s is None:
        return None
    digits = re.sub(r"\D+", "", str(s))
    return digits or None

def looks_like_product(d: dict) -> bool:
    """Мини-критерий, что это карточка товара, а не контейнер."""
    keys = set(d.keys())
    return any(k in keys for k in ("sku", "name", "barcode", "vendor_code", "price_list", "photo_list"))

def extract_items(payload: Any) -> list[dict]:
    """
    Поддерживаем разные варианты корневой структуры.
    - массив карточек: [ {...}, ... ]
    - массив контейнеров: [ {"data":[...]}, {"data":[...]}, ... ]
    - объект с ключом data/items/products/result: { "data": [ ... ] }
    - единичный объект-товар: { "sku": ..., "name": ... }
    """
    # 1) Корень — список
    if isinstance(payload, list):
        flat: list[dict] = []
        for elem in payload:
            if isinstance(elem, dict):
                # контейнер с массивом внутри?
                for key in ("data", "items", "products", "result"):
                    if key in elem and isinstance(elem[key], list):
                        flat.extend([x for x in elem[key] if isinstance(x, dict)])
                        break
                else:
                    # это уже карточка товара?
                    if looks_like_product(elem):
                        flat.append(elem)
                    # иначе игнорируем мусорные элементы
            # если не dict — игнор
        if flat:
            return flat
        # если ничего не собрали, но список — может это уже карточки?
        return [x for x in payload if isinstance(x, dict) and looks_like_product(x)]

    # 2) Корень — объект
    if isinstance(payload, dict):
        for key in ("data", "items", "products", "result"):
            maybe = payload.get(key)
            if isinstance(maybe, list):
                return [x for x in maybe if isinstance(x, dict)]
        if looks_like_product(payload):
            return [payload]

    raise CommandError("Не найден список товаров в JSON (ожидается массив карточек).")

def get_pkg_size(row: dict, key: str) -> Optional[Any]:
    block = row.get("package_size")
    if isinstance(block, list):
        for item in block:
            if isinstance(item, dict) and item.get("type") == key:
                return item.get("value")
    elif isinstance(block, dict):
        return block.get(key)
    return None

def iter_urls(values: Iterable[Any]) -> Iterable[str]:
    for v in values or []:
        if isinstance(v, str) and v.strip():
            yield v.strip()

def get_sku(row: dict, barcode: Optional[str]) -> Optional[str]:
    """Ищем артикул в разных полях. Если нет — генерим от штрихкода."""
    candidates = [
        "sku",
        "sku_id",
        "item_sku",
        "code",
        "id",
        "product_id",
        "vendor_code",  # иногда поставщики кладут артикул сюда
    ]
    for key in candidates:
        val = row.get(key)
        if val is not None and str(val).strip() != "":
            return str(val).strip()
    if barcode:
        return f"BC-{barcode}"
    return None

# ---------------------------
# Command
# ---------------------------

class Command(BaseCommand):
    help = "Импорт Самсон: названия, описания, фото, штрихкод, цены, габариты, страна, сертификаты."

    def add_arguments(self, parser):
        parser.add_argument("json_path", type=str, help="Путь к JSON-файлу Самсона")
        parser.add_argument("--supplier", default="samson", help="Код поставщика (по умолчанию 'samson')")
        parser.add_argument("--source-name", default="", help="Имя источника (например, имя файла)")
        parser.add_argument("--currency", default="RUB", help="Код валюты для цен (по умолчанию RUB)")
        parser.add_argument("--limit", type=int, default=0, help="Ограничить число импортируемых позиций (для теста)")

    @transaction.atomic
    def handle(self, *args, **opts):
        json_path = Path(opts["json_path"])
        if not json_path.exists():
            raise CommandError(f"Файл не найден: {json_path}")

        supplier_code = opts["supplier"]
        currency = opts["currency"]
        limit = int(opts.get("limit") or 0)

        supplier, _ = Supplier.objects.get_or_create(code=supplier_code, defaults={"name": supplier_code.title()})
        batch = ImportBatch.objects.create(
            supplier=supplier,
            source_name=opts.get("source_name") or json_path.name,
        )

        text = detect_and_read_text(json_path)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as e:
            raise CommandError(f"Некорректный JSON: {e}")

        items = extract_items(payload)
        if limit > 0:
            items = items[:limit]

        batch.items_total = len(items)

        upserted = 0
        created_count = 0
        updated_count = 0
        skipped_empty_ident = 0  # нет sku и нет barcode
        skipped_total = 0

        for idx, row in enumerate(items, start=1):
            if not isinstance(row, dict):
                skipped_total += 1
                continue

            barcode = norm_barcode(row.get("barcode"))
            sku = get_sku(row, barcode)

            if not sku:
                skipped_total += 1
                skipped_empty_ident += 1
                continue

            name = row.get("name") or ""
            name_1c = row.get("name_1c") or ""
            description = row.get("description") or ""
            description_ext = row.get("description_ext") or ""
            brand = row.get("brand") or ""
            vendor_code = row.get("vendor_code") or ""
            manufacturer = row.get("manufacturer") or ""

            weight = safe_decimal(row.get("weight"))
            volume = safe_decimal(row.get("volume"))

            h = safe_decimal(get_pkg_size(row, "height"))
            w = safe_decimal(get_pkg_size(row, "width"))
            d = safe_decimal(get_pkg_size(row, "depth"))

            # Поиск существующей записи
            product = None
            if barcode:
                product = Product.objects.filter(barcode=barcode).first()
            if product is None:
                product = (
                    Product.objects
                    .filter(supplier=supplier, sku=sku)
                    .filter(models.Q(barcode__isnull=True) | models.Q(barcode__exact=""))
                    .first()
                )

            if product is None:
                product = Product.objects.create(
                    supplier=supplier,
                    sku=sku,
                    barcode=barcode,  # может быть None/пусто
                    name=name,
                    name_1c=name_1c,
                    description=description,
                    description_ext=description_ext,
                    brand=brand,
                    vendor_code=vendor_code,
                    manufacturer_country=manufacturer,
                    weight_kg=weight,
                    volume_m3=volume,
                    pkg_height_cm=h,
                    pkg_width_cm=w,
                    pkg_depth_cm=d,
                    last_import_batch=batch,
                )
                created_count += 1
                upserted += 1
            else:
                changed = False
                fields_to_update = {
                    "supplier": supplier,
                    "sku": sku,
                    "name": name,
                    "name_1c": name_1c,
                    "description": description,
                    "description_ext": description_ext,
                    "brand": brand,
                    "vendor_code": vendor_code,
                    "manufacturer_country": manufacturer,
                    "weight_kg": weight,
                    "volume_m3": volume,
                    "pkg_height_cm": h,
                    "pkg_width_cm": w,
                    "pkg_depth_cm": d,
                    "last_import_batch": batch,
                }
                for attr, val in fields_to_update.items():
                    if getattr(product, attr) != val:
                        setattr(product, attr, val)
                        changed = True
                if barcode and product.barcode != barcode:
                    product.barcode = barcode
                    changed = True
                if changed:
                    product.save()
                    updated_count += 1
                    upserted += 1

            # Фото
            ProductImage.objects.filter(product=product).delete()
            for pos, url in enumerate(iter_urls(row.get("photo_list")), start=0):
                ProductImage.objects.create(product=product, url=url, position=pos)

            # Сертификаты
            ProductCertificate.objects.filter(product=product).delete()
            ext_certs = row.get("certificate_extended_list") or []
            if isinstance(ext_certs, list) and ext_certs:
                for block in ext_certs:
                    if not isinstance(block, dict):
                        continue
                    issued_by = (block.get("issued_by") or "").strip()
                    cert_name = (block.get("name") or "").strip()
                    active_to = (block.get("active_to") or "").strip()
                    for curl in iter_urls(block.get("url_list")):
                        ProductCertificate.objects.create(
                            product=product,
                            issued_by=issued_by,
                            name=cert_name,
                            active_to=active_to,
                            url=curl,
                        )
            else:
                for curl in iter_urls(row.get("certificate_list")):
                    ProductCertificate.objects.create(product=product, url=curl)

            # Цены
            price_list = row.get("price_list") or []
            if isinstance(price_list, list):
                for price in price_list:
                    if not isinstance(price, dict):
                        continue
                    ptype = (price.get("type") or ProductPrice.OTHER).strip() or ProductPrice.OTHER
                    value = safe_decimal(price.get("value"))
                    if value is None:
                        continue
                    ProductPrice.objects.update_or_create(
                        product=product, price_type=ptype,
                        defaults={"value": value, "currency": currency},
                    )

            if idx % 500 == 0:
                self.stdout.write(f"[{idx}/{len(items)}] processed...")

        batch.items_upserted = upserted
        batch.finished_at = timezone.now()
        batch.save()

        self.stdout.write(self.style.SUCCESS(
            "Импорт завершён: "
            f"всего={batch.items_total}, "
            f"создано={created_count}, обновлено={updated_count}, "
            f"пропущено={batch.items_total - (created_count + updated_count)} "
            f"(без идентификаторов sku/barcode: {skipped_empty_ident})"
        ))
