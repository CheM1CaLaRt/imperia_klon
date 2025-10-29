# core/views_requests.py
from decimal import Decimal, InvalidOperation
import re
from django.contrib import messages
from django.db.models import Q, ForeignKey
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.apps import apps
from django.conf import settings

from .permissions import require_groups
from .forms_requests import (
    RequestForm,
    RequestCreateForm,
    RequestItemForm,
    RequestItemEditForm,
    RequestQuoteForm,
)
from .models_requests import Request, RequestItem, RequestStatus, RequestHistory, RequestQuote
from mimetypes import guess_type
from django.http import FileResponse, Http404
from django.views.decorators.clickjacking import xframe_options_exempt



# ---------- Список заявок ----------
@require_groups("manager", "operator", "warehouse", "director")
def request_list(request):
    status = request.GET.get("status")
    qs = Request.objects.select_related("initiator", "assignee", "counterparty")

    u = request.user
    Counterparty = apps.get_model("core", "Counterparty")

    # Проверим: Counterparty.manager — FK на пользователя? (для менеджера)
    is_manager_fk_to_user = False
    try:
        fld = Counterparty._meta.get_field("manager")
        if isinstance(fld, ForeignKey):
            remote = fld.remote_field.model
            is_manager_fk_to_user = (
                remote == settings.AUTH_USER_MODEL
                or (hasattr(remote, "_meta") and hasattr(u, "_meta") and remote._meta.label == u._meta.label)
            )
    except Exception:
        is_manager_fk_to_user = False

    if u.groups.filter(name="warehouse").exists() and not (u.is_superuser or u.groups.filter(name="director").exists()):
        # Склад видит только то, что ему передано/в работе
        qs = qs.filter(status__in=[RequestStatus.TO_PICK, RequestStatus.IN_PROGRESS, RequestStatus.READY_TO_SHIP])
    elif u.groups.filter(name="manager").exists() and not (u.is_superuser or u.groups.filter(name="director").exists()):
        # Менеджер — только свои и по своим клиентам (если есть FK manager)
        if is_manager_fk_to_user:
            qs = qs.filter(Q(initiator=u) | Q(counterparty__manager=u))
        else:
            qs = qs.filter(initiator=u)
    elif u.groups.filter(name="operator").exists() and not (u.is_superuser or u.groups.filter(name="director").exists()):
        # ОПЕРАТОР — ВИДИТ КАК ДИРЕКТОР (никаких фильтров)
        pass
    else:
        # Директор/суперпользователь — всё
        pass

    if status:
        qs = qs.filter(status=status)

    ctx = {"requests": qs.order_by("-created_at")[:500], "status": status, "statuses": RequestStatus}
    return render(request, "requests/list.html", ctx)

def _parse_qty(v: str) -> Decimal:
    v = (v or "").strip().replace(",", ".")
    if not v:
        return Decimal("1")
    try:
        return Decimal(v)
    except InvalidOperation:
        return Decimal("1")

@require_groups("manager", "operator", "director")
def request_create(request):
    if request.method == "POST":
        form = RequestCreateForm(request.POST, user=request.user)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.initiator = request.user
            obj.status = RequestStatus.SUBMITTED if "submit" in request.POST else RequestStatus.DRAFT
            obj.save()

            # создаём позиции из textarea (каждая строка — одна позиция)
            bulk = form.cleaned_data.get("items_bulk", "") or ""
            for raw in bulk.splitlines():
                line = raw.strip()
                if not line:
                    continue
                parts = [p.strip() for p in re.split(r"[;|,]", line, maxsplit=2)]
                title = parts[0] if parts else ""
                qty = _parse_qty(parts[1] if len(parts) > 1 else "")
                note = parts[2] if len(parts) > 2 else ""
                if title:
                    RequestItem.objects.create(request=obj, title=title, quantity=qty, note=note)

            messages.success(request, "Заявка создана")
            return redirect("core:request_detail", pk=obj.pk)
    else:
        form = RequestCreateForm(user=request.user)
    return render(request, "requests/form.html", {"form": form})


# ---------- Детали заявки ----------
@require_groups("manager", "operator", "warehouse", "director")
def request_detail(request, pk: int):
    obj = get_object_or_404(
        Request.objects.select_related("initiator", "assignee", "counterparty").prefetch_related(
            "items",  # без items__product — не тянем каталог
            "history",
            "comments",
        ),
        pk=pk,
    )
    item_form = RequestItemForm()

    # поддержка инлайн-редактирования: ?edit=<item_id>
    edit_item = None
    edit_form = None
    edit_id = request.GET.get("edit")
    if edit_id:
        try:
            edit_item = obj.items.get(pk=int(edit_id))
            edit_form = RequestItemEditForm(instance=edit_item)
        except (ValueError, RequestItem.DoesNotExist):
            edit_item = None
            edit_form = None

    quote_form = RequestQuoteForm()

    return render(
        request,
        "requests/detail.html",
        {
            "obj": obj,
            "item_form": item_form,
            "edit_item": edit_item,
            "edit_form": edit_form,
            "quote_form": quote_form,  # ← ВАЖНО: всегда передаём
            "statuses": RequestStatus,
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


# ---------- Обновить позицию (инлайн) ----------
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

    # защита от незнакомых значений
    if to not in dict(RequestStatus.choices):
        return HttpResponseBadRequest("Неизвестный статус")

    # Разрешения по ролям
    allowed = {
        # Менеджер: отправка/отмена своей заявки + решение по КП
        "manager": {
            RequestStatus.SUBMITTED,   # Отправить черновик
            RequestStatus.CANCELED,    # Отменить
            RequestStatus.APPROVED,    # ← Согласовано
            RequestStatus.REJECTED,    # ← Не согласовали
        },

        # Оператор: производственный путь, но НЕ утверждает КП и НЕ завершает
        "operator": {
            RequestStatus.QUOTE,
            RequestStatus.TO_PICK,
            RequestStatus.IN_PROGRESS,
            RequestStatus.READY_TO_SHIP,
            RequestStatus.DELIVERED,
            RequestStatus.CANCELED,
        },

        # Склад: только после передачи в сборку, доводит до «Доставлена»
        "warehouse": {
            RequestStatus.IN_PROGRESS,
            RequestStatus.READY_TO_SHIP,
            # RequestStatus.DELIVERED,
        },

        # Директор: всё
        "director": set(s for s, _ in RequestStatus.choices),
    }

    user_groups = {g.name for g in u.groups.all()}
    can = u.is_superuser or any(to in allowed.get(g, set()) for g in user_groups)
    if not can:
        return HttpResponseBadRequest("Недостаточно прав для смены статуса")

    # Склад — двигается только после передачи в сборку
    if (
        "warehouse" in user_groups
        and to in {RequestStatus.IN_PROGRESS, RequestStatus.READY_TO_SHIP, RequestStatus.DELIVERED}
        and obj.status not in {RequestStatus.TO_PICK, RequestStatus.IN_PROGRESS, RequestStatus.READY_TO_SHIP}
        and not (u.is_superuser or "director" in user_groups)
    ):
        return HttpResponseBadRequest("Заявка ещё не передана на склад")

    from_status = obj.status
    obj.status = to

    # Авто-завершение: если доставлена и оплачено — завершить
    if to == RequestStatus.DELIVERED and getattr(obj, "is_paid", False):
        obj.status = RequestStatus.DONE

    obj.save(update_fields=["status", "updated_at"])
    RequestHistory.objects.create(request=obj, author=u, from_status=from_status, to_status=obj.status)
    messages.success(request, "Статус обновлён")
    return redirect("core:request_detail", pk=pk)

# ---------- Загрузить файл КП ----------
@require_POST
@require_groups("operator", "director")  # только оператор/директор загружают
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
        # если оператор сразу ставит статус "quote" — можно разрешить на UI отдельной кнопкой; тут только upload
    else:
        messages.error(request, "Не удалось загрузить файл КП")
    return redirect("core:request_detail", pk=pk)


# ---------- Удалить файл КП ----------
@require_POST
@require_groups("operator", "director")
def request_delete_quote(request, pk: int, quote_id: int):
    obj = get_object_or_404(Request, pk=pk)
    q = get_object_or_404(RequestQuote, pk=quote_id, request=obj)
    # оператор может удалить только свои файлы; директор — любые
    if (q.uploaded_by_id != request.user.id) and (not request.user.groups.filter(name="director").exists()) and (not request.user.is_superuser):
        return HttpResponseBadRequest("Можно удалять только свои файлы")
    q.delete()
    messages.success(request, "Файл КП удалён")
    return redirect("core:request_detail", pk=pk)


# ---------- Просмотр файл КП ----------
@xframe_options_exempt
def request_quote_preview(request, pk: int, quote_id: int):
    """Отдать файл КП для просмотра в iframe."""
    q = get_object_or_404(RequestQuote, pk=quote_id, request_id=pk)
    if not q.file:
        raise Http404("Файл не найден")
    ctype = guess_type(q.original_name or q.file.name)[0] or "application/pdf"
    resp = FileResponse(q.file.open("rb"), content_type=ctype)
    # Показываем в окне, а не скачиваем
    resp["Content-Disposition"] = f'inline; filename="{q.original_name or q.file.name}"'
    return resp


# ---------- Смена статуса оплаты ----------
@require_POST
@require_groups("operator", "director")
def request_toggle_payment(request, pk: int):
    obj = get_object_or_404(Request, pk=pk)
    obj.is_paid = "is_paid" in request.POST
    # Если уже доставлен и теперь оплачен — завершаем
    if obj.is_paid and obj.status == RequestStatus.DELIVERED:
        obj.status = RequestStatus.DONE
    obj.save(update_fields=["is_paid", "status", "updated_at"])
    messages.success(request, "Статус оплаты обновлён")
    return redirect("core:request_detail", pk=pk)
