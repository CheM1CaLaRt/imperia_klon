# core/views.py
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import Group
from django.shortcuts import redirect, render
from django.http import HttpRequest
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from .forms import UserUpdateForm, ProfileForm
from .models import Profile

ROLE_TO_URL = {
    "warehouse": "warehouse_dashboard",
    "operator": "operator_dashboard",
    "manager": "manager_dashboard",
    "director": "director_dashboard",  # управляющий
}

def in_group(group_name):
    def check(user):
        return user.is_authenticated and user.groups.filter(name=group_name).exists()
    return check

def group_required(group_name):
    return user_passes_test(in_group(group_name), login_url="login")

def login_view(request: HttpRequest):
    if request.user.is_authenticated:
        return redirect("post_login_router")

    context = {"error": None}
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            return redirect("post_login_router")
        context["error"] = "Неверный логин или пароль"
    return render(request, "login.html", context)


@login_required
def profile_view(request):
    user = request.user
    profile, _ = Profile.objects.get_or_create(user=user)

    if request.method == "POST":
        u_form = UserUpdateForm(request.POST, instance=user)
        p_form = ProfileForm(request.POST, request.FILES, instance=profile)
        if u_form.is_valid() and p_form.is_valid():
            u_form.save()
            p_form.save()
            messages.success(request, "Профиль обновлён.")
            return redirect("profile")
    else:
        u_form = UserUpdateForm(instance=user)
        p_form = ProfileForm(instance=profile)

    return render(request, "profile.html", {
        "u_form": u_form,
        "p_form": p_form,
        "username": user.username,         # показываем, но не редактируем
        "roles": list(user.groups.values_list("name", flat=True)),  # только для отображения
    })
@login_required
def post_login_router(request: HttpRequest):
    """
    Определяем первую доступную роль пользователя и кидаем в соответствующий раздел.
    Если ролей несколько — приоритет по порядку в ROLE_TO_URL.
    """
    for role, url_name in ROLE_TO_URL.items():
        if request.user.groups.filter(name=role).exists():
            return redirect(url_name)
    # Если ролей нет — можно отправить в заглушку или назад на логин
    return render(request, "no_role.html")

def logout_view(request: HttpRequest):
    logout(request)
    return redirect("login")

# --- Дашборды ролей ---
@login_required
@group_required("warehouse")
def warehouse_dashboard(request):
    return render(request, "dashboards/warehouse.html")

@login_required
@group_required("operator")
def operator_dashboard(request):
    return render(request, "dashboards/operator.html")

@login_required
@group_required("manager")
def manager_dashboard(request):
    return render(request, "dashboards/manager.html")

@login_required
@group_required("director")
def director_dashboard(request):
    return render(request, "dashboards/director.html")
