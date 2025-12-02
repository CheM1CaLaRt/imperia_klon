from django.db.models import Q
from django.core.paginator import Paginator
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, redirect, render
from django.db import transaction
from .forms import CounterpartyCreateRequestForm, CounterpartyCreateRequestDocFormSet
from .models import CounterpartyCreateRequest
from .utils.roles import is_manager, can_review
from django.contrib.auth.decorators import login_required, user_passes_test
try:
    from .utils.roles import is_operator, is_director, is_manager
except Exception:
    # fallback, если utils.roles нет
    def _in(u, names):
        return u.is_authenticated and (u.is_superuser or u.groups.filter(name__in=names).exists())
    def is_operator(u): return _in(u, ["operator"])
    def is_director(u): return _in(u, ["director"])
    def is_manager(u):  return _in(u, ["manager"])


PRESET_REASONS = [
    "Дубликат",
    "Ошибочные данные",
    "Нет подтверждающих документов",
    "Не наш клиент",
    "Создать повторно",
]

# Менеджер: создать заявку
@login_required
@user_passes_test(is_manager)
def counterparty_request_create(request):
    if request.method == "POST":
        form = CounterpartyCreateRequestForm(request.POST)
        if form.is_valid():
            req: CounterpartyCreateRequest = form.save(commit=False)
            req.manager = request.user   # ← сразу проставляем менеджера-создателя
            req.status = CounterpartyCreateRequest.Status.PENDING
            try:
                req.full_clean()
                req.save()
            except Exception as e:
                messages.error(request, f"Ошибка: {e}")
            else:
                messages.success(request, "Заявка отправлена оператору/директору на подтверждение.")
                return redirect("manager_counterparty_requests")
    else:
        form = CounterpartyCreateRequestForm()
    return render(request, "counterparty/request_create.html", {"form": form})

# Менеджер: список своих заявок
@login_required
@user_passes_test(is_manager)
def manager_counterparty_requests(request):
    qs = (CounterpartyCreateRequest.objects
          .filter(manager=request.user)
          .order_by("-created_at"))

    # GET-параметры
    status = (request.GET.get("status") or "").strip()
    q = (request.GET.get("q") or "").strip()

    # Фильтр статуса (только допустимые значения)
    valid_status = {
        CounterpartyCreateRequest.Status.PENDING,
        CounterpartyCreateRequest.Status.APPROVED,
        CounterpartyCreateRequest.Status.REJECTED,
    }
    # сравниваем по значению ('pending'/'approved'/'rejected'), а не по метке
    if status in {s.value for s in valid_status}:
        qs = qs.filter(status=status)

    # Поиск по ИНН/наименованию
    if q:
        qs = qs.filter(Q(inn__icontains=q) | Q(name__icontains=q))

    # Пагинация (по 18 карточек)
    paginator = Paginator(qs, 18)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "counterparty/manager_requests_list.html",
        {
            "requests": page_obj.object_list,
            "page_obj": page_obj,
        },
    )

# Оператор/Директор: очередь на подтверждение
@login_required
@user_passes_test(lambda u: is_operator(u) or is_director(u))
def counterparty_review_queue(request):
    qs = (CounterpartyCreateRequest.objects
          .select_related("manager")
          .order_by("-created_at"))

    # фильтры
    status = request.GET.get("status", "pending").strip()
    q = request.GET.get("q", "").strip()

    if status in {"pending", "approved", "rejected"}:
        qs = qs.filter(status=status)

    if q:
        qs = qs.filter(
            Q(inn__icontains=q) |
            Q(name__icontains=q) |
            Q(manager__username__icontains=q) |
            Q(manager__first_name__icontains=q) |
            Q(manager__last_name__icontains=q)
        )

    # счетчик ожидающих — для бейджа в заголовке
    pending_count = CounterpartyCreateRequest.objects.filter(
        status=CounterpartyCreateRequest.Status.PENDING
    ).count()

    # пагинация (по желанию)
    paginator = Paginator(qs, 18)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "counterparty/review_queue.html",
        {
            "requests": page_obj.object_list,
            "page_obj": page_obj,
            "pending_count": pending_count,
            "presets": PRESET_REASONS,
        },
    )

# Подтвердить
@login_required
@user_passes_test(can_review)
@transaction.atomic
def counterparty_request_approve(request, pk: int):
    req = get_object_or_404(CounterpartyCreateRequest, pk=pk)
    comment = request.POST.get("comment") or ""
    try:
        cp = req.approve(reviewer=request.user, comment=comment)
        messages.success(request, f"Контрагент «{cp.name}» ({cp.inn}) создан/обновлён.")
    except ValueError as e:
        messages.warning(request, str(e))
    return redirect("core:counterparty_review_queue")

# Отклонить
@login_required
@user_passes_test(can_review)
@transaction.atomic
def counterparty_request_reject(request, pk: int):
    req = get_object_or_404(CounterpartyCreateRequest, pk=pk)
    comment = request.POST.get("comment") or "Отклонено без комментария"
    try:
        req.reject(reviewer=request.user, comment=comment)
        messages.info(request, "Заявка отклонена.")
    except ValueError as e:
        messages.warning(request, str(e))
    return redirect("core:counterparty_review_queue")

@login_required
@user_passes_test(is_manager)
def counterparty_request_create(request):
    if request.method == "POST":
        form = CounterpartyCreateRequestForm(request.POST)
        formset = CounterpartyCreateRequestDocFormSet(request.POST, request.FILES, prefix="docs")
        if form.is_valid() and formset.is_valid():
            req = form.save(commit=False)
            req.manager = request.user
            req.status = CounterpartyCreateRequest.Status.PENDING
            req.save()
            # привязываем файлы к созданной заявке
            formset.instance = req
            formset.save()
            messages.success(request, "Заявка отправлена оператору/директору на подтверждение.")
            return redirect("core:manager_counterparty_requests")
    else:
        form = CounterpartyCreateRequestForm()
        formset = CounterpartyCreateRequestDocFormSet(prefix="docs")

    return render(
        request,
        "counterparty/request_create.html",
        {"form": form, "doc_formset": formset},
    )