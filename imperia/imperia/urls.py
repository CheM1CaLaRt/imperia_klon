# imperia/urls.py
from django.contrib import admin
from django.urls import path
from core import views
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("admin/", admin.site.urls),

    # Логин/логаут
    path("", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("after-login/", views.post_login_router, name="post_login_router"),

    # Дашборды по ролям
    path("warehouse/", views.warehouse_dashboard, name="warehouse_dashboard"),
    path("operator/", views.operator_dashboard, name="operator_dashboard"),
    path("manager/", views.manager_dashboard, name="manager_dashboard"),
    path("director/", views.director_dashboard, name="director_dashboard"),
    path("profile/", views.profile_view, name="profile"),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)