from django import template

register = template.Library()

@register.filter(name="in_groups")
def in_groups(user, groups_csv: str) -> bool:
    """
    Usage: {% if request.user|in_groups:"warehouse,director" %} ... {% endif %}
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    names = [g.strip() for g in groups_csv.split(",") if g.strip()]
    return user.groups.filter(name__in=names).exists()
