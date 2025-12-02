from django.contrib import admin
from django.urls import path, reverse_lazy, include
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.conf.urls.static import static

from core import views
from core import views_counterparty
from core.views import home, product_list, product_detail_json
from core.api import product_by_barcode


urlpatterns = [
    path('admin/', admin.site.urls),
    path("", include(("core.urls", "core"), namespace="core")),
    path("", views.post_login_router, name="home"),
    path("home/", home, name="home"),
    path("products/", product_list, name="product-list"),
    path("warehouse/", views.warehouse_dashboard, name="warehouse_dashboard"),
    # НОВЫЙ дашборд
    path("warehouse/ui/", views.warehouse_new_dashboard, name="warehouse_new_dashboard"),
    path("warehouse/new/", views.warehouse_create, name="warehouse_create"),
    path("warehouse/<int:pk>/delete/", views.warehouse_delete, name="warehouse_delete"),

    path("warehouse/<int:pk>/", views.warehouse_detail, name="warehouse_detail"),
    path("warehouse/<int:pk>/put-away/", views.put_away_view, name="put_away"),
    path("warehouse/<int:pk>/move/", views.move_view, name="move_between_bins"),
    path("products/<int:pk>/card/", views.product_card, name="product_card"),
    path("ajax/product-by-barcode/", views.product_by_barcode, name="product_by_barcode"),
    path("ajax/product-create-inline/", views.product_create_inline, name="product_create_inline"),
    path("ajax/product/<int:pk>/edit/", views.product_update_inline, name="product_update_inline"),
    path("ajax/product/<int:pk>/delete/", views.product_delete_inline, name="product_delete_inline"),

# Ячейки
    path("warehouse/<int:pk>/bin/new/", views.bin_create, name="bin_create"),
    path("warehouse/<int:warehouse_pk>/bin/<int:pk>/edit/", views.bin_edit, name="bin_edit"),
    path("warehouse/<int:warehouse_pk>/bin/<int:bin_pk>/delete/",
                      views.bin_delete,
                      name="bin_delete",
                  ),

    # Редактирование позиции остатка
    path("warehouse/<int:warehouse_pk>/inventory/<int:pk>/edit/", views.inventory_edit, name="inventory_edit"),

    path("api/suggest/address/osm", views_counterparty.address_suggest_osm, name="address_suggest_osm"),



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