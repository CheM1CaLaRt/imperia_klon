# core/permissions.py
from functools import wraps
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden

ALLOWED_GROUPS = {"warehouse", "director"}

def user_in_allowed_groups(user) -> bool:
    if not user.is_authenticated:
        return False
    return user.is_superuser or user.groups.filter(name__in=ALLOWED_GROUPS).exists()

def warehouse_or_director_required(view_func):
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if user_in_allowed_groups(request.user):
            return view_func(request, *args, **kwargs)
        return HttpResponseForbidden("Недостаточно прав")
    return _wrapped
