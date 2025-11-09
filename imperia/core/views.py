# core/views.py
from decimal import Decimal
from django.utils.http import url_has_allowed_host_and_scheme
from django.db import IntegrityError
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import (
    Count, Sum, Value, Subquery, OuterRef, IntegerField, DecimalField,
    Q, F, Min
)
from django.db.models.expressions import OrderBy
from django.db.models.functions import Coalesce
from django.http import Http404, HttpRequest, JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import (
    UserUpdateForm, ProfileForm, WarehouseCreateForm, StorageBinForm
)
from .models import Profile, Warehouse, StorageBin, Inventory, Product, StockMovement
from .permissions import warehouse_or_director_required
from .utils.auth import group_required   # <-- единственный нужный импорт
from .widgets import AvatarInput
from django.views.decorators.http import require_http_methods
from .forms import ProductInlineCreateForm
from django.db import transaction, IntegrityError
from django.forms.utils import ErrorList
import json, time
from django.views.decorators.http import require_http_methods
from django.db import transaction, IntegrityError
from django.shortcuts import render, get_object_or_404
from .models import Product, ProductImage, ProductPrice  # Supplier можно получать через FK
from typing import Iterable





# ---------------------------------------------------------------------
# Универсальные константы/утилиты
# ---------------------------------------------------------------------

def movement_const(cls):
    MT = getattr(cls, "MovementType", None) or getattr(cls, "Type", None)
    return {
        "IN":   getattr(MT, "IN",   "IN"),
        "MOVE": getattr(MT, "MOVE", "MOVE"),
        "OUT":  getattr(MT, "OUT",  "OUT"),
    }


ROLE_TO_URL = {
    "warehouse": "warehouse_dashboard",
    "operator": "operator_dashboard",
    "manager": "manager_dashboard",
    "director": "director_dashboard",
}


def in_group(group_name):
    def check(user):
        return user.is_authenticated and user.groups.filter(name=group_name).exists()
    return check


# Стабильная сортировка для остатков
def _qs_with_order(base_qs, order_param: str):
    """
    Поддерживаемые значения order_param:
      bin, -bin, barcode, -barcode, product, -product, qty, -qty, updated, -updated
    """
    if not order_param:
        return base_qs.order_by("product__name", "pk")

    mapping = {
        "bin": F("bin__code"),
        "-bin": F("bin__code").desc(nulls_last=True),
        "barcode": F("product__barcode"),
        "-barcode": F("product__barcode").desc(),
        "product": F("product__name"),
        "-product": F("product__name").desc(),
        "qty": F("quantity"),
        "-qty": F("quantity").desc(),
        "updated": F("updated_at"),
        "-updated": F("updated_at").desc(),
    }
    expr = mapping.get(order_param)
    if expr is None:
        return base_qs.order_by("product__name", "pk")

    alias = "__ord"
    qs = base_qs.annotate(**{alias: expr})
    return qs.order_by(alias, "pk")


# ---------------------------------------------------------------------
# Аутентификация/профиль/роутер ролей
# ---------------------------------------------------------------------

def login_view(request: HttpRequest):
    if request.user.is_authenticated:
        return redirect("post_login_router")

    context = {"error": None}
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            return redirect("post_login_router")
        context["error"] = "Неверный логин или пароль"
    return render(request, "login.html", context)


@login_required
def profile_view(request):
    user = request.user
    profile, _ = Profile.objects.get_or_create(user=user)

    u_form = UserUpdateForm(request.POST or None, instance=user)
    p_form = ProfileForm(request.POST or None, request.FILES or None, instance=profile)
    p_form.fields["avatar"].widget = AvatarInput()  # гарантируем нужный виджет

    if request.method == "POST":
        if u_form.is_valid() and p_form.is_valid():
            u_form.save()
            p_form.save()
            messages.success(request, "Профиль сохранён.")
            return redirect("profile")

    return render(request, "profile.html", {
        "u_form": u_form,
        "p_form": p_form,
        "username": user.username,
        "roles": [g.name for g in user.groups.all()],
    })


@login_required
def post_login_router(request: HttpRequest):
    for role, url_name in ROLE_TO_URL.items():
        if request.user.groups.filter(name=role).exists():
            return redirect(url_name)
    return render(request, "no_role.html")


def logout_view(request: HttpRequest):
    logout(request)
    return redirect("login")


# ---------------------------------------------------------------------
# Дашборды
# ---------------------------------------------------------------------

@login_required
@group_required("warehouse")
def warehouse_dashboard(request):
    # старый дашборд
    warehouses = (
        Warehouse.objects.filter(is_active=True)
        .order_by("code")
        .annotate(
            bins_count=Count("bins", distinct=True),
            inv_positions=Count("inventory", distinct=True),
            inv_qty=Sum("inventory__quantity"),
        )
    )
    recent_moves = (
        StockMovement.objects.select_related("warehouse", "bin_from", "bin_to", "product", "actor")
        .order_by("-timestamp")[:20]
    )

    ctx = {"warehouses": warehouses, "recent_moves": recent_moves}
    return render(request, "dashboards/warehouse.html", ctx)


@login_required
@group_required("warehouse", "director")
def warehouse_new_dashboard(request):
    # новый дашборд
    bins_cnt_sq = Subquery(
        StorageBin.objects.filter(warehouse=OuterRef('pk'))
        .values('warehouse').annotate(c=Count('id')).values('c')[:1],
        output_field=IntegerField(),
    )
    inv_pos_sq = Subquery(
        Inventory.objects.filter(warehouse=OuterRef('pk'), quantity__gt=0)
        .values('warehouse').annotate(c=Count('product', distinct=True)).values('c')[:1],
        output_field=IntegerField(),
    )
    qty_field = DecimalField(max_digits=14, decimal_places=3)
    inv_qty_sq = Subquery(
        Inventory.objects.filter(warehouse=OuterRef('pk'))
        .values('warehouse').annotate(s=Sum('quantity', output_field=qty_field)).values('s')[:1],
        output_field=qty_field,
    )

    warehouses = (
        Warehouse.objects.filter(is_active=True)
        .order_by("code")
        .annotate(
            bins_count=Coalesce(bins_cnt_sq, Value(0, output_field=IntegerField())),
            inv_positions=Coalesce(inv_pos_sq, Value(0, output_field=IntegerField())),
            inv_qty=Coalesce(inv_qty_sq, Value(0, output_field=qty_field)),
        )
    )

    dt_field = "created_at" if hasattr(StockMovement, "created_at") else (
        "created" if hasattr(StockMovement, "created") else None
    )
    moves_qs = StockMovement.objects.select_related(
        "warehouse", "bin_from", "bin_to", "product", "actor"
    )
    moves_qs = moves_qs.order_by(f"-{dt_field}") if dt_field else moves_qs.order_by("-pk")
    recent_moves = list(moves_qs[:20])

    return render(
        request,
        "core/warehouse_dashboard.html",
        {"warehouses": warehouses, "recent_moves": recent_moves},
    )


@login_required
@group_required("operator")
def operator_dashboard(request):
    return render(request, "dashboards/operator.html")


@login_required
@group_required("manager")
def manager_dashboard(request):
    return render(request, "dashboards/manager.html")


@login_required
@group_required("director")
def director_dashboard(request):
    return render(request, "dashboards/director.html")


# ---------------------------------------------------------------------
# Товары
# ---------------------------------------------------------------------

@warehouse_or_director_required
def product_list(request):
    q        = (request.GET.get("q") or "").strip()
    supplier = (request.GET.get("supplier") or "").strip()   # фильтр по коду поставщика
    sort     = (request.GET.get("sort") or "").lower()       # '' | 'price'
    order    = (request.GET.get("order") or "asc").lower()

    try:
        page = int(request.GET.get("page") or 1)
    except ValueError:
        page = 1
    try:
        per_page = int(request.GET.get("per_page") or 24)
    except ValueError:
        per_page = 24

    # База + min_price
    base_qs = (
        Product.objects
        .select_related("supplier")
        .prefetch_related("images", "prices")
        .annotate(min_price=Min("prices__value"))
    )

    # Поиск (3+ символа)
    if len(q) >= 3:
        base_qs = base_qs.filter(
            Q(name__icontains=q) |
            Q(barcode__startswith=q) |
            Q(sku__icontains=q) |
            Q(brand__icontains=q) |
            Q(vendor_code__icontains=q)
        )

    # Список поставщиков из текущей выборки
    suppliers_raw = (
        base_qs
        .values_list("supplier__code", "supplier__name")
        .distinct()
        .order_by("supplier__name", "supplier__code")
    )
    suppliers = [{"code": c or "", "name": n or ""} for c, n in suppliers_raw if c]

    # Фильтр по выбранному поставщику
    qs = base_qs
    if supplier:
        qs = qs.filter(supplier__code=supplier)

    # Сортировка — только по цене (или без сортировки)
    if sort == "price":
        qs = qs.order_by(
            OrderBy(F("min_price"), descending=(order == "desc"), nulls_last=True),
            "id",
        )
    else:
        # «без сортировки» — пусть будет по имени (asc/desc) как дефолт
        sort_field = "name"
        if order == "desc":
            sort_field = "-" + sort_field
        qs = qs.order_by(sort_field, "id")

    # Пагинация
    paginator = Paginator(qs, per_page)
    page_obj = paginator.get_page(page)

    return render(request, "core/product_list.html", {
        "page_obj": page_obj,
        "q": q,
        "supplier": supplier,
        "suppliers": suppliers,
        "sort": sort,
        "order": order,
        "per_page": per_page,
    })

@login_required
def home(request):
    return render(request, "home.html")


def _in_groups(user, names):
    return user.is_authenticated and user.groups.filter(name__in=names).exists()

def product_detail_json(request, pk: int):
    try:
        p = Product.objects.annotate(min_price=Min("prices__value")).get(pk=pk)
    except Product.DoesNotExist:
        raise Http404

    # права
    can_see_supplier = _in_groups(request.user, ["director", "operator", "warehouse"])
    can_see_prices   = _in_groups(request.user, ["director", "operator"])

    images = list(
        p.images.order_by("position", "id").values_list("url", flat=True)
    )
    certs = [
        {"name": c.name, "issued_by": c.issued_by, "active_to": c.active_to, "url": c.url}
        for c in p.certificates.all()
    ]

    # цены только для разрешённых ролей
    if can_see_prices:
        prices = [
            {"type": pr.price_type, "value": str(pr.value), "currency": pr.currency}
            for pr in p.prices.all().order_by("price_type")
        ]
        min_price = str(p.min_price) if p.min_price is not None else None
    else:
        prices = []
        min_price = None

    data = {
        "id": p.id,
        "name": p.name,
        "name_1c": p.name_1c,
        "brand": p.brand,
        "vendor_code": p.vendor_code,
        "barcode": p.barcode,
        # поставщик только для director/operator/warehouse
        "supplier": (getattr(p.supplier, "code", None) if can_see_supplier else None),
        "country": p.manufacturer_country,
        "description": p.description,
        "description_ext": p.description_ext,
        "weight_kg": str(p.weight_kg) if p.weight_kg is not None else None,
        "volume_m3": str(p.volume_m3) if p.volume_m3 is not None else None,
        "pkg": {
            "h_cm": str(p.pkg_height_cm) if p.pkg_height_cm is not None else None,
            "w_cm": str(p.pkg_width_cm) if p.pkg_width_cm is not None else None,
            "d_cm": str(p.pkg_depth_cm) if p.pkg_depth_cm is not None else None,
        },
        "min_price": min_price,
        "images": images,
        "certificates": certs,
        "prices": prices,
    }
    return JsonResponse(data)


# ---------------------------------------------------------------------
# Склады / карточка склада / CRUD ячеек / операции
# ---------------------------------------------------------------------

@login_required
def warehouse_list(request):
    warehouses = Warehouse.objects.filter(is_active=True)
    return render(request, "core/warehouse_list.html", {"warehouses": warehouses})


@login_required
@group_required("warehouse", "director")
def warehouse_detail(request, pk: int):
    warehouse = get_object_or_404(Warehouse, pk=pk)

    active_bin = (request.GET.get("bin") or "").strip()
    q = (request.GET.get("q") or "").strip()
    order = (request.GET.get("o") or "").strip()

    # единый DecimalField для агрегатов по количеству
    qty_field = DecimalField(max_digits=18, decimal_places=3)

    # --- ЯЧЕЙКИ: показываем ВСЕ (активные и неактивные) + статистика ---
    bins = (
        StorageBin.objects
        .filter(warehouse=warehouse)  # ← без is_active=True
        .annotate(
            qty_sum=Coalesce(
                Sum(
                    "inventory__quantity",
                    filter=Q(inventory__quantity__gt=0),
                    output_field=qty_field,
                ),
                Value(0, output_field=qty_field),
            ),
            product_count=Coalesce(
                Count(
                    "inventory__product",
                    filter=Q(inventory__quantity__gt=0),
                    distinct=True,
                ),
                Value(0, output_field=IntegerField()),
            ),
            items_count=Coalesce(
                Count(
                    "inventory__id",
                    filter=Q(inventory__quantity__gt=0),
                ),
                Value(0, output_field=IntegerField()),
            ),
        )
        .order_by("code")
    )

    # --- ОСТАТКИ ---
    inv = (
        Inventory.objects
        .select_related("bin", "product")
        .filter(warehouse=warehouse, quantity__gt=0)
    )

    if active_bin:
        inv = inv.filter(bin__code=active_bin)

    if q:
        inv = inv.filter(
            Q(product__barcode__icontains=q) |
            Q(product__name__icontains=q)
        )

    inv = _qs_with_order(inv, order)

    # --- Метрики шапки (считаем все ячейки, как теперь и показываем) ---
    metrics = {
        "bins_count": StorageBin.objects.filter(warehouse=warehouse).count(),
        "positions": (
            Inventory.objects
            .filter(warehouse=warehouse, quantity__gt=0)
            .values("product").distinct().count()
        ),
        "updated": (
            Inventory.objects
            .filter(warehouse=warehouse)
            .order_by("-updated_at")
            .values_list("updated_at", flat=True)
            .first()
        ),
    }

    ctx = {
        "warehouse": warehouse,
        "bins": bins,
        "inventory": inv,
        "active_bin": active_bin,
        "q": q,
        "order": order,
        **metrics,
    }
    return render(request, "core/warehouse_detail.html", ctx)




@login_required
@group_required("warehouse", "director")
@transaction.atomic
def put_away_view(request, pk: int):
    warehouse = get_object_or_404(Warehouse, pk=pk)

    if request.method == "POST":
        bin_code   = (request.POST.get("bin_code") or "").strip()
        barcode    = (request.POST.get("barcode") or "").strip()
        qty_str    = (request.POST.get("qty") or "").strip()
        create_bin = request.POST.get("create_bin", "") == "on"

        try:
            qty = Decimal(qty_str.replace(",", "."))
        except Exception:
            messages.error(request, "Некорректное количество.")
            return redirect("put_away", pk=warehouse.pk)
        if qty <= 0:
            messages.error(request, "Количество должно быть > 0.")
            return redirect("put_away", pk=warehouse.pk)

        try:
            product = Product.objects.select_for_update().get(barcode=barcode)
        except Product.DoesNotExist:
            messages.error(request, f"Товар со штрихкодом {barcode} не найден.")
            return redirect("put_away", pk=warehouse.pk)

        bin_obj = None
        if bin_code:
            bin_obj = (StorageBin.objects.select_for_update()
                       .filter(warehouse=warehouse, code=bin_code).first())
            if not bin_obj:
                if create_bin:
                    bin_obj = StorageBin.objects.create(
                        warehouse=warehouse, code=bin_code, is_active=True
                    )
                else:
                    messages.error(request, f"Ячейка {bin_code} не найдена.")
                    return redirect("put_away", pk=warehouse.pk)

        qs = (Inventory.objects.select_for_update()
              .filter(warehouse=warehouse, product=product, bin=bin_obj)
              .order_by("pk"))

        inv = qs.first()
        if inv:
            total_existing = qs.aggregate(s=Sum("quantity"))["s"] or Decimal("0")
            qs.exclude(pk=inv.pk).delete()
            inv.quantity = total_existing + qty
            inv.save(update_fields=["quantity", "updated_at"])
        else:
            inv = Inventory.objects.create(
                warehouse=warehouse, product=product, bin=bin_obj, quantity=qty
            )

        const = movement_const(StockMovement)
        field_names = {f.name for f in StockMovement._meta.get_fields() if hasattr(f, "attname")}
        mtype_field = "movement_type" if "movement_type" in field_names else ("type" if "type" in field_names else None)
        actor_field = "actor" if "actor" in field_names else ("performed_by" if "performed_by" in field_names else None)

        kwargs = dict(
            warehouse=warehouse,
            bin_from=None,
            bin_to=bin_obj,
            product=product,
            quantity=qty,
        )
        if mtype_field:
            kwargs[mtype_field] = const["IN"]
        if actor_field:
            kwargs[actor_field] = request.user

        StockMovement.objects.create(**kwargs)

        messages.success(request, "Товар размещён.")
        return redirect("warehouse_detail", pk=warehouse.pk)

    return render(request, "core/put_away.html", {"warehouse": warehouse})


@login_required
@group_required("warehouse", "director")
@transaction.atomic
def move_view(request, pk: int):
    warehouse = get_object_or_404(Warehouse, pk=pk)

    if request.method == "POST":
        from_code = (request.POST.get("from_bin") or "").strip()
        to_code   = (request.POST.get("to_bin")   or "").strip()
        barcode   = (request.POST.get("barcode")  or "").strip()
        qty_str   = (request.POST.get("qty")      or "").strip()
        create_to = request.POST.get("create_to", "") == "on"

        try:
            qty = Decimal(qty_str.replace(",", "."))
        except Exception:
            messages.error(request, "Некорректное количество.")
            return redirect("move_between_bins", pk=warehouse.pk)
        if qty <= 0:
            messages.error(request, "Количество должно быть > 0.")
            return redirect("move_between_bins", pk=warehouse.pk)

        try:
            product = Product.objects.select_for_update().get(barcode=barcode)
        except Product.DoesNotExist:
            messages.error(request, f"Товар со штрихкодом {barcode} не найден.")
            return redirect("move_between_bins", pk=warehouse.pk)

        from_bin = None
        if from_code:
            from_bin = (StorageBin.objects.select_for_update()
                        .filter(warehouse=warehouse, code=from_code).first())
            if not from_bin:
                messages.error(request, f"Ячейка-источник «{from_code}» не найдена.")
                return redirect("move_between_bins", pk=warehouse.pk)

        to_bin = None
        if to_code:
            to_bin = (StorageBin.objects.select_for_update()
                      .filter(warehouse=warehouse, code=to_code).first())
            if not to_bin:
                if create_to:
                    to_bin = StorageBin.objects.create(
                        warehouse=warehouse, code=to_code, is_active=True
                    )
                else:
                    messages.error(request, f"Ячейка-получатель «{to_code}» не найдена.")
                    return redirect("move_between_bins", pk=warehouse.pk)

        if from_bin == to_bin:
            messages.error(request, "Источник и получатель совпадают.")
            return redirect("move_between_bins", pk=warehouse.pk)

        src_qs = (Inventory.objects.select_for_update()
                  .filter(warehouse=warehouse, product=product, bin=from_bin)
                  .order_by("pk"))
        if not src_qs.exists():
            messages.error(request, "В источнике нет такого товара.")
            return redirect("move_between_bins", pk=warehouse.pk)

        src = src_qs.first()
        if src_qs.count() > 1:
            total = sum((r.quantity for r in src_qs), Decimal("0"))
            src.quantity = total
            Inventory.objects.exclude(pk=src.pk).filter(
                warehouse=warehouse, product=product, bin=from_bin
            ).delete()

        if src.quantity < qty:
            messages.error(request, "Недостаточно товара в источнике.")
            return redirect("move_between_bins", pk=warehouse.pk)

        src.quantity -= qty
        if src.quantity == 0:
            src.delete()
        else:
            src.save(update_fields=["quantity", "updated_at"])

        dst_qs = (Inventory.objects.select_for_update()
                  .filter(warehouse=warehouse, product=product, bin=to_bin)
                  .order_by("pk"))
        if dst_qs.exists():
            dst = dst_qs.first()
            if dst_qs.count() > 1:
                total = sum((r.quantity for r in dst_qs), Decimal("0"))
                dst.quantity = total
                Inventory.objects.exclude(pk=dst.pk).filter(
                    warehouse=warehouse, product=product, bin=to_bin
                ).delete()
            dst.quantity = (dst.quantity or Decimal("0")) + qty
            dst.save(update_fields=["quantity", "updated_at"])
        else:
            Inventory.objects.create(
                warehouse=warehouse, product=product, bin=to_bin, quantity=qty
            )

        MT = getattr(StockMovement, "MovementType", None) or getattr(StockMovement, "Type", None)
        MOV_MOVE = getattr(MT, "MOVE", None) or "MOVE"

        StockMovement.objects.create(
            warehouse=warehouse,
            bin_from=from_bin, bin_to=to_bin,
            product=product, quantity=qty,
            movement_type=MOV_MOVE, actor=request.user,
        )

        messages.success(request, "Перемещение выполнено.")
        return redirect("warehouse_detail", pk=warehouse.pk)

    bins = StorageBin.objects.filter(warehouse=warehouse, is_active=True).order_by("code")
    return render(request, "core/move.html", {"warehouse": warehouse, "bins": bins})


@login_required
@group_required("director")
def warehouse_create(request):
    if request.method == "POST":
        form = WarehouseCreateForm(request.POST)
        if form.is_valid():
            w = form.save()
            messages.success(request, f"Склад {w.code} — {w.name} создан.")
            return redirect("warehouse_detail", pk=w.pk)
    else:
        form = WarehouseCreateForm(initial={"is_active": True})
    return render(request, "core/warehouse_create.html", {"form": form})


@login_required
@group_required("director")
def warehouse_delete(request, pk: int):
    wh = get_object_or_404(Warehouse, pk=pk)
    if request.method == "POST":
        name = f"{wh.code} — {wh.name}"
        wh.delete()
        messages.success(request, f"Склад «{name}» удалён.")
        return redirect("warehouse_dashboard")
    return render(request, "core/warehouse_confirm_delete.html", {"warehouse": wh})


@login_required
@group_required("warehouse", "director")
@transaction.atomic
def inventory_edit(request, warehouse_pk: int, pk: int):
    warehouse = get_object_or_404(Warehouse, pk=warehouse_pk)
    inv = get_object_or_404(Inventory.objects.select_for_update(), pk=pk, warehouse=warehouse)

    from .forms import InventoryEditForm
    if request.method == "POST":
        if "delete" in request.POST:
            inv.delete()
            messages.success(request, "Позиция удалена.")
            return redirect("warehouse_detail", pk=warehouse.pk)

        form = InventoryEditForm(request.POST, warehouse=warehouse)
        if form.is_valid():
            new_bin = form.cleaned_data["bin"]
            new_qty = form.cleaned_data["quantity"]

            if new_qty == 0:
                inv.delete()
                messages.success(request, "Позиция удалена (кол-во = 0).")
                return redirect("warehouse_detail", pk=warehouse.pk)

            moved_bin = (new_bin != inv.bin)
            if moved_bin:
                dup = Inventory.objects.select_for_update().filter(
                    warehouse=warehouse, bin=new_bin, product=inv.product
                ).exclude(pk=inv.pk).first()
                if dup:
                    dup.quantity += new_qty
                    dup.save(update_fields=["quantity", "updated_at"])
                    inv.delete()
                    messages.success(request, "Позиция объединена с существующей ячейкой.")
                else:
                    inv.bin = new_bin
                    inv.quantity = new_qty
                    inv.save(update_fields=["bin", "quantity", "updated_at"])
                    messages.success(request, "Позиция обновлена.")
            else:
                inv.quantity = new_qty
                inv.save(update_fields=["quantity", "updated_at"])
                messages.success(request, "Позиция обновлена.")

            return redirect("warehouse_detail", pk=warehouse.pk)
    else:
        form = InventoryEditForm(
            warehouse=warehouse,
            initial={"bin": inv.bin_id, "quantity": inv.quantity},
        )

    return render(request, "core/inventory_edit.html", {
        "warehouse": warehouse,
        "inv": inv,
        "form": form,
    })


# --------------------- CRUD ячеек ---------------------

@login_required
@group_required("warehouse", "director")
def bin_create(request, pk: int):
    warehouse = get_object_or_404(Warehouse, pk=pk)

    if request.method == "POST":
        form = StorageBinForm(request.POST)  # БЕЗ warehouse=...
        if form.is_valid():
            obj = form.save(commit=False)
            obj.warehouse = warehouse
            try:
                obj.save()
            except IntegrityError:
                messages.error(request, "Ячейка с таким кодом уже существует на этом складе.")
                return render(request, "core/bin_form.html", {
                    "warehouse": warehouse,
                    "form": form,
                    "title": "Новая ячейка",
                })
            messages.success(request, "Ячейка создана.")
            return redirect("warehouse_detail", pk=warehouse.pk)
    else:
        form = StorageBinForm(initial={"is_active": True})  # БЕЗ warehouse=...

    return render(request, "core/bin_form.html", {
        "warehouse": warehouse,
        "form": form,
        "title": "Новая ячейка",
    })


@login_required
@group_required("warehouse", "director")
def bin_edit(request, warehouse_pk: int, pk: int):
    warehouse = get_object_or_404(Warehouse, pk=warehouse_pk)
    bin_obj = get_object_or_404(StorageBin, pk=pk, warehouse=warehouse)

    if request.method == "POST":
        form = StorageBinForm(request.POST, instance=bin_obj)  # БЕЗ warehouse=...
        if form.is_valid():
            obj = form.save(commit=False)
            obj.warehouse = warehouse  # на всякий случай закрепим склад
            try:
                obj.save()
            except IntegrityError:
                messages.error(request, "Ячейка с таким кодом уже существует на этом складе.")
                return render(request, "core/bin_form.html", {
                    "warehouse": warehouse,
                    "form": form,
                    "title": f"Ячейка {bin_obj.code}",
                })
            messages.success(request, "Ячейка сохранена.")
            return redirect("warehouse_detail", pk=warehouse.pk)
    else:
        form = StorageBinForm(instance=bin_obj)  # БЕЗ warehouse=...

    return render(request, "core/bin_form.html", {
        "warehouse": warehouse,
        "form": form,
        "title": f"Ячейка {bin_obj.code}",
    })


@login_required
@group_required("warehouse", "director")
@transaction.atomic
def bin_delete(request, warehouse_pk: int, bin_pk: int):
    if request.method != "POST":
        # на всякий случай, не даём удалять через GET
        return redirect("warehouse_detail", warehouse_pk)

    # находим связанную ячейку строго внутри склада
    bin_obj = get_object_or_404(
        StorageBin,
        pk=bin_pk,
        warehouse_id=warehouse_pk,
    )

    # есть ли в ячейке товар?
    has_items = Inventory.objects.filter(
        bin=bin_obj,
        quantity__gt=0
    ).exists()

    if has_items:
        messages.error(request, f"Нельзя удалить ячейку {bin_obj.code}: в ней есть товар.")
        return redirect("warehouse_detail", warehouse_pk)

    code = bin_obj.code
    bin_obj.delete()
    messages.success(request, f"Ячейка {code} удалена.")
    return redirect("warehouse_detail", warehouse_pk)


# --------------------- CRUD продуктов ---------------------
def _can_see_prices(user) -> bool:
    return user.is_authenticated and user.groups.filter(
        name__in=["operator", "director"]
    ).exists()

def product_card(request, pk: int):
    product = get_object_or_404(Product, pk=pk)

    # ---- ГАЛЕРЕЯ (list[str]) ----
    gallery = []
    main = getattr(product, "image_url", None)
    if main:
        gallery.append(main)

    if hasattr(product, "images") and product.images is not None:
        # поддержим related manager и обычный список
        src = product.images.all() if hasattr(product.images, "all") else product.images
        for img in src:
            url = getattr(img, "url", None) or getattr(img, "image_url", None) or str(img)
            if url and url not in gallery:
                gallery.append(url)

    # ---- АТРИБУТЫ (list[tuple[str, str]]) ----
    attrs = []
    raw = getattr(product, "attributes", None)
    if isinstance(raw, dict):
        attrs = [(str(k), str(v)) for k, v in raw.items() if v not in (None, "")]
    elif isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                k, v = item
                if v not in (None, ""):
                    attrs.append((str(k), str(v)))

    # ---- СЕРТИФИКАТЫ (list[{"name","url"}]) ----
    certificates = []
    if hasattr(product, "certificates") and product.certificates is not None:
        src = product.certificates.all() if hasattr(product.certificates, "all") else product.certificates
        for c in src:
            certificates.append({
                "name": getattr(c, "name", None) or str(c),
                "url": getattr(c, "url", None),
            })

    # ---- ПРАВА НА ЦЕНЫ ----
    can_prices = (
        request.user.is_authenticated
        and request.user.groups.filter(name__in=["operator", "director"]).exists()
    )

    # ---- ЦЕНЫ ----
    prices = []
    price_min = None
    if can_prices:
        try:
            prices = list(
                ProductPrice.objects
                .filter(product=product)
                .order_by("price_type")
                .values("price_type", "value", "currency")
            )
            if prices:
                price_min = min((p["value"] for p in prices if p["value"] is not None), default=None)
        except Exception:
            # запасной вариант — если модели цен нет/упали, возьмём поля с объекта
            price_min = getattr(product, "price_min", None) or getattr(product, "price", None)

    context = {
        "product": product,
        "gallery": gallery,
        "attrs": attrs,
        "certificates": certificates,
        "prices": prices,          # список всех цен (если разрешено)
        "price_min": price_min,    # минимальная (если разрешено)
        "can_prices": can_prices,  # флаг для шаблона
    }
    return render(request, "core/partials/product_card.html", context)

# Что показать как картинку — из ProductImage
def _pick_product_image(request, product):
    if not product:
        return None
    return (ProductImage.objects
            .filter(product=product)
            .order_by("position", "id")
            .values_list("url", flat=True)
            .first())

def _price_for(product, price_types: Iterable[str] | str):
    """Вернёт значение цены для первого найденного типа из набора алиасов."""
    if isinstance(price_types, str):
        price_types = [price_types]
    return (ProductPrice.objects
            .filter(product=product, price_type__in=list(price_types))
            .values_list("value", flat=True)
            .first())

def _price_min(product, price_type=None):
    qs = ProductPrice.objects.filter(product=product)
    if price_type:
        qs = qs.filter(price_type=price_type)
    return qs.order_by("value").values_list("value", flat=True).first()

def _first_attr(obj, names, default=""):
    for n in names:
        if hasattr(obj, n):
            v = getattr(obj, n)
            if v not in (None, ""):
                if n == "supplier" and hasattr(v, "name"):
                    return v.name
                return v
    return default

@require_http_methods(["GET"])
def product_by_barcode(request):
    barcode = (request.GET.get("barcode") or "").strip()
    product = Product.objects.filter(barcode=barcode).first() if barcode else None
    ctx = {
        "barcode": barcode,
        "product": product,
        "thumb_url": _pick_product_image(request, product) if product else None,
        "price_min": _price_min(product) if product else None,
    }
    return render(request, "core/partials/putaway_product_preview.html", ctx)


@require_http_methods(["GET", "POST"])
@transaction.atomic
def product_create_inline(request):
    initial_barcode = (request.GET.get("barcode") or request.POST.get("barcode") or "").strip()

    if request.method == "GET":
        form = ProductInlineCreateForm(initial={"barcode": initial_barcode})
        return render(request, "core/partials/product_create_inline.html", {"form": form})

    form = ProductInlineCreateForm(request.POST)
    if not form.is_valid():
        return render(request, "core/partials/product_create_inline.html", {"form": form})

    cd = form.cleaned_data

    # --- supplier обязателен (создаём по имени из формы) ---
    sup_field = Product._meta.get_field("supplier")
    SupplierModel = sup_field.remote_field.model
    supplier_obj, _ = SupplierModel.objects.get_or_create(
        name=(cd.get("vendor") or "Без поставщика").strip() or "Без поставщика"
    )

    # --- vendor_code (NOT NULL): если не ввели — от barcode, иначе AUTO-... ---
    vendor_code = (cd.get("vendor_code") or cd.get("barcode") or f"AUTO-{supplier_obj.code}-{int(time.time())}")[:255]

    # --- создаём сам продукт ---
    product = Product.objects.create(
        name=(cd.get("name") or "").strip(),
        barcode=(cd.get("barcode") or "").strip() or None,
        brand=(cd.get("brand") or "").strip(),
        description=cd.get("description") or "",
        supplier=supplier_obj,
        vendor_code=vendor_code,

        manufacturer_country=cd.get("country") or "",
        weight_kg=cd.get("weight_kg"),
        volume_m3=cd.get("volume_m3"),
        pkg_height_cm=cd.get("pkg_h_cm"),
        pkg_width_cm=cd.get("pkg_w_cm"),
        pkg_depth_cm=cd.get("pkg_d_cm"),
        description_ext=cd.get("description_ext") or "",
    )

    # --- изображение (если указали URL) ---
    img_url = (cd.get("image_url") or "").strip()
    if img_url:
        ProductImage.objects.create(product=product, url=img_url, position=1)

    # --- цена типа 'contracts' (если указали) ---
    price_val = cd.get("price_contracts")
    if price_val is not None:  # поле присутствует в форме и не пустое
        ProductPrice.objects.update_or_create(
            product=product,
            price_type="contracts",
            defaults={"value": price_val, "currency": "RUB"},
        )

    # --- превью справа: показываем ту же 'contracts' цену ---
    ctx = {
        "barcode": product.barcode,
        "product": product,
        "thumb_url": _pick_product_image(request, product),
        "price_min": _price_for(product, "contracts"),  # именно contracts
    }
    return render(request, "core/partials/putaway_product_preview.html", ctx)

@require_http_methods(["GET", "POST"])
@transaction.atomic
def product_update_inline(request, pk: int):
    product = get_object_or_404(Product, pk=pk)

    # ---------- GET: показать форму с заполненными полями ----------
    if request.method == "GET":
        initial = {
            "name": product.name,
            "barcode": product.barcode,
            "brand": product.brand,
            "vendor": product.supplier.name if getattr(product, "supplier", None) else "",
            "image_url": _pick_product_image(request, product) or "",
            "description": product.description,

            # новые поля
            "country": product.manufacturer_country or "",
            "weight_kg": product.weight_kg,
            "volume_m3": product.volume_m3,
            "pkg_h_cm": product.pkg_height_cm,
            "pkg_w_cm": product.pkg_width_cm,
            "pkg_d_cm": product.pkg_depth_cm,
            "description_ext": product.description_ext or "",
            "vendor_code": product.vendor_code or "",
            "price_contracts": _price_for(product, ["contracts", "contract"]),  # ← вот так
        }
        form = ProductInlineCreateForm(initial=initial)
        return render(request, "core/partials/product_update_inline.html", {"form": form, "product": product})

    # ---------- POST: обработка сохранения (тот блок, что вы прислали) ----------
    form = ProductInlineCreateForm(request.POST)
    if not form.is_valid():
        return render(request, "core/partials/product_update_inline.html",
                      {"form": form, "product": product})

    cd = form.cleaned_data

    # простые поля
    for fld in ("name", "barcode", "brand", "description"):
        if cd.get(fld) not in (None, ""):
            setattr(product, fld, cd[fld])

    # supplier по имени vendor
    vendor_name = (cd.get("vendor") or "").strip()
    if vendor_name:
        sup_field = Product._meta.get_field("supplier")
        SupplierModel = sup_field.remote_field.model
        supplier_obj, _ = SupplierModel.objects.get_or_create(name=vendor_name)
        product.supplier = supplier_obj

    # новые поля
    product.manufacturer_country = cd.get("country") or product.manufacturer_country
    if cd.get("weight_kg") is not None: product.weight_kg = cd["weight_kg"]
    if cd.get("volume_m3") is not None: product.volume_m3 = cd["volume_m3"]
    if cd.get("pkg_h_cm") is not None:  product.pkg_height_cm = cd["pkg_h_cm"]
    if cd.get("pkg_w_cm") is not None:  product.pkg_width_cm = cd["pkg_w_cm"]
    if cd.get("pkg_d_cm") is not None:  product.pkg_depth_cm = cd["pkg_d_cm"]
    if cd.get("description_ext") not in (None, ""): product.description_ext = cd["description_ext"]
    # Разрешим менять vendor_code, если текущий автосгенерирован
    if cd.get("vendor_code") not in (None, ""):
        if (product.vendor_code or "").startswith("AUTO-") or not product.vendor_code:
            product.vendor_code = cd["vendor_code"]

    product.save()
    price_val = cd.get("price_contracts")
    if price_val is not None:
        existing = ProductPrice.objects.filter(
            product=product, price_type__in=["contracts", "contract"]
        ).first()
        price_type = existing.price_type if existing else "contracts"
        ProductPrice.objects.update_or_create(
            product=product,
            price_type=price_type,
            defaults={"value": price_val, "currency": "RUB"},
        )
    else:
        ProductPrice.objects.filter(product=product, price_type__in=["contracts", "contract"]).delete()

    # добавим картинку, если ввели
    img_url = (cd.get("image_url") or "").strip()
    if img_url and not ProductImage.objects.filter(product=product, url=img_url).exists():
        pos = ProductImage.objects.filter(product=product).count() + 1
        ProductImage.objects.create(product=product, url=img_url, position=pos)

    ctx = {
        "barcode": product.barcode,
        "product": product,
        "thumb_url": _pick_product_image(request, product),
        "price_min": _price_for(product, ["contracts", "contract"]),
    }
    return render(request, "core/partials/putaway_product_preview.html", ctx)


@require_http_methods(["GET", "POST"])
@transaction.atomic
def product_delete_inline(request, pk: int):
    """Подтверждение удаления и удаление. Блокируем, если есть остатки."""
    product = get_object_or_404(Product, pk=pk)

    # Проверка остатков
    has_stock = False
    if Inventory is not None:
        try:
            has_stock = Inventory.objects.filter(product=product).exclude(quantity__lte=0).exists()
        except Exception:
            # если другая схема полей — считаем, что остатки есть на всякий случай
            has_stock = False

    if request.method == "POST":
        if has_stock:
            # Покажем сообщение и вернёмся к превью
            thumb_url = _pick_product_image(request, product)
            ctx = {"barcode": product.barcode, "product": product, "thumb_url": thumb_url, "delete_error": "Нельзя удалить товар: по нему есть остатки."}
            return render(request, "core/partials/putaway_product_preview.html", ctx)

        barcode = product.barcode
        product.delete()
        # Отрисуем «не найдено» с кнопкой создать
        return render(request, "core/partials/putaway_product_preview.html", {"barcode": barcode, "product": None, "thumb_url": None})

    # GET — показать подтверждение
    return render(request, "core/partials/product_delete_inline.html", {"product": product})