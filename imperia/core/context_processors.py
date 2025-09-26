from .permissions import user_in_allowed_groups
def user_profile(request):
    """
    Возвращает объект профиля в шаблоны.
    Если профиль ещё не создан — создаём (для авторизованных).
    """
    if not request.user.is_authenticated:
        return {}
    from .models import Profile  # локальный импорт, чтобы избежать циклов
    profile, _ = Profile.objects.get_or_create(user=request.user)
    return {"profile": profile}

def nav_flags(request):
    user = getattr(request, "user", None)
    is_wh_or_director = False
    if user and user.is_authenticated:
        is_wh_or_director = user.is_superuser or user.groups.filter(name__in=["warehouse", "director"]).exists()
    return {"is_wh_or_director": is_wh_or_director}
