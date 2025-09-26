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
