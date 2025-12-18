from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.utils.html import format_html
import re
from .models import Supplier, Product, ProductImage, ProductCertificate, ProductPrice, ImportBatch, ProductCategory
from .models import Warehouse, StorageBin, Inventory, StockMovement
from .models import Profile
try:
    from .forms import ProfileForm  # если делали форму с валидацией
except Exception:
    ProfileForm = None

from .models import Counterparty, CounterpartyFinance, CounterpartyContact
from .models import CounterpartyCreateRequest, CounterpartyCreateRequestDocument
from .admin_requests import *  # noqa

from .models_pick import PickItem



class ProfileInline(admin.StackedInline):
    model = Profile
    if ProfileForm:
        form = ProfileForm          # подключаем форму, если есть
    can_delete = False
    fk_name = "user"
    extra = 0

    # readonly-поля должны ссылаться на АТРИБУТЫ/МЕТОДЫ этого класса или поля модели
    readonly_fields = ("avatar_preview", "quick_links")
    fields = (
        "avatar", "avatar_preview",
        "phone", "whatsapp", "telegram", "vk",
        "birth_date",
        "quick_links",
    )

    @admin.display(description="Аватар (превью)")
    def avatar_preview(self, obj):
        if obj and obj.avatar:
            return format_html(
                '<img src="{}" style="height:80px;width:80px;object-fit:cover;border-radius:50%;">',
                obj.avatar.url
            )
        return "—"

    @admin.display(description="Быстрые ссылки")
    def quick_links(self, obj):
        if not obj:
            return "—"
        parts = []
        if obj.whatsapp:
            wa = re.sub(r"\D", "", obj.whatsapp)  # только цифры для wa.me
            if wa:
                parts.append(f'<a href="https://wa.me/{wa}" target="_blank">WhatsApp</a>')
        if obj.telegram:
            tg = obj.telegram.lstrip("@")
            parts.append(f'<a href="https://t.me/{tg}" target="_blank">Telegram</a>')
        if obj.vk:
            parts.append(f'<a href="{obj.vk}" target="_blank">VK</a>')
        return format_html(" | ".join(parts)) if parts else "—"


class UserAdmin(BaseUserAdmin):
    inlines = (ProfileInline,)
    list_display = ("username", "first_name", "last_name", "email", "is_staff", "phone_display", "birth_date_display")
    list_filter = ("is_staff", "is_superuser", "is_active", "groups")

    def phone_display(self, obj):
        return getattr(getattr(obj, "profile", None), "phone", "")
    phone_display.short_description = "Телефон"

    def birth_date_display(self, obj):
        bd = getattr(getattr(obj, "profile", None), "birth_date", None)
        return bd.strftime("%d.%m.%Y") if bd else ""
    birth_date_display.short_description = "ДР"


# Перерегистрируем стандартную модель пользователя с нашим админом
admin.site.unregister(User)
admin.site.register(User, UserAdmin)


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "phone", "telegram", "vk", "birth_date")
    search_fields = ("user__username", "user__first_name", "user__last_name", "phone", "telegram", "vk")
    list_filter = ("birth_date",)

@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("code", "name")
    search_fields = ("code", "name")

class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 0

class ProductCertificateInline(admin.TabularInline):
    model = ProductCertificate
    extra = 0

class ProductPriceInline(admin.TabularInline):
    model = ProductPrice
    extra = 0

@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "icon", "parent", "order", "is_active", "products_count")
    list_filter = ("is_active", "parent")
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at")
    
    def products_count(self, obj):
        return obj.products.count()
    products_count.short_description = "Товаров"

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "barcode", "supplier", "category", "sku", "brand", "manufacturer_country", "is_active")
    list_filter = ("supplier", "category", "brand", "manufacturer_country", "is_active")
    search_fields = ("name", "barcode", "sku", "vendor_code", "brand")
    inlines = [ProductImageInline, ProductCertificateInline, ProductPriceInline]
    readonly_fields = ("created_at", "updated_at", "last_import_batch")

@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = ("supplier", "source_name", "started_at", "finished_at", "items_total", "items_upserted")
    list_filter = ("supplier",)

@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code", "name", "address")

@admin.register(StorageBin)
class StorageBinAdmin(admin.ModelAdmin):
    list_display = ("code", "warehouse", "is_active", "description")
    list_filter = ("warehouse", "is_active")
    search_fields = ("code", "description", "warehouse__code", "warehouse__name")

@admin.register(Inventory)
class InventoryAdmin(admin.ModelAdmin):
    list_display = ("warehouse", "bin", "product", "quantity", "updated_at")
    list_filter = ("warehouse", "bin")
    search_fields = ("product__name", "product__barcode", "bin__code", "warehouse__code")

@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "movement_type", "warehouse", "bin_from", "bin_to", "product", "quantity", "actor")
    list_filter = ("movement_type", "warehouse")
    search_fields = ("product__name", "product__barcode", "bin_from__code", "bin_to__code")
    date_hierarchy = "timestamp"

class CounterpartyContactInline(admin.TabularInline):
    model = CounterpartyContact
    extra = 0
    fields = ("full_name", "position", "email", "phone", "mobile", "note")
    show_change_link = True

@admin.register(Counterparty)
class CounterpartyAdmin(admin.ModelAdmin):
    list_display = ("name", "inn", "kpp", "ogrn", "website", "updated_at")
    search_fields = ("name", "full_name", "inn", "kpp", "ogrn", "website")
    readonly_fields = ("created_at", "updated_at", "meta_json")
    filter_horizontal = ("managers",)  # ← удобно выбирать много пользователей
    inlines = [CounterpartyContactInline]

@admin.register(CounterpartyFinance)
class CounterpartyFinanceAdmin(admin.ModelAdmin):
    list_display = ("counterparty", "revenue_last", "profit_last", "fetched_at")
    readonly_fields = ("fetched_at", "data")

@admin.register(CounterpartyContact)
class CounterpartyContactAdmin(admin.ModelAdmin):
    list_display = ("full_name", "counterparty", "position", "email", "phone", "mobile", "created_at")
    search_fields = ("full_name", "counterparty__name", "email", "phone", "mobile")
    list_filter = ("position",)


class CounterpartyCreateRequestDocumentInline(admin.TabularInline):
    model = CounterpartyCreateRequestDocument
    extra = 0

@admin.register(CounterpartyCreateRequest)
class CounterpartyCreateRequestAdmin(admin.ModelAdmin):
    inlines = [CounterpartyCreateRequestDocumentInline]
    list_display = ("id", "inn", "name", "manager", "status", "created_at", "reviewed_by", "reviewed_at")
    list_filter = ("status", "manager")
    search_fields = ("inn", "name", "full_name")

@admin.register(PickItem)
class PickItemAdmin(admin.ModelAdmin):
    list_display = ("id", "request", "name", "barcode", "qty", "price", "location", "unit")
    list_filter  = ("request",)
    search_fields = ("name", "barcode", "request__id")