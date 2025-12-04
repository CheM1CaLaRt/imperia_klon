# core/views_requests.py
from decimal import Decimal, InvalidOperation
import re
from mimetypes import guess_type
from django.urls import reverse
from django.contrib import messages
from django.db.models import Q, ForeignKey, OneToOneField, ManyToManyField
from django.http import HttpResponseBadRequest, FileResponse, Http404, JsonResponse, HttpResponse
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
    RequestQuoteItemFormSet,
    OrderItemFormSet,
    RequestShipmentItemFormSet,
)
from .models import Counterparty, CounterpartyAddress, CounterpartyContact
from .models_requests import (
    Request,
    RequestItem,
    RequestStatus,
    RequestHistory,
    RequestQuote,
    RequestQuoteItem,
    RequestShipment,
    RequestShipmentItem,
)
from django.db.models import Prefetch
from .models_pick import PickItem
from .models import Product, Inventory
from django.db import transaction

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


@transaction.atomic
def _create_pick_items_from_quote(request_obj, actor):
    """
    Автоматически создает PickItem из товаров активного КП.
    Вызывается при переходе заявки в статус TO_PICK.
    """
    active_quote = request_obj.active_quote
    if not active_quote:
        return []
    
    # Удаляем старые PickItem для этой заявки
    PickItem.objects.filter(request=request_obj).delete()
    
    created_items = []
    quote_items = active_quote.items.select_related("product", "request_item").all()
    
    for quote_item in quote_items:
        product = quote_item.product
        
        # Получаем информацию о местоположении товара на складе
        location = ""
        unit = "шт"
        
        if product:
            # Ищем товар на складе для определения местоположения
            inv = Inventory.objects.filter(
                product=product, quantity__gt=0
            ).select_related("bin", "warehouse").order_by("-quantity").first()
            
            if inv and inv.bin:
                location = inv.bin.code
            elif inv and inv.warehouse:
                location = inv.warehouse.code
        
        # Преобразуем количество в целое число для qty (PositiveIntegerField)
        try:
            qty = int(float(quote_item.quantity)) if quote_item.quantity else 1
            if qty <= 0:
                qty = 1
        except (ValueError, TypeError):
            qty = 1
        
        pick_item = PickItem.objects.create(
            request=request_obj,
            barcode=product.barcode if product else "",
            name=quote_item.title,
            location=location,
            unit=unit,
            qty=qty,
            price=quote_item.price or Decimal("0"),
            note=quote_item.note or "",
        )
        created_items.append(pick_item)
    
    return created_items


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
            "items", "history", "comments", "quotes",
            Prefetch("pick_items", queryset=PickItem.objects.order_by("id")),
            Prefetch("quotes__items", queryset=RequestQuoteItem.objects.select_related("product", "request_item").all()),
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
    
    # Активное коммерческое предложение с товарами
    active_quote = obj.quotes.filter(is_active=True).first()
    quote_items = []
    quote_total = Decimal("0")
    if active_quote:
        try:
            quote_items = list(active_quote.items.select_related("product", "request_item").all())
            # Безопасно суммируем total, обрабатывая возможные None значения
            quote_total = sum(
                (item.total if item.total is not None else Decimal("0")) 
                for item in quote_items
            )
        except Exception as e:
            # Если есть ошибка при загрузке позиций КП, логируем и продолжаем
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Ошибка при загрузке позиций КП для заявки {obj.pk}: {e}")
            quote_items = []
            quote_total = Decimal("0")
    
    # Проверяем, может ли оператор/директор создать/редактировать КП
    can_edit_quote = has_full_access and obj.status in [
        RequestStatus.SUBMITTED, RequestStatus.REJECTED, RequestStatus.QUOTE, RequestStatus.PENDING_APPROVAL
    ]
    
    # Отгрузки заявки
    shipments = obj.shipments.select_related("shipped_by").prefetch_related("items__quote_item").all()
    can_create_shipment = has_full_access and obj.status in [
        RequestStatus.READY_TO_SHIP, RequestStatus.PARTIALLY_SHIPPED
    ]
    
    # Проверка возможности добавления товаров
    is_manager_only = is_manager and not has_full_access
    can_add_items = (is_manager_only and obj.is_editable) or (has_full_access and obj.can_add_items)
    
    # Получаем даты прохождения этапов из истории
    status_dates = {}
    for h in obj.history.all().order_by('created_at'):
        if h.to_status:
            if h.to_status not in status_dates:
                status_dates[h.to_status] = {
                    'date': h.created_at,
                    'author': h.author,
                }
    
    # Добавляем дату создания для статуса draft
    if obj.created_at:
        status_dates['draft'] = {
            'date': obj.created_at,
            'author': obj.initiator,
        }
    
    # Определяем порядок этапов и маппинг статусов
    status_steps = [
        ('draft', 'Черновик'),
        ('submitted', 'Отправлена'),
        ('quote', 'КП'),
        ('pending_approval', 'На согласовании'),
        ('approved', 'Согласована'),
        ('rejected', 'Не согласована'),
        ('to_pick', 'На сборку'),
        ('in_progress', 'Собирается'),
        ('ready_to_ship', 'Готова к отгрузке'),
        ('partially_shipped', 'Частично отгружена'),
        ('shipped', 'Отгружена'),
        ('delivered', 'Доставлена'),
        ('done', 'Завершена'),
        ('canceled', 'Отменена'),
    ]
    
    # Находим текущий этап и определяем предыдущий/следующий
    current_status = obj.status
    current_index = None
    for i, (status, label) in enumerate(status_steps):
        if status == current_status:
            current_index = i
            break
    
    # Определяем этапы для отображения (предыдущий, текущий, следующий)
    display_steps = []
    if current_index is not None:
        # Предыдущий этап
        if current_index > 0:
            prev_status, prev_label = status_steps[current_index - 1]
            prev_data = status_dates.get(prev_status, {})
            display_steps.append({
                'status': prev_status,
                'label': prev_label,
                'type': 'prev',
                'date': prev_data.get('date'),
                'author': prev_data.get('author'),
                'is_completed': True,
            })
        
        # Текущий этап
        current_data = status_dates.get(current_status, {})
        display_steps.append({
            'status': current_status,
            'label': status_steps[current_index][1],
            'type': 'current',
            'date': current_data.get('date') or obj.created_at,
            'author': current_data.get('author') or obj.initiator,
            'is_completed': False,
        })
        
        # Следующий этап
        if current_index < len(status_steps) - 1:
            next_status, next_label = status_steps[current_index + 1]
            next_data = status_dates.get(next_status, {})
            display_steps.append({
                'status': next_status,
                'label': next_label,
                'type': 'next',
                'date': next_data.get('date'),
                'author': next_data.get('author'),
                'is_completed': False,
            })
    else:
        # Если статус не найден в списке, показываем только текущий
        display_steps.append({
            'status': current_status,
            'label': obj.get_status_display() or current_status,
            'type': 'current',
            'date': obj.created_at,
            'author': obj.initiator,
            'is_completed': False,
        })

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
            
            "active_quote": active_quote,
            "quote_items": quote_items,
            "quote_total": quote_total,
            "can_edit_quote": can_edit_quote,
            "shipments": shipments,
            "can_create_shipment": can_create_shipment,
            "can_add_items": can_add_items,
            "status_dates": status_dates,
            "display_steps": display_steps,
        },
    )


# ---------- Добавить позицию ----------
@require_POST
@require_groups("manager", "operator", "director")
def request_add_item(request, pk: int):
    obj = get_object_or_404(Request, pk=pk)
    u = request.user
    
    # Менеджер может добавлять только в редактируемых статусах
    is_manager = u.groups.filter(name="manager").exists()
    has_full_access = u.is_superuser or u.groups.filter(name__in=["operator", "director"]).exists()
    
    if is_manager and not has_full_access:
        if not obj.is_editable:
            return HttpResponseBadRequest("Нельзя изменять в этом статусе")
    else:
        # Оператор и директор могут добавлять товары в любой момент
        if not obj.can_add_items:
            return HttpResponseBadRequest("Нельзя добавлять товары в завершенной или отмененной заявке")
    
    form = RequestItemForm(request.POST)
    if form.is_valid():
        it = form.save(commit=False)
        it.request = obj
        it.save()
        
        # Если есть активное КП, автоматически добавляем товар в КП
        active_quote = obj.active_quote
        if active_quote and has_full_access:
            # Создаем позицию в КП с нулевой ценой (оператор сможет установить цену позже)
            RequestQuoteItem.objects.get_or_create(
                quote=active_quote,
                request_item=it,
                defaults={
                    "product": it.product,
                    "title": it.title,
                    "quantity": it.quantity,
                    "price": Decimal("0.00"),
                    "total": Decimal("0.00"),
                    "note": it.note,
                }
            )
        
        messages.success(request, "Позиция добавлена")
        if active_quote and has_full_access:
            messages.info(request, "Товар добавлен в активное КП. Не забудьте установить цену при редактировании КП.")
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
        # Менеджеры больше не могут изменять статусы через UI
        # (блок действий скрыт для них в шаблоне)
        "operator": set(s for s, _ in RequestStatus.choices),  # Полный доступ (как у директора)
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
    
    # Менеджеры больше не могут изменять статусы
    is_manager = "manager" in user_groups
    has_full_access = u.is_superuser or "director" in user_groups or "operator" in user_groups
    if is_manager and not has_full_access:
        return HttpResponseBadRequest("Менеджеры не могут изменять статусы заявок")

    # ✅ склад двигает только после передачи в сборку
    if (
        "warehouse" in user_groups
        and to in {RequestStatus.IN_PROGRESS, RequestStatus.READY_TO_SHIP, RequestStatus.DELIVERED}
        and obj.status not in {RequestStatus.TO_PICK, RequestStatus.IN_PROGRESS, RequestStatus.READY_TO_SHIP}
        and not (u.is_superuser or "director" in user_groups)
    ):
        return HttpResponseBadRequest("Заявка ещё не передана на склад")

    from_status = obj.status
    
    # ✅ Автоматическое создание PickItem при переходе из APPROVED в TO_PICK
    if to == RequestStatus.TO_PICK and from_status == RequestStatus.APPROVED:
        # Проверяем, что есть активное КП
        active_quote = obj.active_quote
        if not active_quote:
            return HttpResponseBadRequest("Нельзя передать на сборку без коммерческого предложения")
        
        # Проверяем, что в КП есть товары
        if not active_quote.items.exists():
            return HttpResponseBadRequest("Нельзя передать на сборку без товаров в КП")
        
        # Автоматически создаем PickItem из товаров КП
        try:
            created_items = _create_pick_items_from_quote(obj, u)
            if not created_items:
                return HttpResponseBadRequest("Не удалось создать позиции для сборки")
        except Exception as e:
            return HttpResponseBadRequest(f"Ошибка при создании позиций для сборки: {str(e)}")

    # авто-завершение: если доставлена и оплачена - сразу завершаем
    final_status = to
    if to == RequestStatus.DELIVERED and getattr(obj, "is_paid", False):
        final_status = RequestStatus.DONE

    obj.status = final_status
    obj.save(update_fields=["status", "updated_at"])
    
    # Создаем запись в истории
    history_note = ""
    if to == RequestStatus.TO_PICK and from_status == RequestStatus.APPROVED:
        active_quote = obj.active_quote
        item_count = active_quote.items.count() if active_quote else 0
        history_note = f"Созданы позиции для сборки из КП ({item_count} позиций)"
    elif to == RequestStatus.DELIVERED and getattr(obj, "is_paid", False):
        history_note = "Заявка доставлена и оплачена - автоматически завершена"
    
    # Если был переход через DELIVERED в DONE, создаем две записи в истории
    if to == RequestStatus.DELIVERED and final_status == RequestStatus.DONE:
        # Сначала запись о доставке
        RequestHistory.objects.create(
            request=obj, author=u, from_status=from_status, to_status=RequestStatus.DELIVERED, note="Заявка доставлена"
        )
        # Затем запись о завершении
        RequestHistory.objects.create(
            request=obj, author=u, from_status=RequestStatus.DELIVERED, to_status=RequestStatus.DONE,
            note="Заявка оплачена - автоматически завершена"
        )
    else:
        RequestHistory.objects.create(
            request=obj, author=u, from_status=from_status, to_status=final_status, note=history_note
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
    was_paid = obj.is_paid
    obj.is_paid = "is_paid" in request.POST
    
    old_status = obj.status
    
    # Если заявка доставлена и оплачена - автоматически завершаем
    if obj.is_paid and obj.status == RequestStatus.DELIVERED:
        obj.status = RequestStatus.DONE
        obj.save(update_fields=["is_paid", "status", "updated_at"])
        RequestHistory.objects.create(
            request=obj,
            author=request.user,
            from_status=old_status,
            to_status=RequestStatus.DONE,
            note="Заявка оплачена - автоматически завершена",
        )
        messages.success(request, "Заявка оплачена и автоматически завершена")
    # Если снимаем оплату с завершенной заявки - возвращаем в доставленную
    elif not obj.is_paid and obj.status == RequestStatus.DONE:
        obj.status = RequestStatus.DELIVERED
        obj.save(update_fields=["is_paid", "status", "updated_at"])
        RequestHistory.objects.create(
            request=obj,
            author=request.user,
            from_status=old_status,
            to_status=RequestStatus.DELIVERED,
            note="Снята отметка об оплате - заявка возвращена в статус «Доставлена»",
        )
        messages.info(request, "Снята отметка об оплате. Заявка возвращена в статус «Доставлена»")
    else:
        obj.save(update_fields=["is_paid", "updated_at"])
        if obj.is_paid:
            messages.success(request, "Заявка отмечена как оплаченная")
        else:
            messages.info(request, "Снята отметка об оплате")
    
    return redirect("core:request_detail", pk=pk)


# ---------- Создать/редактировать КП с товарами ----------
@require_groups("operator", "director")
def request_quote_create_edit(request, pk: int, quote_id: int = None):
    """Создание или редактирование коммерческого предложения с товарами и ценами"""
    obj = get_object_or_404(Request.objects.select_related("counterparty"), pk=pk)
    
    # Проверяем, что заявка в нужном статусе для создания КП
    if obj.status not in [RequestStatus.SUBMITTED, RequestStatus.REJECTED, RequestStatus.QUOTE, RequestStatus.PENDING_APPROVAL]:
        messages.error(request, "Нельзя создать КП для заявки в этом статусе")
        return redirect("core:request_detail", pk=pk)
    
    quote = None
    if quote_id:
        quote = get_object_or_404(RequestQuote, pk=quote_id, request=obj)
    
    # Получаем товары из заявки, которые еще не добавлены в КП
    if quote:
        existing_item_ids = set(quote.items.values_list("request_item_id", flat=True))
        request_items = obj.items.exclude(id__in=existing_item_ids)
    else:
        request_items = obj.items.all()
    
    if request.method == "POST":
        # Создаем или обновляем КП
        if not quote:
            # Делаем все старые КП неактивными
            RequestQuote.objects.filter(request=obj, is_active=True).update(is_active=False)
            quote = RequestQuote.objects.create(
                request=obj,
                uploaded_by=request.user,
                is_active=True,
                original_name="КП с ценами"  # Дефолтное имя для КП, созданного через форму
            )
        
        # Обрабатываем формсет с товарами
        formset = RequestQuoteItemFormSet(request.POST, prefix="quote_items")
        
        if formset.is_valid():
            # Удаляем старые позиции, если редактируем
            if quote_id:
                quote.items.all().delete()
            
            # Сохраняем новые позиции
            for form in formset:
                if form.cleaned_data and not form.cleaned_data.get("DELETE"):
                    request_item_id = form.cleaned_data.get("request_item_id")
                    if request_item_id:
                        try:
                            request_item = RequestItem.objects.get(id=request_item_id, request=obj)
                            # Получаем и валидируем данные
                            title = form.cleaned_data.get("title") or request_item.title or ""
                            quantity = form.cleaned_data.get("quantity")
                            if quantity is None:
                                quantity = request_item.quantity or Decimal("1")
                            price = form.cleaned_data.get("price")
                            if price is None:
                                price = Decimal("0")
                            
                            # Создаем позицию КП (total будет автоматически рассчитан в save())
                            RequestQuoteItem.objects.create(
                                quote=quote,
                                request_item=request_item,
                                product=request_item.product,
                                title=title,
                                quantity=quantity,
                                price=price,
                                note=form.cleaned_data.get("note", ""),
                            )
                        except (RequestItem.DoesNotExist, ValueError, InvalidOperation) as e:
                            # Логируем ошибку, но продолжаем обработку остальных позиций
                            import logging
                            logger = logging.getLogger(__name__)
                            logger.error(f"Ошибка при создании позиции КП: {e}")
                            pass
            
            # Обновляем статус заявки
            action = request.POST.get("action")
            if action == "submit_for_approval":
                obj.status = RequestStatus.PENDING_APPROVAL
                obj.save(update_fields=["status", "updated_at"])
                RequestHistory.objects.create(
                    request=obj,
                    author=request.user,
                    from_status=RequestStatus.QUOTE,
                    to_status=RequestStatus.PENDING_APPROVAL,
                    note="КП отправлено на согласование"
                )
                messages.success(request, "Коммерческое предложение отправлено на согласование")
            else:
                obj.status = RequestStatus.QUOTE
                obj.save(update_fields=["status", "updated_at"])
                messages.success(request, "Коммерческое предложение сохранено")
            
            return redirect("core:request_detail", pk=pk)
        else:
            messages.error(request, "Проверьте корректность заполнения цен")
    else:
        # GET запрос - показываем форму
        if quote:
            # Загружаем существующие позиции
            initial_data = []
            for item in quote.items.select_related("request_item", "product").all():
                initial_data.append({
                    "request_item_id": item.request_item_id,
                    "product_id": item.product_id,
                    "title": item.title,
                    "quantity": item.quantity,
                    "price": item.price,
                    "note": item.note,
                })
            formset = RequestQuoteItemFormSet(prefix="quote_items", initial=initial_data)
        else:
            # Создаем пустой формсет
            initial_data = []
            for item in request_items.select_related("product").all():
                initial_data.append({
                    "request_item_id": item.id,
                    "product_id": item.product_id if item.product else None,
                    "title": item.title,
                    "quantity": item.quantity,
                    "price": Decimal("0"),
                    "note": item.note,
                })
            formset = RequestQuoteItemFormSet(prefix="quote_items", initial=initial_data)
    
    return render(request, "requests/quote_form.html", {
        "request": obj,
        "quote": quote,
        "formset": formset,
        "request_items": request_items,
    })


# ---------- Создать/редактировать отгрузку ----------
@require_groups("operator", "director")
def request_shipment_create(request, pk: int):
    """Создание отгрузки для заявки"""
    obj = get_object_or_404(
        Request.objects.select_related("counterparty"),
        pk=pk
    )
    
    # Проверяем статус заявки
    if obj.status not in [RequestStatus.READY_TO_SHIP, RequestStatus.PARTIALLY_SHIPPED]:
        messages.error(request, "Заявка должна быть готова к отгрузке для создания отгрузки")
        return redirect("core:request_detail", pk=pk)
    
    active_quote = obj.active_quote
    if not active_quote:
        messages.error(request, "Нельзя создать отгрузку без активного коммерческого предложения")
        return redirect("core:request_detail", pk=pk)
    
    if request.method == "POST":
        formset = RequestShipmentItemFormSet(request.POST, prefix="shipment_items")
        shipment_number = request.POST.get("shipment_number", "").strip()
        comment = request.POST.get("comment", "").strip()
        
        if formset.is_valid():
            shipment_items = []
            has_items = False
            
            for item_form in formset:
                if item_form.cleaned_data and not item_form.cleaned_data.get("DELETE"):
                    quantity = item_form.cleaned_data.get("quantity")
                    if quantity and quantity > 0:
                        has_items = True
                        shipment_items.append(item_form.cleaned_data)
            
            if not has_items:
                messages.error(request, "Добавьте хотя бы одну позицию для отгрузки")
            else:
                with transaction.atomic():
                    # Создаем отгрузку
                    shipment = RequestShipment.objects.create(
                        request=obj,
                        shipment_number=shipment_number,
                        shipped_by=request.user,
                        comment=comment,
                    )
                    
                    # Определяем, частичная ли отгрузка
                    total_shipped_now = sum(item["quantity"] for item in shipment_items)
                    is_partial = False
                    
                    # Создаем позиции отгрузки
                    for item_data in shipment_items:
                        quote_item_id = item_data.get("quote_item_id")
                        quote_item = None
                        if quote_item_id:
                            try:
                                quote_item = RequestQuoteItem.objects.get(id=quote_item_id, quote=active_quote)
                            except RequestQuoteItem.DoesNotExist:
                                pass
                        
                        if quote_item:
                            # Проверяем, не превышаем ли доступное количество
                            already_shipped = obj.get_shipped_quantity(quote_item)
                            available = quote_item.quantity - already_shipped
                            quantity_to_ship = min(item_data["quantity"], available)
                            
                            if already_shipped + quantity_to_ship < quote_item.quantity:
                                is_partial = True
                            
                            RequestShipmentItem.objects.create(
                                shipment=shipment,
                                quote_item=quote_item,
                                product=quote_item.product,
                                title=quote_item.title,
                                quantity=quantity_to_ship,
                                price=quote_item.price,
                            )
                    
                    shipment.is_partial = is_partial
                    shipment.save(update_fields=["is_partial"])
                    
                    # Обновляем статус заявки
                    if obj.is_fully_shipped():
                        obj.status = RequestStatus.SHIPPED
                    else:
                        obj.status = RequestStatus.PARTIALLY_SHIPPED
                    obj.save(update_fields=["status", "updated_at"])
                    
                    # Создаем запись в истории
                    RequestHistory.objects.create(
                        request=obj,
                        author=request.user,
                        from_status=RequestStatus.READY_TO_SHIP if obj.status == RequestStatus.PARTIALLY_SHIPPED else obj.status,
                        to_status=obj.status,
                        note=f"Создана отгрузка #{shipment.shipment_number or shipment.pk}",
                    )
                    
                    messages.success(request, f"Отгрузка #{shipment.shipment_number or shipment.pk} успешно создана")
                    return redirect("core:request_detail", pk=obj.pk)
        else:
            messages.error(request, "Проверьте ошибки в позициях отгрузки")
    else:
        # GET запрос - создаем начальные данные
        initial_data = []
        quote_items = active_quote.items.select_related("product", "request_item").all()
        
        for quote_item in quote_items:
            already_shipped = obj.get_shipped_quantity(quote_item)
            available = quote_item.quantity - already_shipped
            
            if available > 0:
                initial_data.append({
                    "quote_item_id": quote_item.id,
                    "product_id": quote_item.product_id if quote_item.product else None,
                    "title": quote_item.title,
                    "quantity_available": available,
                    "quantity": available,  # По умолчанию отгружаем все доступное
                    "price": quote_item.price,
                })
        
        formset = RequestShipmentItemFormSet(prefix="shipment_items", initial=initial_data)
    
    return render(request, "requests/shipment_form.html", {
        "request_obj": obj,
        "formset": formset,
        "active_quote": active_quote,
    })


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


# ---------- Маршрутный лист ----------
@require_GET
@require_groups("operator", "director", "warehouse")
def request_route_sheet(request, pk: int):
    """Генерация маршрутного листа для заявки"""
    obj = get_object_or_404(
        Request.objects.select_related("counterparty", "delivery_address", "delivery_contact"),
        pk=pk
    )
    
    # Получаем товары из активного КП или из последней отгрузки
    items = []
    active_quote = obj.active_quote
    if active_quote:
        quote_items = active_quote.items.select_related("product").all()
        for item in quote_items:
            items.append({
                "title": item.title,
                "quantity": item.quantity,
                "unit": "шт",
            })
    
    return render(request, "requests/route_sheet.html", {
        "obj": obj,
        "items": items,
    })


# ---------- УПД (Универсальный передаточный документ) ----------
@require_GET
@require_groups("operator", "director")
def request_upd(request, pk: int, shipment_id: int = None):
    """Генерация УПД для заявки или конкретной отгрузки"""
    obj = get_object_or_404(
        Request.objects.select_related("counterparty", "delivery_address", "delivery_contact"),
        pk=pk
    )
    
    shipment = None
    items = []
    total_amount = Decimal("0")
    
    if shipment_id:
        # УПД для конкретной отгрузки
        shipment = get_object_or_404(RequestShipment, pk=shipment_id, request=obj)
        shipment_items = shipment.items.select_related("product", "quote_item").all()
        for item in shipment_items:
            item_total = item.quantity * (item.price or Decimal("0"))
            total_amount += item_total
            items.append({
                "title": item.title,
                "quantity": item.quantity,
                "unit": "шт",
                "price": item.price or Decimal("0"),
                "total": item_total,
            })
    else:
        # УПД для всей заявки (по активному КП)
        active_quote = obj.active_quote
        if active_quote:
            quote_items = active_quote.items.select_related("product").all()
            for item in quote_items:
                item_total = item.total
                total_amount += item_total
                items.append({
                    "title": item.title,
                    "quantity": item.quantity,
                    "unit": "шт",
                    "price": item.price,
                    "total": item_total,
                })
    
    return render(request, "requests/upd.html", {
        "obj": obj,
        "shipment": shipment,
        "items": items,
        "total_amount": total_amount,
    })
