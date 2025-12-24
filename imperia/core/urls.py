# core/urls.py
from django.urls import path

# блок клиентов/контрагентов
from . import views_counterparty as vc
from . import views_counterparty_requests as vcr

# заявки
from . import views_requests as rq

# сборка (pick) — используем функции из views_pick
from . import views_pick as vp

# управление сотрудниками
from . import views_employees as ve

# управление компаниями
from . import views_companies as vcomp

app_name = "core"

urlpatterns = [
    # ===== Контрагенты: заявки на создание/ревью =====
    path("counterparty/requests/new/", vcr.counterparty_request_create, name="counterparty_request_create"),
    path("dashboard/manager/counterparty/requests/", vcr.manager_counterparty_requests, name="manager_counterparty_requests"),
    path("dashboard/review/counterparty/", vcr.counterparty_review_queue, name="counterparty_review_queue"),
    path("dashboard/review/counterparty/<int:pk>/approve/", vcr.counterparty_request_approve, name="counterparty_request_approve"),
    path("dashboard/review/counterparty/<int:pk>/reject/", vcr.counterparty_request_reject, name="counterparty_request_reject"),

    # ===== Заявки =====
    path("requests/", rq.request_list, name="request_list"),
    path("requests/new/", rq.request_create, name="request_create"),
    path("api/counterparty/addresses-contacts/", rq.counterparty_addresses_contacts, name="counterparty_addresses_contacts"),
    path("api/counterparty/add-address/", rq.counterparty_add_address, name="counterparty_add_address"),
    path("api/counterparty/add-contact/", rq.counterparty_add_contact, name="counterparty_add_contact"),
    path("requests/<int:pk>/", rq.request_detail, name="request_detail"),
    path("requests/<int:pk>/add-item/", rq.request_add_item, name="request_add_item"),
    path("requests/<int:pk>/item/<int:item_id>/update/", rq.request_update_item, name="request_update_item"),
    path("requests/<int:pk>/item/<int:item_id>/delete/", rq.request_delete_item, name="request_delete_item"),
    path("requests/<int:pk>/status/", rq.request_change_status, name="request_change_status"),

    # КП (файлы и товары)
    path("requests/<int:pk>/quote/create/", rq.request_quote_create_edit, name="request_quote_create"),
    path("requests/<int:pk>/quote/<int:quote_id>/edit/", rq.request_quote_create_edit, name="request_quote_edit"),
    path("requests/<int:pk>/quote/upload/", rq.request_upload_quote, name="request_upload_quote"),
    path("requests/<int:pk>/quote/<int:quote_id>/delete/", rq.request_delete_quote, name="request_delete_quote"),
    path("requests/<int:pk>/quote/<int:quote_id>/preview/", rq.request_quote_preview, name="request_quote_preview"),
    path("requests/<int:pk>/quotes/<int:qpk>/delete/", rq.request_quote_delete, name="request_quote_delete"),

    # Отгрузка
    path("requests/<int:pk>/shipment/create/", rq.request_shipment_create, name="request_shipment_create"),

    # Документы
    path("requests/<int:pk>/route-sheet/", rq.request_route_sheet, name="request_route_sheet"),
    path("requests/<int:pk>/upd/", rq.request_upd, name="request_upd"),
    path("requests/<int:pk>/upd/<int:shipment_id>/", rq.request_upd, name="request_upd_shipment"),
    path("requests/<int:pk>/upd-xml/", rq.request_upd_xml, name="request_upd_xml"),
    path("requests/<int:pk>/upd-xml/<int:shipment_id>/", rq.request_upd_xml, name="request_upd_xml_shipment"),

    # Оплата
    path("requests/<int:pk>/toggle-payment/", rq.request_toggle_payment, name="request_toggle_payment"),

    # ===== Контрагенты: CRUD и прочее =====
    path("counterparties/", vc.counterparty_list, name="counterparty_list"),
    path("counterparties/new/", vc.counterparty_create, name="counterparty_create"),
    path("counterparties/<int:pk>/", vc.counterparty_detail, name="counterparty_detail"),
    path("counterparties/<int:pk>/edit/", vc.counterparty_update, name="counterparty_update"),
    path("counterparties/<int:pk>/delete/", vc.counterparty_delete, name="counterparty_delete"),
    path("counterparties/lookup/", vc.counterparty_lookup_inn, name="counterparty_lookup_inn"),
    path("counterparties/<int:pk>/finance/refresh/", vc.counterparty_refresh_finance, name="counterparty_refresh_finance"),

    # Контакты контрагента
    path("counterparties/<int:pk>/contacts/add/", vc.contact_add, name="contact_add"),
    path("counterparties/<int:pk>/contacts/<int:contact_id>/edit/", vc.contact_edit, name="contact_edit"),
    path("counterparties/<int:pk>/contacts/<int:contact_id>/delete/", vc.contact_delete, name="contact_delete"),
    path("counterparties/<int:pk>/managers/<int:user_id>/remove/", vc.counterparty_manager_remove, name="counterparty_manager_remove"),

    # Подсказки адресов (OSM)
    path("address/suggest/", vc.address_suggest_osm, name="address_suggest_osm"),
    path("address/geocode/", vc.geocode_address, name="geocode_address"),

    # Дашборды директора / заявки на удаление контрагента
    path("director/", vc.director_dashboard, name="director_dashboard"),
    path("director/requests/<int:req_id>/approve/", vc.deletion_request_approve, name="deletion_request_approve"),
    path("director/requests/<int:req_id>/reject/", vc.deletion_request_reject, name="deletion_request_reject"),
    
    # Управление сотрудниками
    path("employees/", ve.employee_list, name="employee_list"),
    path("employees/new/", ve.employee_create, name="employee_create"),
    path("employees/<int:pk>/edit/", ve.employee_edit, name="employee_edit"),
    path("employees/<int:pk>/delete/", ve.employee_delete, name="employee_delete"),
    path("employees/<int:pk>/detail/", ve.employee_detail, name="employee_detail"),
    
    # Управление компаниями
    path("companies/", vcomp.company_list, name="company_list"),
    path("companies/new/", vcomp.company_create, name="company_create"),
    path("companies/<int:pk>/edit/", vcomp.company_edit, name="company_edit"),
    path("companies/<int:pk>/delete/", vcomp.company_delete, name="company_delete"),
    path("operator/", vc.operator_dashboard, name="operator_dashboard"),
    path("deletion-requests/<int:req_id>/cancel/", vc.deletion_request_cancel, name="deletion_request_cancel"),
    path("deletion-requests/clear-rejected/", vc.deletion_requests_clear_rejected, name="deletion_requests_clear_rejected"),

    # ===== Сборка (pick) =====
    # --- секция сборки ---
    path("requests/<int:pk>/pick/", vp.request_pick_section, name="request_pick_section"),
    path("requests/<int:pk>/pick/submit/", vp.pick_submit, name="pick_submit"),
    path("api/stock/lookup/", vp.stock_lookup_by_barcode, name="stock_lookup_by_barcode"),
    path("api/stock/lookup-by-sku/", vp.stock_lookup_by_sku, name="stock_lookup_by_sku"),
    path("api/stock/lookup-by-name/", vp.stock_lookup_by_name, name="stock_lookup_by_name"),
    path("api/stock/lookup-by-name-selected/", vp.stock_lookup_by_name_selected, name="stock_lookup_by_name_selected"),

    path(
        "requests/<int:pk>/pick/confirm/",
        vp.pick_confirm,
        name="pick_confirm",  # <— БЕЗ двоеточия в имени!
    ),
    path("requests/<int:pk>/pick/print/",   vp.pick_print,   name="pick_print"),
]
