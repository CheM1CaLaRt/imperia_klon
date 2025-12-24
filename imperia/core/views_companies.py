# core/views_companies.py
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .forms_companies import CompanyForm
from .models import Company
from .services.egrul import EgrulError, fetch_by_inn, parse_counterparty_payload


def _is_director(user):
    """Проверка, что пользователь является директором"""
    return user.is_authenticated and (
        user.is_superuser or user.groups.filter(name="director").exists()
    )


@login_required
@user_passes_test(_is_director)
def company_list(request):
    """Список всех компаний"""
    companies = Company.objects.all().order_by("name")
    
    # Поиск
    search_query = request.GET.get("q", "").strip()
    if search_query:
        companies = companies.filter(
            Q(name__icontains=search_query) |
            Q(full_name__icontains=search_query) |
            Q(inn__icontains=search_query)
        )
    
    # Фильтр по активности
    active_filter = request.GET.get("active", "")
    if active_filter == "1":
        companies = companies.filter(is_active=True)
    elif active_filter == "0":
        companies = companies.filter(is_active=False)
    
    # Пагинация
    paginator = Paginator(companies, 20)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)
    
    return render(request, "core/company_list.html", {
        "companies": page_obj,
        "search_query": search_query,
        "active_filter": active_filter,
    })


@login_required
@user_passes_test(_is_director)
@require_http_methods(["GET", "POST"])
def company_create(request):
    """Создание новой компании"""
    if request.method == "POST":
        form = CompanyForm(request.POST)
        if form.is_valid():
            try:
                company = form.save()
                messages.success(
                    request,
                    f"Компания {company.name} успешно создана."
                )
                return redirect("core:company_list")
            except Exception as e:
                messages.error(request, f"Ошибка при создании компании: {str(e)}")
        else:
            messages.error(request, "Пожалуйста, исправьте ошибки в форме.")
    else:
        form = CompanyForm()
    
    return render(request, "core/company_form.html", {
        "form": form,
        "title": "Создать компанию",
        "action": "create"
    })


@login_required
@user_passes_test(_is_director)
@require_http_methods(["GET", "POST"])
def company_edit(request, pk):
    """Редактирование компании"""
    company = get_object_or_404(Company, pk=pk)
    
    if request.method == "POST":
        form = CompanyForm(request.POST, instance=company)
        if form.is_valid():
            try:
                company = form.save()
                messages.success(
                    request,
                    f"Данные компании {company.name} обновлены."
                )
                return redirect("core:company_list")
            except Exception as e:
                messages.error(request, f"Ошибка при обновлении компании: {str(e)}")
        else:
            messages.error(request, "Пожалуйста, исправьте ошибки в форме.")
    else:
        form = CompanyForm(instance=company)
    
    return render(request, "core/company_form.html", {
        "form": form,
        "company": company,
        "title": "Редактировать компанию",
        "action": "edit"
    })


@login_required
@user_passes_test(_is_director)
@require_POST
def company_delete(request, pk):
    """Удаление компании"""
    company = get_object_or_404(Company, pk=pk)
    
    company_name = company.name
    
    # Проверяем, используется ли компания в заявках
    from .models_requests import Request
    requests_count = Request.objects.filter(company=company).count()
    
    if requests_count > 0:
        messages.error(
            request,
            f"Невозможно удалить компанию {company_name}, так как она используется в {requests_count} заявке(ах)."
        )
        return redirect("core:company_list")
    
    try:
        company.delete()
        messages.success(request, f"Компания {company_name} успешно удалена.")
    except Exception as e:
        messages.error(request, f"Ошибка при удалении компании: {str(e)}")
    
    return redirect("core:company_list")


@login_required
@user_passes_test(_is_director)
@require_GET
def company_lookup_inn(request):
    """
    AJAX: /companies/lookup/?inn=...
    Возвращаем JSON для автозаполнения формы компании по ИНН из ЕГРЮЛ.
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

