from django.contrib.auth.decorators import user_passes_test, permission_required
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponseBadRequest
from django.contrib import messages
from django.views.decorators.http import require_POST, require_GET
from .forms import CounterpartyCreateForm
from .models import Counterparty, CounterpartyFinance
from .services.egrul import fetch_by_inn, parse_counterparty_payload, fetch_finance_by_inn, EgrulError
from .forms import CounterpartyCreateForm, ContactFormSet, CounterpartyContactForm
from .models import Counterparty, CounterpartyFinance, CounterpartyContact
def _is_operator_or_director(user):
    return user.is_authenticated and user.groups.filter(name__in=["operator","director"]).exists()

@user_passes_test(_is_operator_or_director)
def counterparty_create(request):
    """
    Страница с формой: вводим ИНН, жмем кнопку «Найти в ЕГРЮЛ» (AJAX)
    → поля автозаполняются → сохраняем.
    """
    if request.method == "POST":
        form = CounterpartyCreateForm(request.POST)
        if form.is_valid():
            inn = form.cleaned_data["inn"]

            obj, created = Counterparty.objects.get_or_create(inn=inn, defaults=form.cleaned_data)
            if not created:
                # обновим данные с формы (поля могли поправить вручную)
                for f in ["name","full_name","registration_country","kpp","ogrn","address"]:
                    setattr(obj, f, form.cleaned_data.get(f))
            obj.save()

            # Финансы подтянем сразу (не критично, если упадет)
            try:
                fin_json, revenue, profit = fetch_finance_by_inn(inn)
                CounterpartyFinance.objects.update_or_create(
                    counterparty=obj,
                    defaults={"data": fin_json, "revenue_last": revenue, "profit_last": profit},
                )
            except EgrulError as e:
                messages.warning(request, f"Не удалось обновить финансы: {e}")

            messages.success(request, "Контрагент сохранён.")
            return redirect("core:counterparty_detail", pk=obj.pk)
    else:
        form = CounterpartyCreateForm()

    return render(request, "core/counterparty_form.html", {"form": form})


@user_passes_test(_is_operator_or_director)
@require_GET
def counterparty_lookup_inn(request):
    """
    AJAX: ?inn=... → вернем JSON для автозаполнения формы
    """
    inn = (request.GET.get("inn") or "").strip()
    if not inn.isdigit() or len(inn) not in (10, 12):
        return HttpResponseBadRequest("Некорректный ИНН")

    try:
        raw = fetch_by_inn(inn)
        payload = parse_counterparty_payload(raw)
        # чтобы ответ был легче, уберём meta_json из AJAX (она нужна только при сохранении и в detail)
        payload.pop("meta_json", None)
        return JsonResponse({"ok": True, "data": payload})
    except EgrulError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=502)


@user_passes_test(_is_operator_or_director)
def counterparty_detail(request, pk: int):
    obj = get_object_or_404(Counterparty.objects.select_related("finance"), pk=pk)
    return render(request, "core/counterparty_detail.html", {"obj": obj})

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

@user_passes_test(_is_operator_or_director)
def counterparty_create(request):
    if request.method == "POST":
        form = CounterpartyCreateForm(request.POST)
        formset = ContactFormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            inn = form.cleaned_data["inn"]
            obj, created = Counterparty.objects.get_or_create(inn=inn, defaults=form.cleaned_data)
            if not created:
                for f in ["name","full_name","registration_country","kpp","ogrn","address","website"]:
                    setattr(obj, f, form.cleaned_data.get(f))
            obj.save()

            # привязываем контакты
            formset.instance = obj
            formset.save()

            # подтянем финансы (как раньше)
            try:
                fin_json, revenue, profit = fetch_finance_by_inn(inn)
                CounterpartyFinance.objects.update_or_create(
                    counterparty=obj,
                    defaults={"data": fin_json, "revenue_last": revenue, "profit_last": profit},
                )
            except EgrulError as e:
                messages.warning(request, f"Не удалось обновить финансы: {e}")

            messages.success(request, "Контрагент сохранён.")
            return redirect("core:counterparty_detail", pk=obj.pk)
    else:
        form = CounterpartyCreateForm()
        formset = ContactFormSet()

    return render(request, "core/counterparty_form.html", {"form": form, "formset": formset})


@user_passes_test(_is_operator_or_director)
def counterparty_detail(request, pk: int):
    obj = get_object_or_404(Counterparty.objects.select_related("finance").prefetch_related("contacts"), pk=pk)
    return render(request, "core/counterparty_detail.html", {"obj": obj})


@user_passes_test(_is_operator_or_director)
def contact_add(request, pk: int):
    obj = get_object_or_404(Counterparty, pk=pk)
    if request.method == "POST":
        form = CounterpartyContactForm(request.POST)
        if form.is_valid():
            CounterpartyContact.objects.create(counterparty=obj, **form.cleaned_data)
            messages.success(request, "Контакт добавлен.")
            return redirect("core:counterparty_detail", pk=obj.pk)
    else:
        form = CounterpartyContactForm()
    return render(request, "core/contact_form.html", {"obj": obj, "form": form})
