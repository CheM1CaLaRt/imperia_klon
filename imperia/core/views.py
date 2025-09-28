# core/views.py
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import Group
from django.http import HttpRequest
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from .forms import UserUpdateForm, ProfileForm
from .models import Profile
from .widgets import AvatarInput
from os.path import basename
from django.db.models import Q, Min, F
from django.db.models.expressions import OrderBy
from django.core.paginator import Paginator
from django.shortcuts import render
from .models import Product
from .permissions import warehouse_or_director_required
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, Http404
from django.forms.models import model_to_dict
from django.db.models import Min
from .models import Product, ProductImage, ProductCertificate, ProductPrice
from .permissions import warehouse_or_director_required
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from .models import Warehouse
from .forms import PutAwayForm, MoveForm
from .services.inventory import put_away, move_between_bins
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.db.models import Sum, Count
from django.contrib import messages
from django.shortcuts import redirect, render
from django.contrib.auth.decorators import login_required
from .forms import WarehouseCreateForm
from .utils.auth import group_required
from django.db.models import (
    Count, Sum, OuterRef, Subquery, IntegerField, DecimalField, Value
)
from django.db.models.functions import Coalesce



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
    from .models import Warehouse, StorageBin, Inventory, StockMovement

    # Подзапросы (каждый агрегат отдельно, чтобы не было декартова произведения)
    bins_sq = (
        StorageBin.objects
        .filter(warehouse=OuterRef("pk"))
        .values("warehouse")
        .annotate(c=Count("id"))
        .values("c")[:1]
    )

    inv_pos_sq = (
        Inventory.objects
        .filter(warehouse=OuterRef("pk"))
        .values("warehouse")
        .annotate(c=Count("id"))
        .values("c")[:1]
    )

    inv_qty_sq = (
        Inventory.objects
        .filter(warehouse=OuterRef("pk"))
        .values("warehouse")
        .annotate(s=Sum("quantity"))
        .values("s")[:1]
    )

    DEC = DecimalField(max_digits=14, decimal_places=3)
    INT = IntegerField()

    warehouses = (
        Warehouse.objects.filter(is_active=True)
        .order_by("code")
        .annotate(
            bins_count=Coalesce(Subquery(bins_sq, output_field=INT), Value(0, output_field=INT)),
            inv_positions=Coalesce(Subquery(inv_pos_sq, output_field=INT), Value(0, output_field=INT)),
            inv_qty=Coalesce(Subquery(inv_qty_sq, output_field=DEC), Value(Decimal("0"), output_field=DEC)),
        )
    )

    recent_moves = (
        StockMovement.objects
        .select_related("warehouse", "bin_from", "bin_to", "product", "actor")
        .order_by("-timestamp")[:20]
    )

    ctx = {
        "warehouses": warehouses,
        "recent_moves": recent_moves,
    }
    return render(request, "core/warehouse_dashboard.html", ctx)

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
@group_required("director", "warehouse") #@permission_required("core.add_inventory", raise_exception=True)
def put_away_view(request, pk: int):
    wh = get_object_or_404(Warehouse, pk=pk)
    if request.method == "POST":
        form = PutAwayForm(request.POST)
        if form.is_valid():
            try:
                inv = put_away(
                    warehouse=wh,
                    bin_code=form.cleaned_data["bin_code"],
                    barcode=form.cleaned_data["barcode"],
                    qty=Decimal(form.cleaned_data["quantity"]),
                    actor=request.user,
                    create_bin_if_missing=form.cleaned_data.get("create_bin", True),
                )
                messages.success(request, f"Добавлено в {inv.bin.code if inv.bin else 'склад'}: {inv.product} x{form.cleaned_data['quantity']}")
                return redirect("warehouse_detail", pk=wh.pk)
            except Exception as e:
                messages.error(request, str(e))
    else:
        form = PutAwayForm()
    return render(request, "core/put_away.html", {"warehouse": wh, "form": form})

@login_required
@group_required("director", "warehouse") #@permission_required("core.change_inventory", raise_exception=True)
def move_view(request, pk: int):
    wh = get_object_or_404(Warehouse, pk=pk)
    if request.method == "POST":
        form = MoveForm(request.POST)
        if form.is_valid():
            try:
                move_between_bins(
                    warehouse=wh,
                    barcode=form.cleaned_data["barcode"],
                    qty=Decimal(form.cleaned_data["quantity"]),
                    bin_from_code=form.cleaned_data["bin_from"],
                    bin_to_code=form.cleaned_data["bin_to"],
                    actor=request.user,
                    create_bin_if_missing=form.cleaned_data.get("create_bin", True),
                )
                messages.success(request, "Перемещение выполнено")
                return redirect("warehouse_detail", pk=wh.pk)
            except Exception as e:
                messages.error(request, str(e))
    else:
        form = MoveForm()
    return render(request, "core/move.html", {"warehouse": wh, "form": form})

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
