def _in_groups(user, names):
    """Проверяет, принадлежит ли пользователь хотя бы к одной из указанных групп."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=names).exists()


def is_manager(user):
    return _in_groups(user, ["manager"])


def is_operator(user):
    return _in_groups(user, ["operator"])


def is_director(user):
    return _in_groups(user, ["director"])


def can_review(user):
    return is_operator(user) or is_director(user)