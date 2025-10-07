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
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .forms import (
    CounterpartyCreateForm,
    ContactFormSet,
    CounterpartyContactForm,
)
from .models import (
    Counterparty,
    CounterpartyFinance,
    CounterpartyContact,
)
from .services.egrul import (
    EgrulError,
    fetch_by_inn,
    fetch_finance_by_inn,
    parse_counterparty_payload,
)

# =========================
# Helpers (права)
# =========================

def _in_groups(user, names):
    return user.is_authenticated and user.groups.filter(name__in=names).exists()

def _is_operator_or_director(user):
    return _in_groups(user, ["operator", "director"])

def _is_ops_mgr_dir(user):
    return _in_groups(user, ["operator", "manager", "director"])

def _is_attached_manager(user, counterparty: Counterparty):
    """Пользователь — менеджер, прикреплённый к контрагенту?"""
    return (
        _in_groups(user, ["manager"])
        and counterparty.managers.filter(pk=user.pk).exists()
    )

# =========================
# Создание / автоподбор по ИНН
# =========================

@login_required
@user_passes_test(_is_operator_or_director)
def counterparty_create(request):
    """
    Оператор/директор создают контрагента.
    Кнопка «Найти в ЕГРЮЛ» подтягивает данные, а также можно добавить контакты (formset).
    """
    if request.method == "POST":
        form = CounterpartyCreateForm(request.POST)
        formset = ContactFormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            inn = form.cleaned_data["inn"]
            obj, created = Counterparty.objects.get_or_create(
                inn=inn,
                defaults=form.cleaned_data,
            )
            if not created:
                # Обновим ключевые поля (если правили вручную)
                for f in ["name", "full_name", "registration_country", "kpp", "ogrn", "address", "website"]:
                    setattr(obj, f, form.cleaned_data.get(f))
            obj.save()

            # Привяжем контакты из инлайн-форм
            formset.instance = obj
            formset.save()

            # Подтянем финансы (не критично, если ошибка)
            try:
                fin_json, revenue, profit = fetch_finance_by_inn(inn)
                CounterpartyFinance.objects.update_or_create(
                    counterparty=obj,
                    defaults={
                        "data": fin_json,
                        "revenue_last": revenue,
                        "profit_last": profit,
                    },
                )
            except EgrulError as e:
                messages.warning(request, f"Не удалось обновить финансы: {e}")

            messages.success(request, "Контрагент сохранён.")
            return redirect("core:counterparty_detail", pk=obj.pk)
    else:
        form = CounterpartyCreateForm()
        formset = ContactFormSet()

    return render(request, "core/counterparty_form.html", {"form": form, "formset": formset})


@login_required
@user_passes_test(_is_operator_or_director)
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
        payload.pop("meta_json", None)  # в форме не нужен «тяжёлый» блок
        return JsonResponse({"ok": True, "data": payload})
    except EgrulError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=502)

# =========================
# Детальная / финансы
# =========================

@login_required
def counterparty_detail(request, pk: int):
    """
    Доступ:
      - operator/director: всегда
      - manager: только если прикреплён к этому контрагенту
    """
    obj = get_object_or_404(
        Counterparty.objects.select_related("finance").prefetch_related("contacts", "managers"),
        pk=pk,
    )

    if not (_is_operator_or_director(request.user) or _is_attached_manager(request.user, obj)):
        return HttpResponseForbidden("Недостаточно прав")

    # Флаги для шаблона: кто что может
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

# =========================
# Редактирование / удаление контрагента (только operator/director)
# =========================

@login_required
@user_passes_test(_is_operator_or_director)
def counterparty_update(request, pk: int):
    obj = get_object_or_404(Counterparty, pk=pk)
    if request.method == "POST":
        form = CounterpartyCreateForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, "Контрагент обновлён.")
            return redirect("core:counterparty_detail", pk=obj.pk)
    else:
        form = CounterpartyCreateForm(instance=obj)
    return render(request, "core/counterparty_form.html", {"form": form, "edit_mode": True})


@login_required
@user_passes_test(_is_operator_or_director)
@require_http_methods(["GET", "POST"])
def counterparty_delete(request, pk: int):
    obj = get_object_or_404(Counterparty, pk=pk)
    if request.method == "POST":
        obj.delete()
        messages.success(request, "Контрагент удалён.")
        return redirect("core:counterparty_list")
    return render(request, "core/counterparty_confirm_delete.html", {"obj": obj})

# =========================
# Контакты
# =========================

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

# =========================
# Менеджеры у контрагента
# =========================

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

# =========================
# Список клиентов
# =========================

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
