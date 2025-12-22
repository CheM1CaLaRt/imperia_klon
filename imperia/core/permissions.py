# core/permissions.py
from functools import wraps
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.contrib.auth.decorators import permission_required

ALLOWED_GROUPS = {"warehouse", "director"}

def user_in_allowed_groups(user) -> bool:
    if not user.is_authenticated:
        return False
    return user.is_superuser or user.groups.filter(name__in=ALLOWED_GROUPS).exists()

def warehouse_or_director_required(view_func):
    return permission_required('core.view_product', raise_exception=True)(view_func)

def user_in_groups(user, *groups):
    return user.is_superuser or user.groups.filter(name__in=groups).exists()

def require_groups(*groups):
    def decorator(view):
        @wraps(view)
        def _wrapped(request, *args, **kwargs):
            # Сначала проверяем аутентификацию
            if not request.user.is_authenticated:
                from django.contrib.auth.views import redirect_to_login
                from django.conf import settings
                login_url = getattr(settings, 'LOGIN_URL', '/login/')
                return redirect_to_login(request.get_full_path(), login_url)
            
            # Затем проверяем права доступа
            if not user_in_groups(request.user, *groups, "director"):
                from django.contrib import messages
                from django.shortcuts import redirect
                messages.error(request, "У вас нет прав для доступа к этой странице")
                # Редиректим на страницу заявки или главную
                pk = kwargs.get('pk')
                if pk:
                    from django.urls import reverse
                    try:
                        return redirect(reverse('core:request_detail', kwargs={'pk': pk}))
                    except Exception:
                        pass
                return redirect('post_login_router')
            return view(request, *args, **kwargs)
        return _wrapped
    return decorator
