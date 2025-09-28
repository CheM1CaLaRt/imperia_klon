# core/views.py
from django.contrib.auth import authenticate, login, logout
from django.http import HttpRequest
from .forms import UserUpdateForm, ProfileForm
from .models import Profile
from .widgets import AvatarInput
from django.db.models import Q, Min, F
from django.db.models.expressions import OrderBy
from django.core.paginator import Paginator
from django.http import JsonResponse, Http404
from django.db.models import Min
from .permissions import warehouse_or_director_required
from .forms import WarehouseCreateForm
from decimal import Decimal
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.db import transaction
from django.contrib.auth.decorators import login_required
from .utils.auth import group_required
from .forms import StorageBinForm
from .models import Warehouse, StorageBin, Inventory, Product, StockMovement
from django.db.models import (
    Count, Sum, OuterRef, Subquery, IntegerField, DecimalField, Value
)
from django.db.models.functions import Coalesce

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
    "director": "director_dashboard",  # управляющий
}

def in_group(group_name):
    def check(user):
       return user.is_authenticated and user.groups.filter(name=group_name).exists()
    return check

#def group_required(group_name):
 #   return user_passes_test(in_group(group_name), login_url="login")

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

    # на случай, если Meta не подхватилась — жёстко заменим:
    p_form.fields["avatar"].widget = AvatarInput()

    if request.method == "POST":
        if u_form.is_valid() and p_form.is_valid():
            u_form.save()
            p_form.save()
            messages.success(request, "Профиль сохранён.")
            # при автосабмите вернёмся на эту же страницу
            return redirect("profile")

    return render(request, "profile.html", {
        "u_form": u_form,
        "p_form": p_form,
        "username": user.username,
        "roles": [g.name for g in user.groups.all()],
    })
@login_required
def post_login_router(request: HttpRequest):
    """
    Определяем первую доступную роль пользователя и кидаем в соответствующий раздел.
    Если ролей несколько — приоритет по порядку в ROLE_TO_URL.
    """
    for role, url_name in ROLE_TO_URL.items():
        if request.user.groups.filter(name=role).exists():
            return redirect(url_name)
    # Если ролей нет — можно отправить в заглушку или назад на логин
    return render(request, "no_role.html")

def logout_view(request: HttpRequest):
    logout(request)
    return redirect("login")

# --- Дашборды ролей ---
@login_required
@group_required("warehouse")
def warehouse_dashboard(request):
    from .models import Warehouse, StorageBin, Inventory, StockMovement

    # Склады + аннотации, чтобы в шаблоне не лазить по словарям
    warehouses = (
        Warehouse.objects.filter(is_active=True)
        .order_by("code")
        .annotate(
            bins_count=Count("bins", distinct=True),
            inv_positions=Count("inventory", distinct=True),
            inv_qty=Sum("inventory__quantity"),
        )
    )

    # Последние движения
    recent_moves = (
        StockMovement.objects.select_related("warehouse", "bin_from", "bin_to", "product", "actor")
        .order_by("-timestamp")[:20]
    )

    ctx = {
        "warehouses": warehouses,
        "recent_moves": recent_moves,
    }
    # ВАЖНО: рендерим СТАРЫЙ шаблон
    return render(request, "dashboards/warehouse.html", ctx)

@login_required
@group_required("warehouse", "director")
def warehouse_new_dashboard(request):
    from .models import Warehouse, StorageBin, Inventory

    bins_cnt_sq = Subquery(
        StorageBin.objects
        .filter(warehouse=OuterRef('pk'))
        .values('warehouse')
        .annotate(c=Count('id'))
        .values('c')[:1],
        output_field=IntegerField(),
    )

    inv_pos_sq = Subquery(
        Inventory.objects
        .filter(warehouse=OuterRef('pk'))
        .values('warehouse')
        .annotate(c=Count('id'))
        .values('c')[:1],
        output_field=IntegerField(),
    )

    qty_field = DecimalField(max_digits=14, decimal_places=3)

    inv_qty_sq = Subquery(
        Inventory.objects
        .filter(warehouse=OuterRef('pk'))
        .values('warehouse')
        .annotate(s=Sum('quantity', output_field=qty_field))
        .values('s')[:1],
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

    if dt_field:
        moves_qs = moves_qs.order_by(f"-{dt_field}")
    else:
        moves_qs = moves_qs.order_by("-pk")

    recent_moves = list(moves_qs[:20])

    return render(request, "core/warehouse_dashboard.html", {
        "warehouses": warehouses,
        # ...
        "recent_moves": recent_moves,  # <— обязательно передаём
    })

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

@warehouse_or_director_required
def product_list(request):
    q = (request.GET.get("q") or "").strip()
    sort = (request.GET.get("sort") or "name").lower()   # name | price | supplier
    order = (request.GET.get("order") or "asc").lower()  # asc | desc
    page = int(request.GET.get("page") or 1)
    per_page = int(request.GET.get("per_page") or 24)

    qs = (Product.objects
          .all()
          .select_related("supplier")
          .prefetch_related("images", "prices"))

    # фильтр при вводе (минимум 3 символа)
    if len(q) >= 3:
        qs = qs.filter(
            Q(name__icontains=q) |
            Q(barcode__startswith=q) |
            Q(sku__icontains=q) |
            Q(brand__icontains=q) |
            Q(vendor_code__icontains=q)
        )

    # минимальная цена по товару
    qs = qs.annotate(min_price=Min("prices__value"))

    # сортировки
    if sort == "price":
        qs = qs.order_by(
            OrderBy(F("min_price"), descending=(order == "desc"), nulls_last=True),
            "id"
        )
    elif sort == "supplier":
        # сортируем по названию поставщика
        qs = qs.order_by(
            OrderBy(F("supplier__name"), descending=(order == "desc")),
            "id"
        )
    else:
        # по умолчанию — по названию
        sort_field = "name"
        if order == "desc":
            sort_field = "-" + sort_field
        qs = qs.order_by(sort_field, "id")

    paginator = Paginator(qs, per_page)
    page_obj = paginator.get_page(page)

    return render(request, "core/product_list.html", {
        "page_obj": page_obj,
        "q": q,
        "sort": sort,
        "order": order,
        "per_page": per_page,
    })
@login_required
def home(request):
    # можешь использовать уже существующий шаблон дашборда склада
    return render(request, "home.html")

@warehouse_or_director_required
def product_detail_json(request, pk: int):
    try:
        p = Product.objects.annotate(min_price=Min("prices__value")).get(pk=pk)
    except Product.DoesNotExist:
        raise Http404

    images = list(p.images.order_by("position", "id").values_list("url", flat=True))
    certs = [
        {
            "name": c.name, "issued_by": c.issued_by,
            "active_to": c.active_to, "url": c.url
        }
        for c in p.certificates.all()
    ]
    prices = [
        {"type": pr.price_type, "value": str(pr.value), "currency": pr.currency}
        for pr in p.prices.all().order_by("price_type")
    ]

    data = {
        "id": p.id,
        "name": p.name,
        "name_1c": p.name_1c,
        "brand": p.brand,
        "vendor_code": p.vendor_code,
        "barcode": p.barcode,
        "supplier": getattr(p.supplier, "code", None),
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
        "min_price": str(p.min_price) if p.min_price is not None else None,
        "images": images,
        "certificates": certs,
        "prices": prices,
    }
    return JsonResponse(data)

@login_required
def warehouse_list(request):
    warehouses = Warehouse.objects.filter(is_active=True)
    return render(request, "core/warehouse_list.html", {"warehouses": warehouses})

@login_required
@group_required("warehouse", "director")
def warehouse_detail(request, pk: int):
    from .models import Warehouse, StorageBin, Inventory

    warehouse = get_object_or_404(Warehouse, pk=pk)

    bins = (
        StorageBin.objects.filter(warehouse=warehouse, is_active=True)
        .order_by("code")
    )

    # Показать только позиции с количеством > 0
    inventory = (
        Inventory.objects.filter(warehouse=warehouse, quantity__gt=0)
        .select_related("bin", "product")
        .order_by("bin__code", "product__barcode")
    )

    return render(
        request,
        "core/warehouse_detail.html",
        {"warehouse": warehouse, "bins": bins, "inventory": inventory},
    )




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

        # qty
        try:
            qty = Decimal(qty_str.replace(",", "."))
        except Exception:
            messages.error(request, "Некорректное количество.")
            return redirect("put_away", pk=warehouse.pk)
        if qty <= 0:
            messages.error(request, "Количество должно быть > 0.")
            return redirect("put_away", pk=warehouse.pk)

        # товар под блокировкой
        try:
            product = Product.objects.select_for_update().get(barcode=barcode)
        except Product.DoesNotExist:
            messages.error(request, f"Товар со штрихкодом {barcode} не найден.")
            return redirect("put_away", pk=warehouse.pk)

        # ячейка (опционально)
        bin_obj = None
        if bin_code:
            bin_obj = (
                StorageBin.objects.select_for_update()
                .filter(warehouse=warehouse, code=bin_code)
                .first()
            )
            if not bin_obj:
                if create_bin:
                    bin_obj = StorageBin.objects.create(
                        warehouse=warehouse, code=bin_code, is_active=True
                    )
                else:
                    messages.error(request, f"Ячейка {bin_code} не найдена.")
                    return redirect("put_away", pk=warehouse.pk)

        # консолидация остатков (одна строка на (warehouse, product, bin))
        qs = (
            Inventory.objects.select_for_update()
            .filter(warehouse=warehouse, product=product, bin=bin_obj)
            .order_by("pk")
        )

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

        # лог движения
        const = movement_const(StockMovement)

        # безопасно определяем имена полей
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

    # GET — форма
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

        # количество
        try:
            qty = Decimal(qty_str.replace(",", "."))
        except Exception:
            messages.error(request, "Некорректное количество.")
            return redirect("move_between_bins", pk=warehouse.pk)
        if qty <= 0:
            messages.error(request, "Количество должно быть > 0.")
            return redirect("move_between_bins", pk=warehouse.pk)

        # товар под блокировкой
        try:
            product = Product.objects.select_for_update().get(barcode=barcode)
        except Product.DoesNotExist:
            messages.error(request, f"Товар со штрихкодом {barcode} не найден.")
            return redirect("move_between_bins", pk=warehouse.pk)

        # ячейки
        from_bin = None
        if from_code:
            from_bin = (StorageBin.objects
                        .select_for_update()
                        .filter(warehouse=warehouse, code=from_code).first())
            if not from_bin:
                messages.error(request, f"Ячейка-источник «{from_code}» не найдена.")
                return redirect("move_between_bins", pk=warehouse.pk)

        to_bin = None
        if to_code:
            to_bin = (StorageBin.objects
                      .select_for_update()
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

        # уменьшить в источнике (консолидация дублей)
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

        # добавить в получателе (консолидация дублей)
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

        # лог движения (универсальный fallback)
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

    # GET — показать форму
    bins = StorageBin.objects.filter(warehouse=warehouse, is_active=True).order_by("code")
    return render(request, "core/move.html", {"warehouse": warehouse, "bins": bins})

@login_required
@group_required("director")  # или: @permission_required("core.add_warehouse", raise_exception=True)
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
    """
    Жёсткое удаление склада. FK в моделях настроены на CASCADE,
    так что удалятся ячейки/остатки/движения, связанные со складом.
    """
    wh = get_object_or_404(Warehouse, pk=pk)

    if request.method == "POST":
        name = f"{wh.code} — {wh.name}"
        wh.delete()
        messages.success(request, f"Склад «{name}» удалён.")
        return redirect("warehouse_dashboard")

    # GET -> страница подтверждения
    return render(request, "core/warehouse_confirm_delete.html", {"warehouse": wh})

@login_required
@group_required("warehouse", "director")
@transaction.atomic
def inventory_edit(request, warehouse_pk: int, pk: int):
    warehouse = get_object_or_404(Warehouse, pk=warehouse_pk)
    inv = get_object_or_404(Inventory.objects.select_for_update(), pk=pk, warehouse=warehouse)

    from .forms import InventoryEditForm
    if request.method == "POST":
        # явная кнопка удаления
        if "delete" in request.POST:
            inv.delete()
            messages.success(request, "Позиция удалена.")
            return redirect("warehouse_detail", pk=warehouse.pk)

        form = InventoryEditForm(request.POST, warehouse=warehouse)
        if form.is_valid():
            new_bin = form.cleaned_data["bin"]
            new_qty = form.cleaned_data["quantity"]

            # 0 -> удалить
            if new_qty == 0:
                inv.delete()
                messages.success(request, "Позиция удалена (кол-во = 0).")
                return redirect("warehouse_detail", pk=warehouse.pk)

            # смена ячейки или qty
            moved_bin = (new_bin != inv.bin)
            if moved_bin:
                # если уже есть позиция для того же товара в новой ячейке — объединим
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

@login_required
@group_required("warehouse", "director")
def bin_create(request, pk: int):
    warehouse = get_object_or_404(Warehouse, pk=pk)
    if request.method == "POST":
        form = StorageBinForm(request.POST, warehouse=warehouse)
        if form.is_valid():
            StorageBin.objects.create(
                warehouse=warehouse,
                code=form.cleaned_data["code"],
                description=form.cleaned_data.get("description") or "",
                is_active=form.cleaned_data.get("is_active", True),
            )
            messages.success(request, "Ячейка создана.")
            return redirect("warehouse_detail", pk=warehouse.pk)
    else:
        form = StorageBinForm(warehouse=warehouse, initial={"is_active": True})
    return render(request, "core/bin_form.html", {"warehouse": warehouse, "form": form, "title": "Новая ячейка"})

@login_required
@group_required("warehouse", "director")
def bin_edit(request, warehouse_pk: int, pk: int):
    warehouse = get_object_or_404(Warehouse, pk=warehouse_pk)
    bin_obj = get_object_or_404(StorageBin, pk=pk, warehouse=warehouse)
    if request.method == "POST":
        form = StorageBinForm(request.POST, instance=bin_obj, warehouse=warehouse)
        if form.is_valid():
            form.instance.warehouse = warehouse
            form.save()
            messages.success(request, "Ячейка сохранена.")
            return redirect("warehouse_detail", pk=warehouse.pk)
    else:
        form = StorageBinForm(instance=bin_obj, warehouse=warehouse)
    return render(request, "core/bin_form.html", {"warehouse": warehouse, "form": form, "title": f"Ячейка {bin_obj.code}"})

@login_required
@group_required("warehouse", "director")
@transaction.atomic
def bin_delete(request, warehouse_pk: int, pk: int):
    warehouse = get_object_or_404(Warehouse, pk=warehouse_pk)
    bin_obj = get_object_or_404(StorageBin.objects.select_for_update(), pk=pk, warehouse=warehouse)

    # запрет удалять, если есть остатки
    has_stock = Inventory.objects.filter(warehouse=warehouse, bin=bin_obj, quantity__gt=0).exists()
    if has_stock:
        messages.error(request, "Нельзя удалить ячейку: в ней есть остатки.")
        return redirect("warehouse_detail", pk=warehouse.pk)

    bin_obj.delete()  # жёсткое удаление
    messages.success(request, "Ячейка удалена.")
    return redirect("warehouse_detail", pk=warehouse.pk)