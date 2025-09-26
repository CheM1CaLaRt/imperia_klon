from django.contrib.auth.decorators import user_passes_test
from django.urls import reverse_lazy

def group_required(*group_names, login_url="login"):
    """
    Использование:
      @login_required
      @group_required("warehouse", "director")
      def view(...):
          ...
    """
    def check(user):
        if not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        return user.groups.filter(name__in=group_names).exists()

    return user_passes_test(check, login_url=reverse_lazy(login_url))
