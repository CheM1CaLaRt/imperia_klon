from django.urls import path
from . import views_counterparty as v


app_name = "core"
urlpatterns = [
    path("counterparties/new/", v.counterparty_create, name="counterparty_create"),
    path("counterparties/<int:pk>/", v.counterparty_detail, name="counterparty_detail"),
    path("counterparties/<int:pk>/edit/", v.counterparty_update, name="counterparty_update"),
    path("counterparties/<int:pk>/delete/", v.counterparty_delete, name="counterparty_delete"),

    path("counterparties/lookup/", v.counterparty_lookup_inn, name="counterparty_lookup_inn"),
    path("counterparties/<int:pk>/refresh-finance/", v.counterparty_refresh_finance, name="counterparty_refresh_finance"),

    path("counterparties/<int:pk>/contacts/new/", v.contact_add, name="contact_add"),
    path("counterparties/<int:pk>/contacts/<int:contact_id>/edit/", v.contact_edit, name="contact_edit"),
    path("counterparties/<int:pk>/contacts/<int:contact_id>/delete/", v.contact_delete, name="contact_delete"),
    path(
        "counterparties/<int:pk>/managers/<int:user_id>/remove/",
        v.counterparty_manager_remove,
        name="counterparty_manager_remove",
    ),

    path("counterparties/", v.counterparty_list, name="counterparty_list"),
]
