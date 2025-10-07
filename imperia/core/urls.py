from django.urls import path
from . import views_counterparty as v

app_name = "core"
urlpatterns = [
    path("counterparties/new/", v.counterparty_create, name="counterparty_create"),
    path("counterparties/lookup/", v.counterparty_lookup_inn, name="counterparty_lookup_inn"),
    path("counterparties/<int:pk>/", v.counterparty_detail, name="counterparty_detail"),
    path("counterparties/<int:pk>/refresh-finance/", v.counterparty_refresh_finance, name="counterparty_refresh_finance"),
    path("counterparties/<int:pk>/contacts/new/", v.contact_add, name="contact_add"),
]