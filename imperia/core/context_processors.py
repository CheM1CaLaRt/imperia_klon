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
