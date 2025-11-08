from __future__ import annotations

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_GET, require_POST

from .permissions import require_groups
from .models import Product, Inventory
from .models_requests import Request, RequestStatus, RequestHistory
from .models_pick import PickItem  # <-- НОВОЕ

# --- формы (формсет сборки) ---
try:
    from .forms_pick import PickItemFormSet
except Exception:
    PickItemFormSet = None  # fallback

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

    return JsonResponse({"ok": True, "name": product.name, "location": location, "unit": unit})


@require_GET
@require_groups("operator", "director", "warehouse")
def stock_lookup_by_barcode(request):
    return stock_lookup(request)


@require_GET
@require_groups("operator", "director")
def request_pick_section(request, pk: int):
    # запасной URL — возвращаем на карточку
    return redirect("core:request_detail", pk=pk)


# ----------------------------- #
#     Сохранение листа сборки    #
# ----------------------------- #
@require_POST
@require_groups("operator", "director")
def pick_submit(request, pk: int):
    """
    Принимает форму сборки.
    commit=save -> сохранить (в т.ч. можно очистить лист сборки)
    commit=send -> сохранить и перевести в TO_PICK (только если есть позиции)
    """
    req = get_object_or_404(Request, pk=pk)
    commit = (request.POST.get("commit") or "save").lower()

    items = []

    if PickItemFormSet is not None:
        formset = PickItemFormSet(request.POST, prefix="pick")
        if not formset.is_valid():
            messages.error(request, "Проверьте строки сборки — есть ошибки.")
            return redirect("core:request_detail", pk=pk)

        for cd in formset.cleaned_data:
            if not cd or cd.get("DELETE"):
                continue
            if any(cd.get(k) for k in ("barcode", "name", "qty", "location", "unit", "price")):
                items.append({
                    "barcode":  cd.get("barcode") or "",
                    "name":     cd.get("name") or "",
                    "location": cd.get("location") or "",
                    "unit":     cd.get("unit") or "",
                    "qty":      int(cd.get("qty") or 0) or 1,
                    "price":    cd.get("price") or 0,
                })
    else:
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
                qty = int(q or 0) or 1
                try:
                    pr = float((price or "0").replace(",", "."))
                except Exception:
                    pr = 0
                items.append({
                    "barcode": bc, "name": nm, "location": loc, "unit": unit, "qty": qty, "price": pr
                })

    # ---- если позиций нет ----
    if not items:
        if commit == "send":
            messages.error(request, "Нельзя отправить на склад пустой лист сборки.")
            return redirect("core:request_detail", pk=pk)

        # commit=save: трактуем как очистку листа сборки
        deleted = PickItem.objects.filter(request=req).delete()[0]
        RequestHistory.objects.create(
            request=req, author=request.user,
            from_status=req.status, to_status=req.status,
            note=f"Очищен лист сборки (удалено позиций: {deleted}).",
        )
        messages.success(request, "Лист сборки очищен.")
        return redirect("core:request_detail", pk=pk)

    # ---- сохраняем новые позиции (перезапись) ----
    PickItem.objects.filter(request=req).delete()
    PickItem.objects.bulk_create([PickItem(request=req, **it) for it in items])

    # ---- поведение по commit ----
    if commit == "send":
        if req.status not in (RequestStatus.APPROVED, RequestStatus.TO_PICK):
            messages.error(request, "Заявка должна быть в статусе «Согласована».")
            return redirect("core:request_detail", pk=pk)

        old = req.status
        if req.status == RequestStatus.APPROVED:
            req.status = RequestStatus.TO_PICK
            req.save(update_fields=["status", "updated_at"])
            RequestHistory.objects.create(
                request=req, author=request.user,
                from_status=old, to_status=req.status,
                note=f"Создан лист сборки, позиций: {len(items)}",
            )
        else:
            RequestHistory.objects.create(
                request=req, author=request.user,
                from_status=old, to_status=old,
                note=f"Обновлён лист сборки, позиций: {len(items)}",
            )
        messages.success(request, "Отправлено на склад. Заявка появилась в списке склада.")
    else:
        RequestHistory.objects.create(
            request=req, author=request.user,
            from_status=req.status, to_status=req.status,
            note=f"Сохранён черновик листа сборки, позиций: {len(items)}",
        )
        messages.success(request, "Черновик листа сборки сохранён.")

    return redirect("core:request_detail", pk=pk)

