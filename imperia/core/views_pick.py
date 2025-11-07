from __future__ import annotations

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_GET, require_POST

from .permissions import require_groups
from .models import Product, Inventory
from .models_requests import Request, RequestStatus, RequestHistory

# --- формы (формсет сборки) ---
try:
    from .forms_pick import PickItemFormSet
except Exception:
    PickItemFormSet = None  # fallback: обработаем без формсета


# ----------------------------- #
#     Автозаполнение по ШК      #
# ----------------------------- #
@require_GET
@require_groups("operator", "director", "warehouse")
def stock_lookup(request):
    barcode = (request.GET.get("barcode") or "").strip()
    if not barcode:
        return JsonResponse({"ok": False, "error": "empty"}, status=400)

    product = Product.objects.filter(barcode=barcode).first()
    if not product:
        return JsonResponse({"ok": False, "error": "not_found"}, status=404)

    inv = (
        Inventory.objects
        .filter(product=product, quantity__gt=0)
        .select_related("warehouse", "bin")
        .order_by("-quantity")
        .first()
    )
    location = inv.bin.code if inv and inv.bin else ""
    unit = "шт"

    return JsonResponse({
        "ok": True,
        "name": product.name,
        "location": location,
        "unit": unit,
    })


@require_GET
@require_groups("operator", "director", "warehouse")
def stock_lookup_by_barcode(request):
    return stock_lookup(request)


# -------------------------------- #
#   Показ секции (запасной URL)    #
# -------------------------------- #
@require_GET
@require_groups("operator", "director")
def request_pick_section(request, pk: int):
    return redirect("core:request_detail", pk=pk)


# ----------------------------- #
#     Сохранение листа сборки    #
# ----------------------------- #
@require_POST
@require_groups("operator", "director")
def pick_submit(request, pk: int):
    """
    Принимает форму сборки.
    commit=save -> просто сохранить лист (без смены статуса);
    commit=send -> перевести заявку в TO_PICK и записать историю.
    """
    req = get_object_or_404(Request, pk=pk)
    commit = (request.POST.get("commit") or "save").lower()

    # считаем валидные строки
    items_count = 0

    if PickItemFormSet is not None:
        formset = PickItemFormSet(request.POST, prefix="pick")
        if not formset.is_valid():
            messages.error(request, "Проверьте строки сборки — есть ошибки.")
            return redirect("core:request_detail", pk=pk)

        for cd in formset.cleaned_data:
            if not cd or cd.get("DELETE"):
                continue
            if any(cd.get(k) for k in ("barcode", "name", "qty", "location", "unit", "price")):
                items_count += 1
    else:
        # fallback без формсета
        try:
            total = int(request.POST.get("pick-TOTAL_FORMS", 0))
        except ValueError:
            total = 0
        for i in range(total):
            bc   = (request.POST.get(f"pick-{i}-barcode") or "").strip()
            nm   = (request.POST.get(f"pick-{i}-name") or "").strip()
            q    = (request.POST.get(f"pick-{i}-qty") or "").strip()
            loc  = (request.POST.get(f"pick-{i}-location") or "").strip()
            unit = (request.POST.get(f"pick-{i}-unit") or "").strip()
            price = (request.POST.get(f"pick-{i}-price") or "").strip()
            if bc or nm or q or loc or unit or price:
                items_count += 1

    if items_count == 0:
        messages.error(request, "Добавьте хотя бы одну позицию для сборки.")
        return redirect("core:request_detail", pk=pk)

    # --- поведение по команде ---
    if commit == "send":
        # Отправка на склад (смена статуса)
        if req.status not in (RequestStatus.APPROVED, RequestStatus.TO_PICK):
            messages.error(request, "Заявка должна быть в статусе «Согласована».")
            return redirect("core:request_detail", pk=pk)

        old = req.status
        if req.status == RequestStatus.APPROVED:
            req.status = RequestStatus.TO_PICK
            req.save(update_fields=["status", "updated_at"])
            RequestHistory.objects.create(
                request=req,
                author=request.user,
                from_status=old,
                to_status=req.status,
                note=f"Создан лист сборки, позиций: {items_count}",
            )
        else:
            RequestHistory.objects.create(
                request=req,
                author=request.user,
                from_status=old,
                to_status=old,
                note=f"Обновлён лист сборки, позиций: {items_count}",
            )
        messages.success(request, "Отправлено на склад. Заявка появилась в списке склада.")
    else:
        # Просто сохранили черновик листа (без смены статуса)
        RequestHistory.objects.create(
            request=req,
            author=request.user,
            from_status=req.status,
            to_status=req.status,
            note=f"Сохранён черновик листа сборки, позиций: {items_count}",
        )
        messages.success(request, "Черновик листа сборки сохранён.")

    return redirect("core:request_detail", pk=pk)
