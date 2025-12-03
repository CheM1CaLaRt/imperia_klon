# core/views_requests.py
from decimal import Decimal, InvalidOperation
import re
from mimetypes import guess_type
from django.urls import reverse
from django.contrib import messages
from django.db.models import Q, ForeignKey, OneToOneField, ManyToManyField
from django.http import HttpResponseBadRequest, FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.apps import apps
from django.views.decorators.clickjacking import xframe_options_exempt
from django.core.exceptions import FieldDoesNotExist

from .permissions import require_groups
from .forms_requests import (
    RequestForm,
    RequestCreateForm,
    RequestItemForm,
    RequestItemEditForm,
    RequestQuoteForm,
    OrderItemFormSet,
)
from .models import Counterparty, CounterpartyAddress, CounterpartyContact
from .models_requests import (
    Request,
    RequestItem,
    RequestStatus,
    RequestHistory,
    RequestQuote,
)
from django.db.models import Prefetch
from .models_pick import PickItem

# ✅ безопасный импорт формсета сборки (операторская секция)
try:
    from .forms_pick import PickItemFormSet
except Exception:
    PickItemFormSet = None

# ✅ безопасный импорт моделей листа сборки (для просмотра складом)
try:
    from .models_pick import PickList
except Exception:
    PickList = None


def _can_manager_access_request(user, request_obj):
    """
    Проверяет, может ли менеджер получить доступ к заявке.
    Менеджер может видеть заявку если:
    1. Он является инициатором заявки
    2. Он прикреплен к контрагенту заявки (через managers ManyToMany)
    """
    if not user.groups.filter(name="manager").exists():
        return False
    
    # Если менеджер создал заявку - доступ есть
    if request_obj.initiator == user:
        return True
    
    # Если есть контрагент, проверяем привязку
    if request_obj.counterparty:
        # Проверяем ManyToMany поле managers
        if request_obj.counterparty.managers.filter(pk=user.pk).exists():
            return True
        
        # Проверяем ForeignKey поле manager (если есть)
        Counterparty = apps.get_model("core", "Counterparty")
        try:
            fld = Counterparty._meta.get_field("manager")
            if isinstance(fld, (ForeignKey, OneToOneField)):
                manager_field = getattr(request_obj.counterparty, "manager", None)
                if manager_field == user:
                    return True
        except FieldDoesNotExist:
            pass
    
    return False


# ---------- Список заявок ----------
@require_groups("manager", "operator", "warehouse", "director")
def request_list(request):
    status = request.GET.get("status")
    u = request.user

    Counterparty = apps.get_model("core", "Counterparty")

    has_cp_manager_fk = False
    has_cp_managers_m2m = False
    try:
        fld = Counterparty._meta.get_field("manager")
        has_cp_manager_fk = isinstance(fld, (ForeignKey, OneToOneField))
    except FieldDoesNotExist:
        pass
    try:
        fld = Counterparty._meta.get_field("managers")
        has_cp_managers_m2m = isinstance(fld, ManyToManyField)
    except FieldDoesNotExist:
        pass

    qs = Request.objects.select_related("initiator", "assignee", "counterparty")
    if has_cp_manager_fk:
        qs = qs.select_related("counterparty__manager")

    # --- Ролевые фильтры ---
    is_warehouse = u.groups.filter(name="warehouse").exists()
    is_manager = u.groups.filter(name="manager").exists()
    is_operator = u.groups.filter(name="operator").exists()
    is_director = u.groups.filter(name="director").exists()
    has_full_access = u.is_superuser or is_director or is_operator
    
    if is_warehouse and not has_full_access:
        # Склад видит только заявки на сбор
        qs = qs.filter(
            status__in=[
                RequestStatus.TO_PICK,
                RequestStatus.IN_PROGRESS,
                RequestStatus.READY_TO_SHIP,
            ]
        )
    elif is_manager and not has_full_access:
        # Менеджер видит:
        # 1. Заявки контрагентов, к которым он прикреплен
        # 2. Заявки, которые он сам создал
        cond = Q(initiator=u)
        
        # Проверяем привязку через ManyToMany поле managers
        if has_cp_managers_m2m:
            cond |= Q(counterparty__managers=u)
        # Проверяем привязку через ForeignKey поле manager (если есть)
        if has_cp_manager_fk:
            cond |= Q(counterparty__manager=u)
            
        qs = qs.filter(cond)
    # Оператор и директор видят все заявки (без фильтрации)

    if status:
        qs = qs.filter(status=status)

    return render(
        request,
        "requests/list.html",
        {
            "requests": qs.order_by("-created_at")[:500],
            "status": status,
            "statuses": RequestStatus,
        },
    )


# ---------- Утилиты ----------
def _parse_qty(v: str) -> Decimal:
    v = (v or "").strip().replace(",", ".")
    if not v:
        return Decimal("1")
    try:
        return Decimal(v)
    except InvalidOperation:
        return Decimal("1")


def _in_groups(user, names):
    return user.is_authenticated and user.groups.filter(name__in=names).exists()


# ---------- Создание заявки ----------
@require_groups("manager", "operator", "director")
def request_create(request):
    if request.method == "POST":
        form = RequestCreateForm(request.POST, user=request.user)
        order_formset = OrderItemFormSet(request.POST, prefix="order")
        
        if form.is_valid() and order_formset.is_valid():
            obj = form.save(commit=False)
            obj.initiator = request.user
            obj.status = (
                RequestStatus.SUBMITTED
                if "submit" in request.POST
                else RequestStatus.DRAFT
            )
            obj.save()

            # Сохраняем позиции заказа из формсета
            for item_form in order_formset:
                if item_form.cleaned_data and not item_form.cleaned_data.get("DELETE"):
                    product_id = item_form.cleaned_data.get("product_id")
                    name = item_form.cleaned_data.get("name", "").strip()
                    quantity = item_form.cleaned_data.get("quantity") or Decimal("1")
                    note = item_form.cleaned_data.get("note", "").strip()
                    
                    if name or product_id:
                        product = None
                        if product_id:
                            try:
                                from .models import Product
                                product = Product.objects.get(id=product_id)
                                if not name:
                                    name = product.name
                            except Exception:
                                pass
                        
                        RequestItem.objects.create(
                            request=obj,
                            product=product,
                            title=name,
                            quantity=quantity,
                            note=note
                        )

            messages.success(request, "Заявка создана")
            return redirect("core:request_detail", pk=obj.pk)
    else:
        form = RequestCreateForm(user=request.user)
        order_formset = OrderItemFormSet(prefix="order")
    
    return render(request, "requests/form.html", {
        "form": form,
        "order_formset": order_formset,
    })


# ---------- Детали заявки ----------
@require_groups("manager", "operator", "warehouse", "director")
def request_detail(request, pk: int):
    # Заявка + связанные данные, pick_items сразу в нужном порядке
    obj = get_object_or_404(
        Request.objects
        .select_related("initiator", "assignee", "counterparty")
        .prefetch_related(
            "items", "history", "comments",
            Prefetch("pick_items", queryset=PickItem.objects.order_by("id")),
        ),
        pk=pk,
    )
    
    # Проверка доступа для менеджера
    u = request.user
    is_manager = u.groups.filter(name="manager").exists()
    has_full_access = u.is_superuser or u.groups.filter(name__in=["director", "operator"]).exists()
    
    if is_manager and not has_full_access:
        # Менеджер может видеть только свои заявки или заявки контрагентов, к которым прикреплен
        if not _can_manager_access_request(u, obj):
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden("У вас нет доступа к этой заявке")
    
    # Проверка доступа для склада
    is_warehouse = u.groups.filter(name="warehouse").exists()
    if is_warehouse and not has_full_access:
        # Склад может видеть только заявки на сбор
        if obj.status not in [RequestStatus.TO_PICK, RequestStatus.IN_PROGRESS, RequestStatus.READY_TO_SHIP]:
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden("У вас нет доступа к этой заявке")

    # Форма добавления позиции
    item_form = RequestItemForm()

    # Inline-редактирование позиции
    edit_item = None
    edit_form = None
    edit_id = request.GET.get("edit")
    if edit_id:
        try:
            edit_item = obj.items.get(pk=int(edit_id))
            edit_form = RequestItemEditForm(instance=edit_item)
        except (ValueError, RequestItem.DoesNotExist):
            pass

    # Форма КП
    quote_form = RequestQuoteForm()

    # --- СЕКЦИЯ СБОРКИ (оператор/директор, только в статусе approved) ---
    try:
        from .forms_pick import PickItemFormSet
    except Exception:
        PickItemFormSet = None

    is_approved = obj.status == RequestStatus.APPROVED
    can_pick = _in_groups(request.user, ["operator", "director"])
    show_pick_section = bool(PickItemFormSet and can_pick and is_approved)

    pick_formset = None
    if show_pick_section:
        # Подставляем сохранённые строки в формсет
        initial = [
            {
                "barcode": it.barcode,
                "name": it.name,
                "location": it.location,
                "unit": it.unit,
                "qty": it.qty,
                "price": it.price,
            }
            for it in obj.pick_items.all()
        ] or [{}]
        pick_formset = PickItemFormSet(prefix="pick", initial=initial)

    # Список строк сборки для превью/склада.
    # ДОБАВЛЕНО: picked_qty, missing, note — чтобы модалка подставляла сохранённые значения.
    pick_items = list(
        obj.pick_items.values(
            "barcode", "name", "location", "unit", "qty", "price",
            "picked_qty", "missing", "note"
        )
    )

    # Истина, если строки сборки есть (шаблону важен сам факт)
    latest_pick = obj.pick_items.first()

    # URL приёма сохранения скан-сборки (без двоеточий в имени)
    pick_confirm_url = reverse("core:pick_confirm", args=[obj.pk])

    return render(
        request,
        "requests/detail.html",
        {
            "obj": obj,
            "item_form": item_form,
            "edit_item": edit_item,
            "edit_form": edit_form,
            "quote_form": quote_form,
            "statuses": RequestStatus,

            "show_pick_section": show_pick_section,
            "pick_formset": pick_formset,

            "pick_items": pick_items,
            "latest_pick": latest_pick,
            "pick_confirm_url": pick_confirm_url,
        },
    )


# ---------- Добавить позицию ----------
@require_POST
@require_groups("manager", "operator", "director")
def request_add_item(request, pk: int):
    obj = get_object_or_404(Request, pk=pk)
    if not obj.is_editable:
        return HttpResponseBadRequest("Нельзя изменять в этом статусе")
    form = RequestItemForm(request.POST)
    if form.is_valid():
        it = form.save(commit=False)
        it.request = obj
        it.save()
        messages.success(request, "Позиция добавлена")
    else:
        messages.error(request, "Проверьте корректность позиции")
    return redirect("core:request_detail", pk=pk)


# ---------- Обновить позицию ----------
@require_POST
@require_groups("manager", "operator", "director")
def request_update_item(request, pk: int, item_id: int):
    obj = get_object_or_404(Request, pk=pk)
    if not obj.is_editable:
        return HttpResponseBadRequest("Нельзя изменять в этом статусе")

    it = get_object_or_404(RequestItem, pk=item_id, request=obj)
    form = RequestItemEditForm(request.POST, instance=it)
    if form.is_valid():
        form.save()
        messages.success(request, "Позиция обновлена")
    else:
        messages.error(request, "Проверьте корректность позиции")

    return redirect("core:request_detail", pk=pk)


# ---------- Удалить позицию ----------
@require_POST
@require_groups("manager", "operator", "director")
def request_delete_item(request, pk: int, item_id: int):
    obj = get_object_or_404(Request, pk=pk)
    if not obj.is_editable:
        return HttpResponseBadRequest("Нельзя изменять в этом статусе")

    it = get_object_or_404(RequestItem, pk=item_id, request=obj)
    it.delete()
    messages.success(request, "Позиция удалена")
    return redirect("core:request_detail", pk=pk)


# ---------- Смена статуса ----------
@require_POST
@require_groups("manager", "operator", "warehouse", "director")
def request_change_status(request, pk: int):
    obj = get_object_or_404(Request, pk=pk)
    to = request.POST.get("to")
    u = request.user

    if to not in dict(RequestStatus.choices):
        return HttpResponseBadRequest("Неизвестный статус")

    allowed = {
        "manager": {
            RequestStatus.SUBMITTED,  # Может отправить заявку
            RequestStatus.CANCELED,   # Может отменить
            RequestStatus.APPROVED,   # Может согласовать
            RequestStatus.REJECTED,   # Может отклонить
        },
        "operator": {
            RequestStatus.SUBMITTED,      # Может отправить заявку
            RequestStatus.QUOTE,          # Может создать КП
            RequestStatus.PENDING_APPROVAL,  # Может отправить на согласование
            RequestStatus.TO_PICK,        # Может передать на сборку
            RequestStatus.READY_TO_SHIP,  # Может изменить статус (если нужно)
            RequestStatus.PARTIALLY_SHIPPED,  # Может частично отгрузить
            RequestStatus.SHIPPED,        # Может полностью отгрузить
            RequestStatus.DELIVERED,      # Может отметить как доставленную
            RequestStatus.CANCELED,       # Может отменить
        },
        "warehouse": {
            RequestStatus.IN_PROGRESS,    # Может начать сборку
            RequestStatus.READY_TO_SHIP,  # Может завершить сборку
        },
        "director": set(s for s, _ in RequestStatus.choices),  # Полный доступ
    }

    user_groups = {g.name for g in u.groups.all()}
    can = u.is_superuser or any(to in allowed.get(g, set()) for g in user_groups)
    if not can:
        return HttpResponseBadRequest("Недостаточно прав для смены статуса")
    
    # Дополнительная проверка доступа для менеджера
    is_manager = "manager" in user_groups
    has_full_access = u.is_superuser or "director" in user_groups or "operator" in user_groups
    if is_manager and not has_full_access:
        if not _can_manager_access_request(u, obj):
            return HttpResponseBadRequest("У вас нет доступа к этой заявке")

    # ✅ склад двигает только после передачи в сборку
    if (
        "warehouse" in user_groups
        and to in {RequestStatus.IN_PROGRESS, RequestStatus.READY_TO_SHIP, RequestStatus.DELIVERED}
        and obj.status not in {RequestStatus.TO_PICK, RequestStatus.IN_PROGRESS, RequestStatus.READY_TO_SHIP}
        and not (u.is_superuser or "director" in user_groups)
    ):
        return HttpResponseBadRequest("Заявка ещё не передана на склад")

    # ✅ оператор/директор не могут отправить "в работу" без листа сборки
    if to == RequestStatus.TO_PICK and PickList is not None:
        has_items = PickList.objects.filter(request=obj).filter(items__isnull=False).exists()
        if not has_items:
            return HttpResponseBadRequest(
                "Сначала заполните «Сборка со склада», затем отправляйте в работу."
            )

    from_status = obj.status
    obj.status = to

    # авто-завершение
    if to == RequestStatus.DELIVERED and getattr(obj, "is_paid", False):
        obj.status = RequestStatus.DONE

    obj.save(update_fields=["status", "updated_at"])
    RequestHistory.objects.create(
        request=obj, author=u, from_status=from_status, to_status=obj.status
    )
    messages.success(request, "Статус обновлён")
    return redirect("core:request_detail", pk=pk)


# ---------- Загрузить КП ----------
@require_POST
@require_groups("operator", "director")
def request_upload_quote(request, pk: int):
    obj = get_object_or_404(Request, pk=pk)
    form = RequestQuoteForm(request.POST, request.FILES)
    if form.is_valid():
        q = form.save(commit=False)
        q.request = obj
        q.uploaded_by = request.user
        q.original_name = request.FILES["file"].name
        q.save()
        messages.success(request, "Коммерческое предложение прикреплено")
    else:
        messages.error(request, "Не удалось загрузить файл КП")
    return redirect("core:request_detail", pk=pk)


# ---------- Удалить КП ----------
@require_POST
@require_groups("operator", "director")
def request_delete_quote(request, pk: int, quote_id: int):
    obj = get_object_or_404(Request, pk=pk)
    q = get_object_or_404(RequestQuote, pk=quote_id, request=obj)
    if (q.uploaded_by_id != request.user.id) and (
        not request.user.groups.filter(name="director").exists()
    ) and (not request.user.is_superuser):
        return HttpResponseBadRequest("Можно удалять только свои файлы")
    q.delete()
    messages.success(request, "Файл КП удалён")
    return redirect("core:request_detail", pk=pk)


# ---------- Просмотр КП ----------
@xframe_options_exempt
def request_quote_preview(request, pk: int, quote_id: int):
    q = get_object_or_404(RequestQuote, pk=quote_id, request_id=pk)
    if not q.file:
        raise Http404("Файл не найден")
    ctype = guess_type(q.original_name or q.file.name)[0] or "application/pdf"
    resp = FileResponse(q.file.open("rb"), content_type=ctype)
    resp["Content-Disposition"] = (
        f'inline; filename="{q.original_name or q.file.name}"'
    )
    return resp


# ---------- Смена оплаты ----------
@require_POST
@require_groups("operator", "director")
def request_toggle_payment(request, pk: int):
    obj = get_object_or_404(Request, pk=pk)
    obj.is_paid = "is_paid" in request.POST
    if obj.is_paid and obj.status == RequestStatus.DELIVERED:
        obj.status = RequestStatus.DONE
    obj.save(update_fields=["is_paid", "status", "updated_at"])
    messages.success(request, "Статус оплаты обновлён")
    return redirect("core:request_detail", pk=pk)


# ---------- Удалить файл КП (вариант 2) ----------
@require_POST
@require_groups("operator", "director")
def request_quote_delete(request, pk: int, qpk: int):
    obj = get_object_or_404(Request, pk=pk)
    quote = get_object_or_404(RequestQuote, pk=qpk, request=obj)
    if hasattr(obj, "is_editable") and not obj.is_editable:
        messages.error(request, "Заявка недоступна для редактирования.")
        return redirect("core:request_detail", pk=obj.pk)
    try:
        if quote.file:
            quote.file.delete(save=False)
        quote.delete()
        messages.success(request, "Файл удалён.")
    except Exception as e:
        messages.error(request, f"Не удалось удалить файл: {e}")
    return redirect("core:request_detail", pk=obj.pk)


# ---------- API: Загрузка адресов и контактов контрагента ----------
@login_required
@require_GET
@require_groups("manager", "operator", "director")
def counterparty_addresses_contacts(request):
    """
    API endpoint для загрузки адресов и контактов контрагента.
    GET ?counterparty_id=123
    """
    try:
        counterparty_id = int(request.GET.get("counterparty_id", 0))
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "invalid_id"}, status=400)

    try:
        counterparty = Counterparty.objects.get(id=counterparty_id)
    except Counterparty.DoesNotExist:
        return JsonResponse({"ok": False, "error": "not_found"}, status=404)

    # Проверка прав: менеджер видит только своих клиентов
    is_manager = request.user.groups.filter(name="manager").exists()
    is_operator_or_director = request.user.groups.filter(name__in=["operator", "director"]).exists()
    
    if is_manager and not request.user.is_superuser and not is_operator_or_director:
        if hasattr(counterparty, "managers"):
            if request.user not in counterparty.managers.all():
                return JsonResponse({"ok": False, "error": "forbidden"}, status=403)

    addresses = CounterpartyAddress.objects.filter(counterparty=counterparty).order_by("-is_default", "created_at")
    contacts = CounterpartyContact.objects.filter(counterparty=counterparty).order_by("full_name")

    return JsonResponse({
        "ok": True,
        "addresses": [
            {
                "id": addr.id,
                "address": addr.address,
                "is_default": addr.is_default,
            }
            for addr in addresses
        ],
        "contacts": [
            {
                "id": contact.id,
                "full_name": contact.full_name,
                "position": contact.position or "",
                "phone": contact.phone or "",
                "email": contact.email or "",
            }
            for contact in contacts
        ],
    })


# ---------- API: Добавление адреса контрагента ----------
@login_required
@require_POST
@require_groups("manager", "operator", "director")
def counterparty_add_address(request):
    """
    API endpoint для добавления адреса доставки контрагента.
    POST: counterparty_id, address, is_default
    """
    try:
        counterparty_id = int(request.POST.get("counterparty_id", 0))
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "invalid_id"}, status=400)

    try:
        counterparty = Counterparty.objects.get(id=counterparty_id)
    except Counterparty.DoesNotExist:
        return JsonResponse({"ok": False, "error": "not_found"}, status=404)

    # Проверка прав: оператор/директор или прикрепленный менеджер
    is_manager = request.user.groups.filter(name="manager").exists()
    is_operator_or_director = request.user.groups.filter(name__in=["operator", "director"]).exists()
    
    if is_manager and not request.user.is_superuser and not is_operator_or_director:
        if hasattr(counterparty, "managers"):
            if request.user not in counterparty.managers.all():
                return JsonResponse({"ok": False, "error": "forbidden"}, status=403)

    address = request.POST.get("address", "").strip()
    if not address:
        return JsonResponse({"ok": False, "error": "address_required"}, status=400)

    is_default = request.POST.get("is_default") == "true"
    
    # Если устанавливаем как адрес по умолчанию, снимаем флаг с остальных
    if is_default:
        CounterpartyAddress.objects.filter(counterparty=counterparty).update(is_default=False)

    addr = CounterpartyAddress.objects.create(
        counterparty=counterparty,
        address=address,
        is_default=is_default
    )

    return JsonResponse({
        "ok": True,
        "address": {
            "id": addr.id,
            "address": addr.address,
            "is_default": addr.is_default,
        }
    })


# ---------- API: Добавление контакта контрагента ----------
@login_required
@require_POST
@require_groups("manager", "operator", "director")
def counterparty_add_contact(request):
    """
    API endpoint для добавления контактного лица контрагента.
    POST: counterparty_id, full_name, position, email, phone, mobile, note, birthday
    """
    try:
        counterparty_id = int(request.POST.get("counterparty_id", 0))
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "invalid_id"}, status=400)

    try:
        counterparty = Counterparty.objects.get(id=counterparty_id)
    except Counterparty.DoesNotExist:
        return JsonResponse({"ok": False, "error": "not_found"}, status=404)

    # Проверка прав: оператор/директор или прикрепленный менеджер
    is_manager = request.user.groups.filter(name="manager").exists()
    is_operator_or_director = request.user.groups.filter(name__in=["operator", "director"]).exists()
    
    if is_manager and not request.user.is_superuser and not is_operator_or_director:
        if hasattr(counterparty, "managers"):
            if request.user not in counterparty.managers.all():
                return JsonResponse({"ok": False, "error": "forbidden"}, status=403)

    full_name = request.POST.get("full_name", "").strip()
    if not full_name:
        return JsonResponse({"ok": False, "error": "full_name_required"}, status=400)

    from datetime import datetime
    birthday_str = request.POST.get("birthday", "").strip()
    birthday = None
    if birthday_str:
        try:
            birthday = datetime.strptime(birthday_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            pass

    contact = CounterpartyContact.objects.create(
        counterparty=counterparty,
        full_name=full_name,
        position=request.POST.get("position", "").strip(),
        email=request.POST.get("email", "").strip(),
        phone=request.POST.get("phone", "").strip(),
        mobile=request.POST.get("mobile", "").strip(),
        note=request.POST.get("note", "").strip(),
        birthday=birthday
    )

    return JsonResponse({
        "ok": True,
        "contact": {
            "id": contact.id,
            "full_name": contact.full_name,
            "position": contact.position or "",
            "phone": contact.phone or "",
            "email": contact.email or "",
        }
    })
