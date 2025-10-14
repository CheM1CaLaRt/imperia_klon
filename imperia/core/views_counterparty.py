# core/views_counterparty.py
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Q
from django.http import (
    JsonResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods
import json

from .services.egrul import (
    EgrulError,
    fetch_by_inn,
    fetch_finance_by_inn,
    parse_counterparty_payload,
)

from .models import (
    Counterparty,
    CounterpartyFinance,
    CounterpartyContact,
    # заявки на удаление
    CounterpartyDeletionRequest,
)

from .forms import (
    CounterpartyCreateForm,
    ContactFormSet,
    CounterpartyContactForm,
    CounterpartyDocumentFormSet,
    # комментарий к заявке на удаление (для оператора)
    CounterpartyDeletionRequestForm,
)

# ============================================================
# Helpers (права)
# ============================================================

def _in_groups(user, names):
    return user.is_authenticated and user.groups.filter(name__in=names).exists()

def _is_operator_or_director(user):
    return _in_groups(user, ["operator", "director"])

def _is_ops_mgr_dir(user):
    return _in_groups(user, ["operator", "manager", "director"])

def _is_operator(user):
    return _in_groups(user, ["operator"])

def _is_director(user):
    # позволяем суперпользователю выполнять действия директора
    return _in_groups(user, ["director"]) or user.is_superuser

def _is_attached_manager(user, counterparty: Counterparty):
    """Пользователь — менеджер, прикреплённый к контрагенту?"""
    return (
        _in_groups(user, ["manager"])
        and counterparty.managers.filter(pk=user.pk).exists()
    )

# ============================================================
# Создание / автоподбор по ИНН
# ============================================================

@login_required
@user_passes_test(_is_operator_or_director)
def counterparty_create(request):
    """
    Создание контрагента + опциональные документы.
    """
    if request.method == "POST":
        form = CounterpartyCreateForm(request.POST)
        if form.is_valid():
            obj = form.save()
            doc_formset = CounterpartyDocumentFormSet(
                data=request.POST, files=request.FILES, instance=obj
            )
            if doc_formset.is_valid():
                doc_formset.save()
                messages.success(request, "Контрагент создан.")
                return redirect("core:counterparty_detail", pk=obj.pk)
            else:
                messages.error(request, "Проверьте блок «Сканы документов».")
        else:
            messages.error(request, "Проверьте корректность полей.")
            doc_formset = CounterpartyDocumentFormSet(instance=Counterparty())
    else:
        form = CounterpartyCreateForm()
        doc_formset = CounterpartyDocumentFormSet(instance=Counterparty())

    return render(
        request,
        "core/counterparty_form.html",
        {"form": form, "doc_formset": doc_formset},
    )


@login_required
@user_passes_test(_is_operator_or_director)
def counterparty_update(request, pk: int):
    obj = get_object_or_404(Counterparty, pk=pk)

    if request.method == "POST":
        form = CounterpartyCreateForm(request.POST, instance=obj)
        doc_formset = CounterpartyDocumentFormSet(
            data=request.POST, files=request.FILES, instance=obj
        )
        if form.is_valid() and doc_formset.is_valid():
            form.save()
            doc_formset.save()
            messages.success(request, "Контрагент обновлён.")
            return redirect("core:counterparty_detail", pk=obj.pk)
        else:
            messages.error(request, "Проверьте форму и сканы документов.")
    else:
        form = CounterpartyCreateForm(instance=obj)
        doc_formset = CounterpartyDocumentFormSet(instance=obj)

    return render(
        request,
        "core/counterparty_form.html",
        {"form": form, "doc_formset": doc_formset, "edit_mode": True},
    )


@login_required
# @user_passes_test(_is_operator_or_director)
@require_GET
def counterparty_lookup_inn(request):
    """
    AJAX: /counterparties/lookup/?inn=...
    Возвращаем «лёгкий» JSON для автозаполнения формы.
    """
    inn = (request.GET.get("inn") or "").strip()
    if not inn.isdigit() or len(inn) not in (10, 12):
        return HttpResponseBadRequest("Некорректный ИНН")

    try:
        raw = fetch_by_inn(inn)
        payload = parse_counterparty_payload(raw)
        payload.pop("meta_json", None)
        return JsonResponse({"ok": True, "data": payload})
    except EgrulError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=502)

# ============================================================
# Детальная / финансы
# ============================================================

@login_required
def counterparty_detail(request, pk: int):
    """
    Доступ:
      - operator/director: всегда
      - manager: только если прикреплён к этому контрагенту
    """
    obj = get_object_or_404(
        Counterparty.objects.select_related("finance")
        .prefetch_related("contacts", "managers", "documents"),
        pk=pk,
    )

    if not (_is_operator_or_director(request.user) or _is_attached_manager(request.user, obj)):
        return HttpResponseForbidden("Недостаточно прав")

    can_edit_client = _is_operator_or_director(request.user)
    can_add_contact = can_edit_client or _is_attached_manager(request.user, obj)
    can_edit_contact = _is_operator_or_director(request.user)

    return render(
        request,
        "core/counterparty_detail.html",
        {
            "obj": obj,
            "can_edit_client": can_edit_client,
            "can_add_contact": can_add_contact,
            "can_edit_contact": can_edit_contact,
        },
    )


@login_required
@user_passes_test(_is_operator_or_director)
@require_POST
def counterparty_refresh_finance(request, pk: int):
    obj = get_object_or_404(Counterparty, pk=pk)
    try:
        fin_json, revenue, profit = fetch_finance_by_inn(obj.inn)
        CounterpartyFinance.objects.update_or_create(
            counterparty=obj,
            defaults={"data": fin_json, "revenue_last": revenue, "profit_last": profit},
        )
        messages.success(request, "Финансовые данные обновлены.")
    except EgrulError as e:
        messages.error(request, f"Не удалось обновить финансы: {e}")
    return redirect("core:counterparty_detail", pk=obj.pk)

# ============================================================
# Удаление / заявки на удаление
# ============================================================

@login_required
@require_http_methods(["GET", "POST"])
def counterparty_delete(request, pk: int):
    """
    - operator: вместо удаления оформляет заявку (comment + PENDING)
    - director/superuser: реальное удаление
    """
    obj = get_object_or_404(Counterparty, pk=pk)

    # Оператор — только запрос на удаление
    if _is_operator(request.user) and not _is_director(request.user):
        if request.method == "POST":
            form = CounterpartyDeletionRequestForm(request.POST)
            if form.is_valid():
                # не плодим дубликаты PENDING-заявок
                already = CounterpartyDeletionRequest.objects.filter(
                    counterparty=obj,
                    status=CounterpartyDeletionRequest.Status.PENDING,
                ).exists()
                if already:
                    messages.info(request, "Заявка на удаление уже находится на рассмотрении.")
                    return redirect("core:counterparty_detail", pk=obj.pk)

                CounterpartyDeletionRequest.objects.create(
                    counterparty=obj,
                    requested_by=request.user,
                    comment=form.cleaned_data.get("comment", ""),
                    status=CounterpartyDeletionRequest.Status.PENDING,
                )
                messages.success(request, "Заявка на удаление отправлена управляющему.")
                return redirect("core:counterparty_detail", pk=obj.pk)
        else:
            form = CounterpartyDeletionRequestForm()

        return render(
            request,
            "core/counterparty_confirm_delete.html",
            {"obj": obj, "is_operator_request": True, "form": form},
        )

    # Директор — удаляет сразу
    if not _is_director(request.user):
        messages.error(request, "Недостаточно прав для удаления.")
        return redirect("core:counterparty_detail", pk=obj.pk)

    if request.method == "POST":
        obj.delete()
        messages.success(request, "Контрагент удалён.")
        return redirect("core:director_dashboard")

    return render(
        request,
        "core/counterparty_confirm_delete.html",
        {"obj": obj, "is_operator_request": False},
    )

# === Дашборд директора и обработка заявок ===

@login_required
@user_passes_test(_is_director)
def director_dashboard(request):
    pending = (
        CounterpartyDeletionRequest.objects
        .filter(status=CounterpartyDeletionRequest.Status.PENDING)
        .select_related("counterparty", "requested_by")
        .order_by("-created_at")
    )
    return render(request, "core/director_dashboard.html", {"pending": pending})

@login_required
@user_passes_test(_is_director)
@require_POST
def deletion_request_approve(request, req_id: int):
    dr = get_object_or_404(
        CounterpartyDeletionRequest.objects.select_related("counterparty"),
        pk=req_id,
        status=CounterpartyDeletionRequest.Status.PENDING,
    )
    dr.status = CounterpartyDeletionRequest.Status.APPROVED
    dr.reviewed_by = request.user
    dr.reviewed_at = timezone.now()
    dr.save(update_fields=["status", "reviewed_by", "reviewed_at"])

    name = str(dr.counterparty)
    dr.counterparty.delete()
    messages.success(request, f"Контрагент «{name}» удалён.")
    return redirect("core:director_dashboard")

@login_required
@user_passes_test(_is_director)
@require_POST
def deletion_request_reject(request, req_id: int):
    dr = get_object_or_404(
        CounterpartyDeletionRequest,
        pk=req_id,
        status=CounterpartyDeletionRequest.Status.PENDING,
    )
    dr.status = CounterpartyDeletionRequest.Status.REJECTED
    dr.reviewed_by = request.user
    dr.reviewed_at = timezone.now()
    dr.save(update_fields=["status", "reviewed_by", "reviewed_at"])
    messages.info(request, "Заявка отклонена.")
    return redirect("core:director_dashboard")

# === Дашборд оператора и отмена своей заявки ===

@login_required
@user_passes_test(_is_operator)
def operator_dashboard(request):
    my_requests = (
        CounterpartyDeletionRequest.objects
        .filter(requested_by=request.user)
        .select_related("counterparty")
        .order_by("-created_at")
    )
    has_rejected = my_requests.filter(
        status=CounterpartyDeletionRequest.Status.REJECTED
    ).exists()

    return render(
        request,
        "core/operator_dashboard.html",
        {"my_requests": my_requests, "has_rejected": has_rejected},
    )

@login_required
@user_passes_test(_is_operator)
@require_POST
def deletion_request_cancel(request, req_id: int):
    """Отменить свою неподтверждённую (PENDING) заявку."""
    dr = get_object_or_404(
        CounterpartyDeletionRequest,
        pk=req_id,
        requested_by=request.user,
        status=CounterpartyDeletionRequest.Status.PENDING,
    )
    dr.delete()
    messages.info(request, "Заявка отменена.")
    return redirect("core:operator_dashboard")

@login_required
@user_passes_test(_is_operator)
@require_POST
def deletion_requests_clear_rejected(request):
    """Очистить все ОТКЛОНЁННЫЕ заявки текущего оператора."""
    qs = CounterpartyDeletionRequest.objects.filter(
        requested_by=request.user,
        status=CounterpartyDeletionRequest.Status.REJECTED,
    )
    count = qs.count()
    qs.delete()
    if count:
        messages.success(request, f"Удалено отклонённых заявок: {count}.")
    else:
        messages.info(request, "Отклонённых заявок нет.")
    return redirect("core:operator_dashboard")

# ============================================================
# Контакты
# ============================================================

@login_required
def contact_add(request, pk: int):
    """
    Добавить контакт могут:
      - operator/director
      - менеджер, прикреплённый к этому контрагенту
    """
    obj = get_object_or_404(Counterparty, pk=pk)
    if not (_is_operator_or_director(request.user) or _is_attached_manager(request.user, obj)):
        return HttpResponseForbidden("Недостаточно прав")

    if request.method == "POST":
        form = CounterpartyContactForm(request.POST)
        if form.is_valid():
            CounterpartyContact.objects.create(counterparty=obj, **form.cleaned_data)
            messages.success(request, "Контакт добавлен.")
            return redirect("core:counterparty_detail", pk=obj.pk)
    else:
        form = CounterpartyContactForm()
    return render(request, "core/contact_form.html", {"obj": obj, "form": form})


@login_required
@user_passes_test(_is_operator_or_director)
def contact_edit(request, pk: int, contact_id: int):
    obj = get_object_or_404(Counterparty, pk=pk)
    contact = get_object_or_404(CounterpartyContact, pk=contact_id, counterparty=obj)
    if request.method == "POST":
        form = CounterpartyContactForm(request.POST, instance=contact)
        if form.is_valid():
            form.save()
            messages.success(request, "Контакт обновлён.")
            return redirect("core:counterparty_detail", pk=obj.pk)
    else:
        form = CounterpartyContactForm(instance=contact)
    return render(request, "core/contact_form.html", {"obj": obj, "form": form, "edit_mode": True})


@login_required
@user_passes_test(_is_operator_or_director)
@require_http_methods(["GET", "POST"])
def contact_delete(request, pk: int, contact_id: int):
    obj = get_object_or_404(Counterparty, pk=pk)
    contact = get_object_or_404(CounterpartyContact, pk=contact_id, counterparty=obj)
    if request.method == "POST":
        contact.delete()
        messages.success(request, "Контакт удалён.")
        return redirect("core:counterparty_detail", pk=obj.pk)
    return render(request, "core/contact_confirm_delete.html", {"obj": obj, "contact": contact})

# ============================================================
# Менеджеры у контрагента
# ============================================================

@login_required
@require_POST
@user_passes_test(_is_operator_or_director)
def counterparty_manager_remove(request, pk: int, user_id: int):
    obj = get_object_or_404(Counterparty, pk=pk)
    User = get_user_model()
    user = get_object_or_404(User, pk=user_id)
    obj.managers.remove(user)
    messages.success(
        request,
        f"Менеджер «{user.get_full_name() or user.username}» снят с контрагента.",
    )
    return redirect("core:counterparty_detail", pk=pk)

# ============================================================
# Список клиентов
# ============================================================

@login_required
@user_passes_test(_is_ops_mgr_dir)
def counterparty_list(request):
    """
    Видят operator/manager/director.
    Менеджер видит только контрагентов, к которым прикреплён.
    Поиск по названию/пол. названию/ИНН.
    """
    q = (request.GET.get("q") or "").strip()
    qs = Counterparty.objects.all().prefetch_related("managers").order_by("name")

    is_manager = request.user.groups.filter(name="manager").exists()
    is_director = request.user.groups.filter(name="director").exists()
    is_operator = request.user.groups.filter(name="operator").exists()

    if is_manager and not (is_director or is_operator):
        qs = qs.filter(managers=request.user)

    if q:
        qs = qs.filter(
            Q(name__icontains=q) |
            Q(full_name__icontains=q) |
            Q(inn__icontains=q)
        )

    return render(request, "core/counterparty_list.html", {"objects": qs, "q": q})

# ============================================================
# Подсказки адресов через OSM Nominatim (бесплатно)
# ============================================================

@login_required
@require_GET
def address_suggest_osm(request):
    """
    GET ?q=ул тверская 7  → {"suggestions":[{"value":"..."}]}
    Проксирующий ендпоинт для клиента. Источник — OSM Nominatim.
    """
    q = (request.GET.get("q") or "").strip()
    if len(q) < 3:
        return JsonResponse({"suggestions": []})

    params = {
        "q": q,
        "format": "json",
        "addressdetails": 0,
        "limit": 5,
        "accept-language": "ru",
    }
    headers = {
        "User-Agent": "ImperiaApp/1.0 (admin@example.com)"
    }

    suggestions = []

    try:
        import requests  # noqa: F401
        try:
            r = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params=params,
                headers=headers,
                timeout=3,
            )
            r.raise_for_status()
            data = r.json()
            suggestions = [
                {"value": item.get("display_name", "")}
                for item in data
                if item.get("display_name")
            ]
        except Exception:
            suggestions = []
    except Exception:
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen

        try:
            url = "https://nominatim.openstreetmap.org/search?" + urlencode(params)
            req = Request(url, headers=headers)
            with urlopen(req, timeout=3) as resp:
                raw = resp.read().decode("utf-8", "ignore")
                data = json.loads(raw)
                suggestions = [
                    {"value": item.get("display_name", "")}
                    for item in data
                    if item.get("display_name")
                ]
        except Exception:
            suggestions = []

    return JsonResponse({"suggestions": suggestions})
