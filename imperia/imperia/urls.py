from django.contrib import admin
from django.urls import path, reverse_lazy
from django.contrib.auth import views as auth_views
from core import views
from django.conf import settings
from django.conf.urls.static import static
from core.api import product_by_barcode
from django.contrib import admin
from django.contrib import admin
from django.urls import path
from core.views import home, product_list
from core.views import product_detail_json
from django.urls import path
from core import views

urlpatterns = [
    path('admin/', admin.site.urls),
    # даём пустому пути второе имя 'home'
    path('', views.post_login_router, name='home'),
    path("", home, name="post_login_router"),  # ссылка из base.html
    path("home/", home, name="home"),  # альтернативное имя
    path("products/", product_list, name="product-list"),
    path("warehouse/", views.warehouse_dashboard, name="warehouse_dashboard"),
    # НОВЫЙ дашборд
    path("warehouse/ui/", views.warehouse_new_dashboard, name="warehouse_new_dashboard"),
    path("warehouse/new/", views.warehouse_create, name="warehouse_create"),
    path("warehouse/<int:pk>/delete/", views.warehouse_delete, name="warehouse_delete"),

    path("warehouse/<int:pk>/", views.warehouse_detail, name="warehouse_detail"),
    path("warehouse/<int:pk>/put-away/", views.put_away_view, name="put_away"),
    path("warehouse/<int:pk>/move/", views.move_view, name="move_between_bins"),
    path("warehouse/<int:warehouse_pk>/inventory/<int:pk>/",
         views.inventory_edit, name="inventory_edit"),

    path("warehouse/<int:pk>/bins/new/", views.bin_create, name="bin_create"),
    path("warehouse/<int:warehouse_pk>/bins/<int:pk>/edit/", views.bin_edit, name="bin_edit"),
    path("warehouse/<int:warehouse_pk>/bins/<int:pk>/delete/", views.bin_delete, name="bin_delete"),

    path('operator/', views.operator_dashboard, name='operator_dashboard'),
    path('manager/', views.manager_dashboard, name='manager_dashboard'),
    path('director/', views.director_dashboard, name='director_dashboard'),
    path('profile/', views.profile_view, name='profile'),
    path("api/products/barcode/<str:barcode>/", product_by_barcode, name="product-by-barcode"),
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

urlpatterns += [
    path("products/<int:pk>/json/", product_detail_json, name="product-detail-json"),
]