# core/views_requests.py
from decimal import Decimal, InvalidOperation
import re
from mimetypes import guess_type
from django.urls import reverse
from django.contrib import messages
from django.db.models import Q, ForeignKey, OneToOneField, ManyToManyField
from django.http import HttpResponseBadRequest, FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
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
)
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
    if u.groups.filter(name="warehouse").exists() and not (
        u.is_superuser or u.groups.filter(name="director").exists()
    ):
        qs = qs.filter(
            status__in=[
                RequestStatus.TO_PICK,
                RequestStatus.IN_PROGRESS,
                RequestStatus.READY_TO_SHIP,
            ]
        )
    elif u.groups.filter(name="manager").exists() and not (
        u.is_superuser or u.groups.filter(name="director").exists()
    ):
        cond = Q(initiator=u) | Q(assignee=u)
        if has_cp_manager_fk:
            cond |= Q(counterparty__manager=u)
        if has_cp_managers_m2m:
            cond |= Q(counterparty__managers=u)
        qs = qs.filter(cond)

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


# Импортируем из общих утилит
from .utils.roles import _in_groups


# ---------- Создание заявки ----------
@require_groups("manager", "operator", "director")
def request_create(request):
    if request.method == "POST":
        form = RequestCreateForm(request.POST, user=request.user)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.initiator = request.user
            obj.status = (
                RequestStatus.SUBMITTED
                if "submit" in request.POST
                else RequestStatus.DRAFT
            )
            obj.save()

            # bulk-позиции
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
                    RequestItem.objects.create(
                        request=obj, title=title, quantity=qty, note=note
                    )

            messages.success(request, "Заявка создана")
            return redirect("core:request_detail", pk=obj.pk)
    else:
        form = RequestCreateForm(user=request.user)
    return render(request, "requests/form.html", {"form": form})


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
            RequestStatus.SUBMITTED,
            RequestStatus.CANCELED,
            RequestStatus.APPROVED,
            RequestStatus.REJECTED,
        },
        "operator": {
            RequestStatus.QUOTE,
            RequestStatus.TO_PICK,
            RequestStatus.IN_PROGRESS,
            RequestStatus.READY_TO_SHIP,
            RequestStatus.DELIVERED,
            RequestStatus.CANCELED,
        },
        "warehouse": {
            RequestStatus.IN_PROGRESS,
            RequestStatus.READY_TO_SHIP,
        },
        "director": set(s for s, _ in RequestStatus.choices),
    }

    user_groups = {g.name for g in u.groups.all()}
    can = u.is_superuser or any(to in allowed.get(g, set()) for g in user_groups)
    if not can:
        return HttpResponseBadRequest("Недостаточно прав для смены статуса")

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
