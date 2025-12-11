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
    CounterpartyAddressFormSet,
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
            address_formset = CounterpartyAddressFormSet(
                data=request.POST, instance=obj
            )
            doc_formset = CounterpartyDocumentFormSet(
                data=request.POST, files=request.FILES, instance=obj
            )
            if address_formset.is_valid() and doc_formset.is_valid():
                address_formset.save()
                doc_formset.save()
                messages.success(request, "Контрагент создан.")
                return redirect("core:counterparty_detail", pk=obj.pk)
            else:
                if not address_formset.is_valid():
                    messages.error(request, "Проверьте блок «Адреса доставки».")
                if not doc_formset.is_valid():
                    messages.error(request, "Проверьте блок «Сканы документов».")
        else:
            messages.error(request, "Проверьте корректность полей.")
            address_formset = CounterpartyAddressFormSet(instance=Counterparty())
            doc_formset = CounterpartyDocumentFormSet(instance=Counterparty())
    else:
        form = CounterpartyCreateForm()
        address_formset = CounterpartyAddressFormSet(instance=Counterparty())
        doc_formset = CounterpartyDocumentFormSet(instance=Counterparty())

    return render(
        request,
        "core/counterparty_form.html",
        {"form": form, "address_formset": address_formset, "doc_formset": doc_formset},
    )


@login_required
@user_passes_test(_is_operator_or_director)
def counterparty_update(request, pk: int):
    obj = get_object_or_404(Counterparty, pk=pk)

    if request.method == "POST":
        form = CounterpartyCreateForm(request.POST, instance=obj)
        address_formset = CounterpartyAddressFormSet(
            data=request.POST, instance=obj
        )
        doc_formset = CounterpartyDocumentFormSet(
            data=request.POST, files=request.FILES, instance=obj
        )
        if form.is_valid() and address_formset.is_valid() and doc_formset.is_valid():
            form.save()
            address_formset.save()
            doc_formset.save()
            messages.success(request, "Контрагент обновлён.")
            return redirect("core:counterparty_detail", pk=obj.pk)
        else:
            if not form.is_valid():
                messages.error(request, "Проверьте форму.")
            if not address_formset.is_valid():
                messages.error(request, "Проверьте блок «Адреса доставки».")
            if not doc_formset.is_valid():
                messages.error(request, "Проверьте блок «Сканы документов».")
    else:
        form = CounterpartyCreateForm(instance=obj)
        # При GET запросе загружаем существующие адреса
        address_formset = CounterpartyAddressFormSet(instance=obj)
        doc_formset = CounterpartyDocumentFormSet(instance=obj)

    return render(
        request,
        "core/counterparty_form.html",
        {"form": form, "address_formset": address_formset, "doc_formset": doc_formset, "edit_mode": True},
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
        .prefetch_related("contacts", "managers", "documents", "addresses"),
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
    Проксирующий ендпоинт для клиента. 
    Пробует несколько источников: Dadata (если есть ключ), затем Nominatim.
    """
    q = (request.GET.get("q") or "").strip()
    if len(q) < 3:
        return JsonResponse({"suggestions": []})

    suggestions = []
    
    # Попытка 1: Dadata API (если есть ключ) - лучший для российских адресов
    # Бесплатный тариф: до 10,000 запросов в день
    # Регистрация: https://dadata.ru/api/
    import os
    dadata_token = os.getenv("DADATA_API_TOKEN")
    dadata_secret = os.getenv("DADATA_API_SECRET")
    
    if dadata_token:
        try:
            import requests
            dadata_url = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Token {dadata_token}",
            }
            # Добавляем Secret только если он указан (для некоторых тарифов не требуется)
            if dadata_secret:
                headers["X-Secret"] = dadata_secret
            
            payload = {
                "query": q,
                "count": 5,
                "language": "ru",
                "locations": [{"country": "*"}],  # Ищем по всему миру, но приоритет России
            }
            
            r = requests.post(dadata_url, json=payload, headers=headers, timeout=3)
            r.raise_for_status()
            data = r.json()
            
            suggestions = [
                {"value": item.get("value", "")}
                for item in data.get("suggestions", [])
                if item.get("value")
            ]
            
            if suggestions:
                import logging
                logger = logging.getLogger(__name__)
                logger.info(f"Dadata API: найдено {len(suggestions)} подсказок для '{q}'")
                return JsonResponse({"suggestions": suggestions})
        except Exception as e:
            # Продолжаем к другим источникам
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Dadata API error: {e}")
            pass
    
    # Попытка 2: Photon API (бесплатный, без ключа, хорошо работает с адресами)
    try:
        import requests
        # Photon API - бесплатный геокодер от Komoot
        # Пробуем несколько вариантов запроса для лучших результатов
        search_variants = [q]
        # Добавляем вариант с "Россия" для приоритета российских адресов
        if "россия" not in q.lower() and "russia" not in q.lower():
            search_variants.append(f"{q}, Россия")
        
        for search_q in search_variants:
            url = f"https://photon.komoot.io/api/?q={requests.utils.quote(search_q)}&limit=10"
            r = requests.get(url, timeout=3)
            if r.ok:
                data = r.json()
                if data.get("features"):
                    suggestions = []
                    seen = set()  # Для избежания дубликатов
                    
                    for feature in data["features"]:
                        props = feature.get("properties", {})
                        
                        # Приоритет российским адресам
                        country = props.get("country", "").lower()
                        countrycode = props.get("countrycode", "").lower()
                        if country not in ("россия", "russia") and countrycode not in ("ru", "ru"):
                            continue
                        
                        # Формируем читаемый адрес
                        parts = []
                        
                        # Улица
                        street = props.get("street") or props.get("name")
                        if street:
                            parts.append(street)
                        
                        # Номер дома
                        if props.get("housenumber"):
                            parts.append(props["housenumber"])
                        
                        # Город
                        city = props.get("city") or props.get("state")
                        if city:
                            parts.append(f", {city}")
                        
                        if parts:
                            formatted = " ".join(parts)
                            # Убираем дубликаты
                            if formatted not in seen:
                                seen.add(formatted)
                                suggestions.append({"value": formatted})
                        
                        if len(suggestions) >= 5:
                            break
                    
                    if suggestions:
                        import logging
                        logger = logging.getLogger(__name__)
                        logger.info(f"Photon API: найдено {len(suggestions)} подсказок для '{q}'")
                        return JsonResponse({"suggestions": suggestions})
                    break  # Если нашли результаты, не пробуем другие варианты
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Photon API error: {e}")
        pass
    
    # Попытка 3: Nominatim с улучшенными параметрами и разными вариантами запроса
    # Пробуем несколько вариантов запроса для лучших результатов
    query_variants = [
        q,  # Как есть
        f"{q}, Россия",  # С добавлением страны
    ]
    
    # Если запрос начинается с "проспект" или "пр.", добавляем город
    if q.lower().startswith(("проспект", "пр.", "пр ")):
        query_variants.extend([
            f"{q}, Санкт-Петербург",
            f"{q}, Санкт-Петербург, Россия",
        ])
    elif q.lower().startswith(("улица", "ул.", "ул ")):
        query_variants.extend([
            f"{q}, Москва",
            f"{q}, Москва, Россия",
        ])
    
    headers = {
        "User-Agent": "ImperiaApp/1.0 (admin@example.com)"
    }

    try:
        import requests
        for query_variant in query_variants:
            try:
                params = {
                    "q": query_variant,
                    "format": "json",
                    "addressdetails": 1,
                    "limit": 10,
                    "accept-language": "ru",
                    "countrycodes": "ru",
                }
                
                r = requests.get(
                    "https://nominatim.openstreetmap.org/search",
                    params=params,
                    headers=headers,
                    timeout=5,
                )
                r.raise_for_status()
                data = r.json()
                
                if data:
                    # Форматируем результаты лучше
                    for item in data:
                        display_name = item.get("display_name", "")
                        if display_name:
                            # Пробуем создать более читаемый адрес
                            address = item.get("address", {})
                            if address:
                                parts = []
                                if address.get("road"):
                                    parts.append(address["road"])
                                if address.get("house_number"):
                                    parts.append(address["house_number"])
                                if address.get("city") or address.get("town"):
                                    city = address.get("city") or address.get("town")
                                    if parts:
                                        parts.append(f", {city}")
                                    else:
                                        parts.append(city)
                                
                                if parts:
                                    formatted = " ".join(parts)
                                    suggestions.append({"value": formatted})
                                else:
                                    suggestions.append({"value": display_name})
                            else:
                                suggestions.append({"value": display_name})
                            
                            if len(suggestions) >= 5:
                                break
                    
                    if suggestions:
                        break  # Если нашли результаты, прекращаем поиск
                
                # Небольшая задержка между запросами
                import time
                time.sleep(0.5)
            except Exception:
                continue
    except Exception:
        pass
    except Exception:
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen

        try:
            url = "https://nominatim.openstreetmap.org/search?" + urlencode(params)
            req = Request(url, headers=headers)
            with urlopen(req, timeout=5) as resp:
                raw = resp.read().decode("utf-8", "ignore")
                data = json.loads(raw)
                suggestions = [
                    {"value": item.get("display_name", "")}
                    for item in data[:5]
                    if item.get("display_name")
                ]
        except Exception:
            suggestions = []

    return JsonResponse({"suggestions": suggestions})


@require_GET
@login_required
def geocode_address(request):
    """
    Геокодирование адреса через Nominatim API (прокси на бэкенде для избежания CORS).
    Возвращает координаты и полное название адреса.
    """
    address = (request.GET.get("address") or "").strip()
    country = request.GET.get("country", "Россия").strip()
    
    if not address:
        return JsonResponse({"error": "Адрес не указан"}, status=400)
    
    # Функция для упрощения адреса
    def simplify_addr(addr):
        """Упрощает адрес, убирая лишние детали"""
        simplified = addr
        # Убираем детали: этаж, помещение, комната (более агрессивно)
        import re
        simplified = re.sub(r',\s*ЭТ\s*\d+', '', simplified, flags=re.IGNORECASE)
        simplified = re.sub(r',\s*ПОМ\s*[IVX\dА-Яа-я]+', '', simplified, flags=re.IGNORECASE)
        simplified = re.sub(r',\s*КОМ\s*\d+', '', simplified, flags=re.IGNORECASE)
        simplified = re.sub(r',\s*ПОМЕЩ\.?\s*[IVX\dА-Яа-я]+', '', simplified, flags=re.IGNORECASE)
        simplified = re.sub(r',\s*ПОМЕЩЕНИЕ\s*[IVX\dА-Яа-я]+', '', simplified, flags=re.IGNORECASE)
        simplified = re.sub(r',\s*ОФИС\s*\d+', '', simplified, flags=re.IGNORECASE)
        simplified = re.sub(r'ПОМ\s*II', '', simplified, flags=re.IGNORECASE)
        simplified = re.sub(r'КОМ\s*\d+', '', simplified, flags=re.IGNORECASE)
        simplified = re.sub(r'ПОМЕЩ\.?\s*[IVX\dА-Яа-я]+', '', simplified, flags=re.IGNORECASE)
        # Заменяем "К. 2А" на "корпус 2А" (с поддержкой букв)
        simplified = re.sub(r',\s*К\.\s*(\d+[А-Яа-я]?)', r', корпус \1', simplified, flags=re.IGNORECASE)
        simplified = re.sub(r',\s*К\s*(\d+[А-Яа-я]?)', r', корпус \1', simplified, flags=re.IGNORECASE)
        # Нормализуем пробелы
        simplified = re.sub(r'\s+', ' ', simplified)
        simplified = re.sub(r',\s*,', ',', simplified)
        simplified = re.sub(r'^,\s*', '', simplified)
        simplified = re.sub(r',\s*$', '', simplified)
        return simplified.strip()
    
    # Функция для извлечения основных частей адреса (улица + дом)
    def extract_main_address(addr):
        """Извлекает только улицу и дом из адреса"""
        import re
        variants = []
        simplified = simplify_addr(addr)
        
        # Пытаемся извлечь улицу и дом
        street_match = re.search(r'(?:ул\.|улица|пр\.|проспект|пер\.|переулок|пл\.|площадь|б-р|бульвар)\s+([^,]+)', simplified, re.IGNORECASE)
        house_match = re.search(r'д\.\s*([^,]+)', simplified, re.IGNORECASE)
        
        if street_match and house_match:
            street = street_match.group(1).strip()
            house = house_match.group(1).strip()
            
            # Убираем корпус из номера дома, если он там есть
            house = re.sub(r'\s*корпус\s*\d+[А-Яа-я]?', '', house, flags=re.IGNORECASE).strip()
            
            # Варианты с городом
            if re.search(r'Москва', simplified, re.IGNORECASE):
                variants.append(f"Москва, {street}, {house}")
                variants.append(f"{street}, {house}, Москва")
            elif re.search(r'Санкт-Петербург', simplified, re.IGNORECASE):
                variants.append(f"Санкт-Петербург, {street}, {house}")
                variants.append(f"{street}, {house}, Санкт-Петербург")
            
            # Варианты без города
            variants.append(f"{street}, {house}")
        
        return variants
    
    # Стратегии поиска - более агрессивные для российских адресов
    search_queries = []
    import re
    
    # Сначала пробуем самые простые варианты (только улица + дом)
    main_variants = extract_main_address(address)
    search_queries.extend(main_variants)
    
    # Упрощаем адрес
    simplified = simplify_addr(address)
    
    # Если адрес содержит индекс в начале, пробуем с ним и без
    index_match = re.match(r'^(\d{6}),?\s*(.+)', simplified)
    if index_match:
        index, rest = index_match.groups()
        search_queries.append(f"{rest}, {index}")
        search_queries.append(simplified)
    else:
        search_queries.append(simplified)
    
    # Если адрес содержит "Москва", пробуем разные варианты
    if "Москва" in simplified or "МОСКВА" in simplified:
        without_city = re.sub(r'г\.?\s*Москва,?\s*', '', simplified, flags=re.IGNORECASE)
        without_city = re.sub(r'г\.?\s*МОСКВА,?\s*', '', without_city, flags=re.IGNORECASE)
        if without_city != simplified:
            search_queries.extend([
                f"Москва, {without_city}",
                without_city
            ])
            
            # Пробуем без индекса
            without_index = re.sub(r'^\d{6},?\s*', '', without_city).strip()
            if without_index != without_city:
                search_queries.extend([
                    f"Москва, {without_index}",
                    without_index
                ])
    
    # Если адрес содержит "Санкт-Петербург", пробуем разные варианты
    if "Санкт-Петербург" in simplified or "САНКТ-ПЕТЕРБУРГ" in simplified:
        without_city = re.sub(r'г\.?\s*Санкт-Петербург,?\s*', '', simplified, flags=re.IGNORECASE)
        without_city = re.sub(r'г\.?\s*САНКТ-ПЕТЕРБУРГ,?\s*', '', without_city, flags=re.IGNORECASE)
        if without_city != simplified:
            search_queries.extend([
                f"Санкт-Петербург, {without_city}",
                without_city
            ])
            
            # Пробуем без индекса
            without_index = re.sub(r'^\d{6},?\s*', '', without_city).strip()
            if without_index != without_city:
                search_queries.extend([
                    f"Санкт-Петербург, {without_index}",
                    without_index
                ])
    
    # Если адрес начинается с "Пр." или "пр.", добавляем Санкт-Петербург
    if simplified.startswith(("Пр.", "пр.", "Пр ", "пр ")):
        search_queries.extend([
            f"{simplified}, Санкт-Петербург, {country}",
            f"{simplified}, Санкт-Петербург",
            f"{simplified}, СПб, {country}",
            f"{simplified}, СПб",
        ])
    
    # Общие стратегии
    search_queries.extend([
        f"{simplified}, {country}",
        simplified,
    ])
    
    # Убираем дубликаты, сохраняя порядок (простые варианты первыми)
    seen = set()
    unique_queries = []
    for q in search_queries:
        if q and q.strip():
            normalized = q.strip().lower()
            if normalized not in seen:
                seen.add(normalized)
                unique_queries.append(q.strip())
    
    search_queries = unique_queries
    
    headers = {
        "User-Agent": "ImperiaApp/1.0 (admin@example.com)",
        "Accept-Language": "ru-RU,ru;q=0.9"
    }
    
    try:
        import requests
        use_requests = True
    except ImportError:
        use_requests = False
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen
    
    for i, search_query in enumerate(search_queries):
        try:
            # Задержка между попытками (кроме первой)
            if i > 0:
                import time
                time.sleep(0.5)
            
            params = {
                "q": search_query,
                "format": "json",
                "limit": 5,  # Увеличиваем лимит для лучшего поиска
                "addressdetails": 1,
                "accept-language": "ru",
            }
            
            # Для большинства попыток ограничиваем по России
            if i < len(search_queries) - 2:
                params["countrycodes"] = "ru"
            
            if use_requests:
                r = requests.get(
                    "https://nominatim.openstreetmap.org/search",
                    params=params,
                    headers=headers,
                    timeout=5,
                )
                r.raise_for_status()
                data = r.json()
            else:
                url = "https://nominatim.openstreetmap.org/search?" + urlencode(params)
                req = Request(url, headers=headers)
                with urlopen(req, timeout=5) as resp:
                    raw = resp.read().decode("utf-8", "ignore")
                    data = json.loads(raw)
            
            if data and len(data) > 0:
                # Ищем результат в России (если возможно)
                result = data[0]
                if i < len(search_queries) - 1:
                    russian_result = next(
                        (r for r in data if r.get("address", {}).get("country_code") == "ru"),
                        None
                    )
                    if russian_result:
                        result = russian_result
                
                return JsonResponse({
                    "success": True,
                    "lat": float(result["lat"]),
                    "lon": float(result["lon"]),
                    "display_name": result.get("display_name", search_query)
                })
                
        except Exception as e:
            # Продолжаем со следующей стратегией
            continue
    
    return JsonResponse({
        "success": False,
        "error": "Адрес не найден"
    })