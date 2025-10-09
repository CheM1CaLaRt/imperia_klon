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
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .services.egrul import (
    EgrulError,
    fetch_by_inn,
    fetch_finance_by_inn,
    parse_counterparty_payload,
)


from .models import Counterparty, CounterpartyFinance, CounterpartyContact

from .forms import (
    CounterpartyCreateForm,
    ContactFormSet,
    CounterpartyContactForm,
    CounterpartyDocumentFormSet,   # <-- ВАЖНО
)

# --- Подсказки адресов через OSM Nominatim (бесплатно) ---
from django.views.decorators.http import require_GET
import json

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

# ------- CREATE -------
@login_required
@user_passes_test(_is_operator_or_director)
def counterparty_create(request):
    """
    Создание контрагента + опциональные документы.
    """
    if request.method == "POST":
        form = CounterpartyCreateForm(request.POST)
        # сначала валидируем саму карточку
        if form.is_valid():
            obj = form.save()

            # теперь связываем и валидируем formset документов
            doc_formset = CounterpartyDocumentFormSet(
                data=request.POST,
                files=request.FILES,
                instance=obj,
            )
            if doc_formset.is_valid():
                doc_formset.save()
                messages.success(request, "Контрагент создан.")
                return redirect("core:counterparty_detail", pk=obj.pk)
            else:
                # если документы невалидны — показываем ошибки вместе с формой
                messages.error(request, "Проверьте блок «Сканы документов».")
        else:
            # если сама форма невалидна — тоже покажем ошибки
            doc_formset = CounterpartyDocumentFormSet(
                instance=Counterparty()
            )
            messages.error(request, "Проверьте корректность полей.")

    else:
        form = CounterpartyCreateForm()
        # на GET отдаём пустой formset (extra=1)
        doc_formset = CounterpartyDocumentFormSet(
            instance=Counterparty()
        )

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
            data=request.POST,
            files=request.FILES,
            instance=obj,
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
        Counterparty.objects.select_related("finance").prefetch_related("contacts", "managers", "documents"),
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

    # Параметры/заголовки согласно правилам Nominatim
    params = {
        "q": q,
        "format": "json",
        "addressdetails": 0,
        "limit": 5,
        "accept-language": "ru",
    }
    headers = {
        # укажи свой домен/почту проекта
        "User-Agent": "ImperiaApp/1.0 (admin@example.com)"
    }

    suggestions = []

    # Пытаемся через requests; если его нет — через urllib
    try:
        import requests  # noqa
        try:
            r = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params=params,
                headers=headers,
                timeout=3,
            )
            r.raise_for_status()
            data = r.json()
            suggestions = [{"value": item.get("display_name", "")} for item in data if item.get("display_name")]
        except Exception:
            suggestions = []
    except Exception:
        # Fallback на стандартную библиотеку
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen

        try:
            url = "https://nominatim.openstreetmap.org/search?" + urlencode(params)
            req = Request(url, headers=headers)
            with urlopen(req, timeout=3) as resp:
                raw = resp.read().decode("utf-8", "ignore")
                data = json.loads(raw)
                suggestions = [{"value": item.get("display_name", "")} for item in data if item.get("display_name")]
        except Exception:
            suggestions = []

    return JsonResponse({"suggestions": suggestions})
