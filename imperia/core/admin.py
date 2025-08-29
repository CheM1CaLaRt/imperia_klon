from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.utils.html import format_html
import re

from .models import Profile
try:
    from .forms import ProfileForm  # если делали форму с валидацией
except Exception:
    ProfileForm = None


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
