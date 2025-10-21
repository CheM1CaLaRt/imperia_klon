# core/management/commands/import_samson.py
from __future__ import annotations

import json
import re
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Optional

import requests
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

# ===== ЗАШИТЫЕ ПАРАМЕТРЫ ПО УМОЛЧАНИЮ =====
API_KEY = '71ff564120987ededde395364777a4c3'
DEFAULT_PER_PAGE = 100
DEFAULT_SLEEP_SEC = 0.2
DEFAULT_SUPPLIER_CODE = "samson"
DEFAULT_CURRENCY = "RUB"

# ===== HTTP / API =====
DEFAULT_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "samson-importer/1.0 (+django)",
    "Accept-Encoding": "gzip",
}
SAMSON_STOCK_URL = "https://api.samsonopt.ru/v1/sku/stock"
SAMSON_CARD_BASE = "https://api.samsonopt.ru/v1/sku/"

def http_get_json(url: str, params: dict[str, Any], headers: dict[str, str], timeout: float = 30.0) -> Any:
    resp = requests.get(url, params=params, headers=headers, timeout=timeout)
    if resp.status_code != 200:
        raise CommandError(f"HTTP {resp.status_code} for {url}: {resp.text[:500]}")
    try:
        return resp.json()
    except Exception as e:
        raise CommandError(f"JSON parse error for {url}: {e}\n{resp.text[:500]}")

def fetch_all_in_stock(api_key: str, per_page: int = 100, verbose: bool = True):
    """Генератор SKU с total>0 из /v1/sku/stock (с пагинацией)."""
    url = SAMSON_STOCK_URL
    params: dict[str, Any] = {"api_key": api_key, "limit": per_page}
    headers = DEFAULT_HEADERS.copy()
    page = 1
    while True:
        if verbose:
            print(f"[INFO] STOCK page {page}: {url}")
        payload = http_get_json(url, params=params, headers=headers)
        data_block = payload[0] if isinstance(payload, list) else payload
        items = (data_block or {}).get("data", []) or []
        if not items:
            break

        for item in items:
            sku = item.get("sku")
            stock_list = item.get("stock_list", []) or []
            total = sum(int(x.get("value", 0)) for x in stock_list if x.get("type") == "total")
            if sku and total > 0:
                yield str(sku)

        next_url = (data_block.get("meta", {}) or {}).get("pagination", {}).get("next")
        if not next_url:
            break
        url = next_url
        params = {"api_key": api_key}
        page += 1

def fetch_card(sku: str, api_key: str) -> Any:
    url = f"{SAMSON_CARD_BASE}{sku}"
    return http_get_json(url, params={"api_key": api_key}, headers=DEFAULT_HEADERS)

# ===== Helpers (ваша логика) =====
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
    keys = set(d.keys())
    return any(k in keys for k in ("sku", "name", "barcode", "vendor_code", "price_list", "photo_list"))

def extract_items(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        flat: list[dict] = []
        for elem in payload:
            if isinstance(elem, dict):
                for key in ("data", "items", "products", "result"):
                    if key in elem and isinstance(elem[key], list):
                        flat.extend([x for x in elem[key] if isinstance(x, dict)])
                        break
                else:
                    if looks_like_product(elem):
                        flat.append(elem)
        if flat:
            return flat
        return [x for x in payload if isinstance(x, dict) and looks_like_product(x)]
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
    for key in ["sku", "sku_id", "item_sku", "code", "id", "product_id", "vendor_code"]:
        val = row.get(key)
        if val is not None and str(val).strip() != "":
            return str(val).strip()
    if barcode:
        return f"BC-{barcode}"
    return None

# ===== Апсерт одной карточки =====
def upsert_product_row(row: dict, *, supplier, currency: str, batch) -> bool:
    if not isinstance(row, dict):
        return False

    barcode = norm_barcode(row.get("barcode"))
    sku = get_sku(row, barcode)
    if not sku:
        return False

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
            barcode=barcode,
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

    return True

# ===== Команда =====
class Command(BaseCommand):
    help = "Импорт Самсон: остатки -> карточки -> немедленный апсерт (ничего не сохраняет в JSON)."

    def add_arguments(self, parser):
        # Все аргументы опциональны — по умолчанию берем зашитые значения
        parser.add_argument("--from-file", dest="json_path", type=str, default="", help="Импорт из локального JSON (необязательно)")
        parser.add_argument("--api-key", dest="api_key", type=str, default=API_KEY, help="API ключ Samson")
        parser.add_argument("--per-page", type=int, default=DEFAULT_PER_PAGE, help="Размер страницы для /sku/stock")
        parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP_SEC, help="Пауза между карточками (сек)")
        parser.add_argument("--supplier", default=DEFAULT_SUPPLIER_CODE, help="Код поставщика")
        parser.add_argument("--currency", default=DEFAULT_CURRENCY, help="Валюта цен")
        parser.add_argument("--limit", type=int, default=0, help="Ограничить число позиций (для теста)")

    @transaction.atomic
    def handle(self, *args, **opts):
        # Настройки c возможностью переопределить флагами
        json_path_str: str = opts.get("json_path") or ""
        api_key: str = opts.get("api_key") or API_KEY
        per_page: int = int(opts.get("per_page") or DEFAULT_PER_PAGE)
        sleep_sec: float = float(opts.get("sleep") or DEFAULT_SLEEP_SEC)

        supplier_code = opts.get("supplier") or DEFAULT_SUPPLIER_CODE
        currency = opts.get("currency") or DEFAULT_CURRENCY
        limit = int(opts.get("limit") or 0)

        # Supplier + ImportBatch
        supplier, _ = Supplier.objects.get_or_create(code=supplier_code, defaults={"name": supplier_code.title()})
        source_name = "samson-api" if not json_path_str else json_path_str
        batch = ImportBatch.objects.create(supplier=supplier, source_name=source_name)

        upserted = 0
        skipped_ident = 0
        processed = 0

        # Два режима: из файла (если указан) или напрямую из API (по умолчанию)
        if json_path_str:
            # Старый путь: подали готовый JSON
            with open(json_path_str, "r", encoding="utf-8") as f:
                payload = json.load(f)
            rows = extract_items(payload)
            if limit:
                rows = rows[:limit]
            batch.items_total = len(rows)
            for idx, row in enumerate(rows, start=1):
                ok = upsert_product_row(row, supplier=supplier, currency=currency, batch=batch)
                if ok:
                    upserted += 1
                else:
                    skipped_ident += 1
                if idx % 500 == 0:
                    self.stdout.write(f"[{idx}/{len(rows)}] processed...")
        else:
            # Основной путь: напрямую из API, без промежуточного JSON
            for sku in fetch_all_in_stock(api_key=api_key, per_page=per_page, verbose=True):
                if limit and processed >= limit:
                    break
                processed += 1
                print(f"[{processed}] SKU {sku}: тянем карточку...", end=" ")
                try:
                    card_payload = fetch_card(sku, api_key=api_key)
                    rows = extract_items(card_payload)  # на случай обёрток
                    if not rows:
                        print("нет данных")
                        continue
                    for row in rows:
                        ok = upsert_product_row(row, supplier=supplier, currency=currency, batch=batch)
                        if ok:
                            upserted += 1
                            print("OK")
                        else:
                            skipped_ident += 1
                            print("пропущено (нет sku/barcode)")
                except Exception as e:
                    print(f"ошибка: {e}")
                if sleep_sec > 0:
                    time.sleep(sleep_sec)
            batch.items_total = processed

        batch.items_upserted = upserted
        batch.finished_at = timezone.now()
        batch.save()

        self.stdout.write(self.style.SUCCESS(
            "Импорт завершён: "
            f"всего={batch.items_total}, апсертов={upserted}, "
            f"пропущено (без идентификаторов sku/barcode): {skipped_ident}"
        ))
