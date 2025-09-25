# core/management/commands/import_relef_api.py
from __future__ import annotations

import time
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter, Retry

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, IntegrityError

from core.models import (
    Supplier,
    Product,
    ProductImage,
    ProductCertificate,
    ProductPrice,
)

DIGITS_RE = re.compile(r"\d+")


def norm_barcode(val: Any) -> Optional[str]:
    """Оставляем только цифры; пустые -> None."""
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    digits = "".join(DIGITS_RE.findall(s))
    return digits or None


def norm_sku(val: Any) -> Optional[str]:
    """Артикул: trim; пустые -> None."""
    if val is None:
        return None
    s = str(val).strip()
    return s or None


def to_decimal(val: Any) -> Optional[Decimal]:
    if val in (None, "", "-", "—"):
        return None
    s = str(val).strip().replace(" ", "").replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def build_session(timeout: int = 30) -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    s.request = _wrap_timeout(s.request, timeout)  # type: ignore
    return s


def _wrap_timeout(fn, timeout: int):
    def inner(method, url, **kwargs):
        if "timeout" not in kwargs:
            kwargs["timeout"] = timeout
        return fn(method, url, **kwargs)
    return inner


class Command(BaseCommand):
    """
    Импорт из Relef API:
      - Пагинация (limit/offset), ретраи на 429/5xx.
      - Фильтр по остатку (--min-remains, по умолчанию >0).
      - Поиск товара: barcode -> (supplier, vendor_code).
      - NOT NULL vendor_code: если sku нет, используем barcode, иначе генерируем AUTO-<supplier>-<code|cnt>.
      - Пер-товарные транзакции (savepoint) + перехват IntegrityError.
      - Цены: сохраняем один тип (--price-type, по умолчанию 'contracts').
      - Фото/сертификаты: дозапись уникальных URL, либо полная замена (--replace-images).
    """
    help = "Импорт товаров из Relef API в БД проекта."

    API_URL = "https://api-sale.relef.ru/api/v1/products/list"

    def add_arguments(self, parser):
        # По просьбе — ключ зашит по умолчанию, но можно переопределить
        parser.add_argument("--apikey", type=str, default="2ae904872405477f8e7b543e885f3db0",
                            help="API ключ Relef (по умолчанию зашит).")
        parser.add_argument("--supplier", type=str, default="relef",
                            help="Код поставщика (по умолчанию relef).")
        parser.add_argument("--limit", type=int, default=1000,
                            help="Размер страницы (1..3000).")
        parser.add_argument("--sleep", type=float, default=0.4,
                            help="Пауза между страницами, сек.")
        parser.add_argument("--min-remains", type=int, default=1,
                            help="Минимальный остаток для импорта (по умолчанию 1).")
        parser.add_argument("--price-type", type=str, default="contracts",
                            help="Тип цены, который записываем (например: contracts / base / retail).")
        parser.add_argument("--replace-images", action="store_true",
                            help="Удалять старые изображения перед вставкой новых.")
        parser.add_argument("--max-pages", type=int, default=None,
                            help="Ограничить число страниц (для теста).")
        parser.add_argument("--timeout", type=int, default=30,
                            help="HTTP таймаут, сек.")

    def handle(self, *args, **opts):
        apikey: str = (opts["apikey"] or "").strip()
        if not apikey:
            raise CommandError("Не задан --apikey")

        supplier_code: str = (opts["supplier"] or "").strip().lower() or "relef"
        limit: int = max(1, min(3000, int(opts["limit"])))
        pause: float = float(opts["sleep"])
        min_remains: int = int(opts["min_remains"])          # <-- snake_case
        price_type: str = str(opts["price_type"]).strip()    # <-- snake_case
        replace_images: bool = bool(opts["replace_images"])   # <-- snake_case
        max_pages: Optional[int] = opts["max_pages"]          # <-- snake_case
        timeout: int = int(opts["timeout"])

        supplier, _ = Supplier.objects.get_or_create(
            code=supplier_code,
            defaults={"name": supplier_code.upper()}
        )

        session = build_session(timeout=timeout)
        headers = {
            "Content-Type": "application/json",
            "accept": "application/json",
            "apikey": apikey,
        }

        total = created = updated = skipped = no_ids = 0
        pages = 0
        offset = 0

        while True:
            pages += 1
            if max_pages and pages > max_pages:
                break

            payload = {"limit": limit, "offset": offset}
            resp = session.post(self.API_URL, headers=headers, json=payload)
            status = resp.status_code

            if status != 200:
                if status in (429, 500, 502, 503, 504):
                    self.stderr.write(self.style.WARNING(f"Временная ошибка {status}. Пауза 5с…"))
                    time.sleep(5)
                    continue
                raise CommandError(f"HTTP {status}: {resp.text}")

            data = resp.json()
            items: List[Dict[str, Any]] = data.get("list") or []
            if not items:
                break

            for item in items:
                total += 1

                # Остатки
                qty = 0
                remains = item.get("remains") or []
                if remains and isinstance(remains, list):
                    try:
                        qty = int(remains[0].get("quantity") or 0)
                    except Exception:
                        qty = 0
                if qty < min_remains:
                    skipped += 1
                    continue

                code = item.get("code")  # внутренний код Relef
                sku = norm_sku(item.get("vendorCode"))
                name = (item.get("name") or "").strip()
                description = (item.get("description") or "").replace("\n", " ").strip()
                brand = (item.get("brand") or "").strip() if isinstance(item.get("brand"), str) else ""

                # Цены: ищем переданный тип (по умолчанию 'contracts')
                prices_map: Dict[str, Decimal] = {}
                for p in item.get("prices") or []:
                    ptype = (p.get("type") or "").strip().lower()
                    pval = to_decimal(p.get("value"))
                    if ptype and pval is not None:
                        prices_map[ptype] = pval
                price_val = prices_map.get(price_type.lower())

                # Упаковка: штрихкод / вес / объем
                barcode = None
                weight_kg = None
                volume_m3 = None
                pack_units = item.get("packUnits") or []
                if pack_units:
                    pu0 = pack_units[0]
                    barcodes = pu0.get("barcodes") or []
                    if barcodes:
                        barcode = norm_barcode(barcodes[0])
                    weight_kg = to_decimal(pu0.get("weight"))
                    volume_m3 = to_decimal(pu0.get("volume"))

                # Страна
                country = ""
                cobj = item.get("country") or {}
                if isinstance(cobj, dict):
                    country = (cobj.get("name") or "").strip()

                # Сертификаты
                certs_in = item.get("certificates") or []
                certs: List[Dict[str, str]] = []
                for c in certs_in:
                    nm = (c.get("name") or "").strip()
                    url = (c.get("path") or "").strip()  # у них ссылка обычно в path
                    if url.lower().startswith(("http://", "https://")):
                        certs.append({"name": nm, "url": url, "issued_by": ""})

                # Изображения
                images_in = item.get("images") or []
                images: List[str] = []
                for im in images_in:
                    url = (im.get("path") or "").strip()
                    if url.lower().startswith(("http://", "https://")):
                        images.append(url)

                # Базовая валидация
                if not name:
                    skipped += 1
                    continue

                if not (barcode or sku):
                    # Оба идентификатора пустые — лучше пропустить, чтобы не плодить мусор
                    no_ids += 1
                    skipped += 1
                    continue

                # vendor_code обязателен (NOT NULL)
                if sku:
                    vendor_code_to_store = sku
                elif barcode:
                    vendor_code_to_store = barcode
                else:
                    vendor_code_to_store = f"AUTO-{supplier.code}-{code or total}"

                with transaction.atomic():
                    # Поиск существующего товара
                    product: Optional[Product] = None
                    if barcode:
                        product = Product.objects.filter(barcode=barcode).first()
                    if not product and sku:
                        product = Product.objects.filter(supplier=supplier, vendor_code=sku).first()

                    defaults = {
                        "name": name,
                        "brand": brand,
                        "description": description or "",
                        "description_ext": "",
                        "manufacturer_country": country or "",
                        "weight_kg": weight_kg,
                        "volume_m3": volume_m3,
                        "pkg_height_cm": None,
                        "pkg_width_cm": None,
                        "pkg_depth_cm": None,
                        "supplier": supplier,
                        "vendor_code": vendor_code_to_store,  # НЕ None
                        "barcode": barcode,                   # может быть None
                    }

                    try:
                        if product:
                            # не перетираем внятный vendor_code автосгенерированным
                            if product.vendor_code and (not sku) and vendor_code_to_store.startswith("AUTO-"):
                                defaults.pop("vendor_code", None)
                            for k, v in defaults.items():
                                setattr(product, k, v)
                            product.save()
                            updated += 1
                        else:
                            product = Product.objects.create(**defaults)
                            created += 1
                    except IntegrityError:
                        skipped += 1
                        continue

                    # Цена
                    if price_val is not None:
                        ProductPrice.objects.update_or_create(
                            product=product,
                            price_type=price_type,
                            defaults={"value": price_val, "currency": "RUB"},
                        )

                    # Изображения
                    if images:
                        if replace_images:
                            ProductImage.objects.filter(product=product).delete()
                        existing = set(ProductImage.objects.filter(product=product).values_list("url", flat=True))
                        pos = ProductImage.objects.filter(product=product).count()
                        for u in images:
                            if u not in existing:
                                pos += 1
                                ProductImage.objects.create(product=product, url=u, position=pos)

                    # Сертификаты
                    if certs:
                        existing_certs = set(ProductCertificate.objects.filter(product=product).values_list("url", flat=True))
                        for c in certs:
                            if c["url"] not in existing_certs:
                                ProductCertificate.objects.create(
                                    product=product,
                                    url=c["url"],
                                    name=c["name"],
                                    issued_by=c.get("issued_by", ""),
                                )

            self.stdout.write(self.style.SUCCESS(
                f"Страница {pages}: получено {len(items)}; всего: {total}, создано: {created}, обновлено: {updated}, пропущено: {skipped}"
            ))

            offset += limit
            time.sleep(pause)

        msg = f"Готово. Всего={total}, создано={created}, обновлено={updated}, пропущено={skipped}"
        if no_ids:
            msg += f" (без идентификаторов: {no_ids})"
        self.stdout.write(self.style.SUCCESS(msg))
