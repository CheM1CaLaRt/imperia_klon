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
        @login_required
        def _wrapped(request, *args, **kwargs):
            # Проверяем права доступа
            if not user_in_groups(request.user, *groups, "director"):
                from django.contrib import messages
                from django.shortcuts import redirect
                messages.error(request, "У вас нет прав для доступа к этой странице")
                # Редиректим на страницу заявки или главную
                if hasattr(request, 'resolver_match') and request.resolver_match:
                    # Пытаемся получить pk из kwargs для редиректа на заявку
                    pk = kwargs.get('pk')
                    if pk:
                        from django.urls import reverse
                        try:
                            return redirect(reverse('core:request_detail', kwargs={'pk': pk}))
                        except:
                            pass
                return redirect('post_login_router')
            return view(request, *args, **kwargs)
        return _wrapped
    return decorator
