from django.contrib import admin
from django.urls import path, reverse_lazy
from django.contrib.auth import views as auth_views
from core import views
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    # даём пустому пути второе имя 'home'
    path('', views.post_login_router, name='home'),

    path('', views.post_login_router, name='post_login_router'),
    path('warehouse/', views.warehouse_dashboard, name='warehouse_dashboard'),
    path('operator/', views.operator_dashboard, name='operator_dashboard'),
    path('manager/', views.manager_dashboard, name='manager_dashboard'),
    path('director/', views.director_dashboard, name='director_dashboard'),
    path('profile/', views.profile_view, name='profile'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),

    # 🔐 смена пароля
    path(
        'password/change/',
        auth_views.PasswordChangeView.as_view(
            template_name='auth/password_change.html',
            success_url=reverse_lazy('password_change_done'),
        ),
        name='password_change',
    ),
    path(
        'password/change/done/',
        auth_views.PasswordChangeDoneView.as_view(
            template_name='auth/password_change_done.html'
        ),
        name='password_change_done',
    ),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
