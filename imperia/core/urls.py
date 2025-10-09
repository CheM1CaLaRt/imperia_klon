# core/urls.py
from django.urls import path
from . import views_counterparty as vc

app_name = "core"

urlpatterns = [
    # Клиенты
    path("counterparties/", vc.counterparty_list, name="counterparty_list"),
    path("counterparties/new/", vc.counterparty_create, name="counterparty_create"),
    path("counterparties/<int:pk>/", vc.counterparty_detail, name="counterparty_detail"),
    path("counterparties/<int:pk>/edit/", vc.counterparty_update, name="counterparty_update"),
    path("counterparties/<int:pk>/delete/", vc.counterparty_delete, name="counterparty_delete"),
    path("counterparties/lookup/", vc.counterparty_lookup_inn, name="counterparty_lookup_inn"),
    path("counterparties/<int:pk>/finance/refresh/", vc.counterparty_refresh_finance, name="counterparty_refresh_finance"),

    # Контакты
    path("counterparties/<int:pk>/contacts/add/", vc.contact_add, name="contact_add"),
    path("counterparties/<int:pk>/contacts/<int:contact_id>/edit/", vc.contact_edit, name="contact_edit"),
    path("counterparties/<int:pk>/contacts/<int:contact_id>/delete/", vc.contact_delete, name="contact_delete"),
    path("counterparties/<int:pk>/managers/<int:user_id>/remove/", vc.counterparty_manager_remove, name="counterparty_manager_remove"),

    # Подсказки адресов (OSM)
    path("address/suggest/", vc.address_suggest_osm, name="address_suggest_osm"),

    # Дашборды и заявки на удаление
    path("director/", vc.director_dashboard, name="director_dashboard"),
    path("director/requests/<int:req_id>/approve/", vc.deletion_request_approve, name="deletion_request_approve"),
    path("director/requests/<int:req_id>/reject/", vc.deletion_request_reject, name="deletion_request_reject"),

    path("operator/", vc.operator_dashboard, name="operator_dashboard"),
    path(
        "deletion-requests/<int:req_id>/cancel/",
        vc.deletion_request_cancel,
        name="deletion_request_cancel",
    ),
    path(
        "deletion-requests/clear-rejected/",
        vc.deletion_requests_clear_rejected,
        name="deletion_requests_clear_rejected",
    ),
]
