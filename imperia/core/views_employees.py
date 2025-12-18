# core/views_employees.py
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST
from django.core.paginator import Paginator

from .forms_employees import EmployeeForm
from .models import Profile

User = get_user_model()


def _is_director(user):
    """Проверка, что пользователь является директором"""
    return user.is_authenticated and (
        user.is_superuser or user.groups.filter(name="director").exists()
    )


@login_required
@user_passes_test(_is_director)
def employee_list(request):
    """Список всех сотрудников"""
    employees = User.objects.select_related("profile").prefetch_related("groups").all().order_by("-date_joined")
    
    # Поиск
    search_query = request.GET.get("q", "").strip()
    if search_query:
        employees = employees.filter(
            Q(username__icontains=search_query) |
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query) |
            Q(email__icontains=search_query)
        )
    
    # Фильтр по роли
    role_filter = request.GET.get("role", "")
    if role_filter:
        employees = employees.filter(groups__name=role_filter)
    
    # Пагинация
    paginator = Paginator(employees, 20)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)
    
    # Получаем список всех ролей для фильтра
    from django.contrib.auth.models import Group
    roles = Group.objects.all().order_by("name")
    
    return render(request, "core/employee_list.html", {
        "employees": page_obj,
        "roles": roles,
        "search_query": search_query,
        "role_filter": role_filter,
    })


@login_required
@user_passes_test(_is_director)
@require_http_methods(["GET", "POST"])
def employee_create(request):
    """Создание нового сотрудника"""
    if request.method == "POST":
        form = EmployeeForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    user = form.save()
                    messages.success(
                        request,
                        f"Сотрудник {user.get_full_name() or user.username} успешно создан."
                    )
                    return redirect("core:employee_list")
            except Exception as e:
                messages.error(request, f"Ошибка при создании сотрудника: {str(e)}")
        else:
            messages.error(request, "Пожалуйста, исправьте ошибки в форме.")
    else:
        form = EmployeeForm()
    
    return render(request, "core/employee_form.html", {
        "form": form,
        "title": "Создать сотрудника",
        "action": "create"
    })


@login_required
@user_passes_test(_is_director)
@require_http_methods(["GET", "POST"])
def employee_edit(request, pk):
    """Редактирование сотрудника"""
    user = get_object_or_404(User, pk=pk)
    
    # Нельзя редактировать суперпользователя (кроме самого себя, если ты суперпользователь)
    if user.is_superuser and not request.user.is_superuser:
        messages.error(request, "Недостаточно прав для редактирования этого пользователя.")
        return redirect("core:employee_list")
    
    if request.method == "POST":
        form = EmployeeForm(request.POST, instance=user)
        if form.is_valid():
            try:
                with transaction.atomic():
                    user = form.save()
                    messages.success(
                        request,
                        f"Данные сотрудника {user.get_full_name() or user.username} обновлены."
                    )
                    return redirect("core:employee_list")
            except Exception as e:
                messages.error(request, f"Ошибка при обновлении сотрудника: {str(e)}")
        else:
            messages.error(request, "Пожалуйста, исправьте ошибки в форме.")
    else:
        form = EmployeeForm(instance=user)
    
    return render(request, "core/employee_form.html", {
        "form": form,
        "employee": user,
        "title": "Редактировать сотрудника",
        "action": "edit"
    })


@login_required
@user_passes_test(_is_director)
@require_POST
def employee_delete(request, pk):
    """Удаление сотрудника"""
    user = get_object_or_404(User, pk=pk)
    
    # Нельзя удалить самого себя
    if user == request.user:
        messages.error(request, "Вы не можете удалить самого себя.")
        return redirect("core:employee_list")
    
    # Нельзя удалить суперпользователя
    if user.is_superuser and not request.user.is_superuser:
        messages.error(request, "Недостаточно прав для удаления этого пользователя.")
        return redirect("core:employee_list")
    
    username = user.get_full_name() or user.username
    
    try:
        user.delete()
        messages.success(request, f"Сотрудник {username} успешно удален.")
    except Exception as e:
        messages.error(request, f"Ошибка при удалении сотрудника: {str(e)}")
    
    return redirect("core:employee_list")


@login_required
@user_passes_test(_is_director)
@require_http_methods(["GET"])
def employee_detail(request, pk):
    """Детальная информация о сотруднике (JSON для модального окна)"""
    user = get_object_or_404(
        User.objects.select_related("profile").prefetch_related("groups"),
        pk=pk
    )
    
    try:
        profile = user.profile
    except Profile.DoesNotExist:
        profile = None
    
    role = user.groups.first()
    
    data = {
        "id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "full_name": user.get_full_name() or user.username,
        "email": user.email or "",
        "is_active": user.is_active,
        "date_joined": user.date_joined.strftime("%d.%m.%Y %H:%M") if user.date_joined else "",
        "last_login": user.last_login.strftime("%d.%m.%Y %H:%M") if user.last_login else "Никогда",
        "role": role.name if role else "Нет роли",
        "phone": profile.phone if profile else "",
        "whatsapp": profile.whatsapp if profile else "",
        "telegram": profile.telegram if profile else "",
        "vk": profile.vk if profile else "",
        "birth_date": profile.birth_date.strftime("%d.%m.%Y") if profile and profile.birth_date else "",
    }
    
    return JsonResponse(data)

