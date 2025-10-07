from .permissions import user_in_allowed_groups
# core/context_processors.py

def user_profile(request):
    """
    Возвращает объект профиля в шаблоны.
    Если профиль ещё не создан — создаём (для авторизованных).
    """
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    from .models import Profile  # локальный импорт, чтобы избежать циклов
    profile, _ = Profile.objects.get_or_create(user=request.user)
    return {"profile": profile}


def nav_flags(request):
    """
    Набор флагов для навигации/доступа в шаблонах.
    - is_operator / is_manager / is_director / is_warehouse
    - is_wh_or_director  (сохранён твой старый флаг совместимости)
    - show_clients_link  (показывать пункт 'Клиенты' в шапке)
    """
    user = getattr(request, "user", None)

    def in_groups(names):
        return bool(
            user
            and user.is_authenticated
            and user.groups.filter(name__in=names).exists()
        )

    is_operator = in_groups(["operator"])
    is_manager = in_groups(["manager"])
    is_director = in_groups(["director"])
    is_warehouse = in_groups(["warehouse"])

    # старый флаг совместимости — как было у тебя
    is_wh_or_director = (user.is_superuser if (user and user.is_authenticated) else False) \
        or is_warehouse or is_director

    # кнопка "Клиенты" видна операторам, менеджерам и директорам
    show_clients_link = any([is_operator, is_manager, is_director])

    return {
        "is_operator": is_operator,
        "is_manager": is_manager,
        "is_director": is_director,
        "is_warehouse": is_warehouse,
        "is_wh_or_director": is_wh_or_director,
        "show_clients_link": show_clients_link,
    }


