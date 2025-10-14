# core/forms.py
from datetime import date
import re

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.forms import inlineformset_factory

from .widgets import AvatarInput
from .models import (
    Profile,
    Warehouse,
    Inventory,
    StorageBin,
    Counterparty,
    CounterpartyContact,
    CounterpartyDocument,
    inn_validator,
)
from .models import CounterpartyDeletionRequest
from .models import CounterpartyCreateRequest, CounterpartyCreateRequestDocument


User = get_user_model()

# -----------------------------
# Регулярки
# -----------------------------
PHONE_RE = re.compile(r"^\+?\d{7,15}$")
TG_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")

# =============================
# Пользователь / Профиль
# =============================
class UserUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("first_name", "last_name", "email")
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "input"}),
            "last_name": forms.TextInput(attrs={"class": "input"}),
            "email": forms.EmailInput(attrs={"class": "input"}),
        }


class ProfileUpdateForm(forms.ModelForm):
    avatar = forms.ImageField(
        required=False,
        widget=forms.FileInput(
            attrs={
                "id": "id_avatar",
                "accept": "image/*",
                "style": "position:absolute;left:-9999px;width:1px;height:1px;opacity:0;",
            }
        ),
    )

    class Meta:
        model = Profile
        fields = ("avatar", "phone", "whatsapp", "telegram", "vk", "birth_date")
        widgets = {
            "phone": forms.TextInput(attrs={"class": "input"}),
            "whatsapp": forms.TextInput(attrs={"class": "input"}),
            "telegram": forms.TextInput(attrs={"class": "input"}),
            "vk": forms.URLInput(attrs={"class": "input"}),
            "birth_date": forms.DateInput(attrs={"type": "date", "class": "input"}),
        }


class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ["avatar", "phone", "whatsapp", "telegram", "vk", "birth_date"]
        widgets = {
            "avatar": AvatarInput(),
            "phone": forms.TextInput(
                attrs={
                    "placeholder": "+79990001122",
                    "pattern": r"^\+?\d{7,15}$",
                    "title": "7–15 цифр, допустим + в начале",
                }
            ),
            "whatsapp": forms.TextInput(
                attrs={
                    "placeholder": "+79990001122 или 79990001122",
                    "pattern": r"^\+?\d{7,15}$",
                    "title": "7–15 цифр, допустим + в начале",
                }
            ),
            "telegram": forms.TextInput(
                attrs={
                    "placeholder": "@username",
                    "pattern": r"^@?[A-Za-z0-9_]{5,32}$",
                    "title": "5–32 символа: латиница/цифры/_; можно с @",
                }
            ),
            "vk": forms.URLInput(attrs={"placeholder": "https://vk.com/username"}),
            "birth_date": forms.DateInput(attrs={"type": "date"}),
        }

    def clean_phone(self):
        v = (self.cleaned_data.get("phone") or "").strip().replace(" ", "")
        if not v:
            return v
        if not PHONE_RE.fullmatch(v):
            raise ValidationError("Телефон: 7–15 цифр, можно + в начале.")
        return v

    def clean_whatsapp(self):
        v = (self.cleaned_data.get("whatsapp") or "").strip().replace(" ", "")
        if not v:
            return v
        if not PHONE_RE.fullmatch(v):
            raise ValidationError("WhatsApp: 7–15 цифр, можно + в начале.")
        return v

    def clean_telegram(self):
        v = (self.cleaned_data.get("telegram") or "").strip()
        if not v:
            return v
        if v.startswith("https://t.me/"):
            v = v[len("https://t.me/") :]
        v = v.lstrip("@")
        if not TG_RE.fullmatch(v):
            raise ValidationError("Telegram: 5–32 символов (латиница, цифры, _).")
        return "@" + v

    def clean_vk(self):
        v = (self.cleaned_data.get("vk") or "").strip()
        if not v:
            return v
        URLValidator()(v)
        if "vk.com" not in v:
            raise ValidationError("Ссылка должна вести на vk.com.")
        return v

    def clean_birth_date(self):
        bd = self.cleaned_data.get("birth_date")
        if not bd:
            return bd
        today = date.today()
        if bd > today:
            raise ValidationError("Дата рождения не может быть в будущем.")
        if bd.year < today.year - 120:
            raise ValidationError("Слишком ранняя дата рождения.")
        return bd


# =============================
# Склад
# =============================
class WarehouseCreateForm(forms.ModelForm):
    class Meta:
        model = Warehouse
        fields = ["code", "name", "address", "comment", "is_active"]
        widgets = {
            "code": forms.TextInput(attrs={"class": "input", "autofocus": True}),
            "name": forms.TextInput(attrs={"class": "input"}),
            "address": forms.TextInput(attrs={"class": "input"}),
            "comment": forms.Textarea(attrs={"rows": 3, "class": "input"}),
        }

    def clean_code(self):
        code = (self.cleaned_data.get("code") or "").strip()
        if not code:
            raise forms.ValidationError("Код обязателен")
        return code


class StorageBinForm(forms.ModelForm):
    """Передавайте warehouse=... при инициализации формы для проверки уникальности кода."""
    class Meta:
        model = StorageBin
        fields = ["code", "description", "is_active"]
        widgets = {
            "code": forms.TextInput(attrs={"autofocus": True}),
            "description": forms.TextInput(),
        }

    def __init__(self, *args, **kwargs):
        self.warehouse = kwargs.pop("warehouse", None)
        super().__init__(*args, **kwargs)

    def clean_code(self):
        code = (self.cleaned_data.get("code") or "").strip()
        if not code:
            raise forms.ValidationError("Укажите код ячейки")
        qs = StorageBin.objects.filter(warehouse=self.warehouse, code__iexact=code)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Такая ячейка уже есть в этом складе")
        return code


class InventoryEditForm(forms.Form):
    bin = forms.ModelChoiceField(
        queryset=StorageBin.objects.none(),
        required=False,
        empty_label="— (без ячейки)",
        label="Ячейка",
    )
    quantity = forms.IntegerField(
        min_value=0,
        label="Количество",
        help_text="0 — удалить позицию",
        widget=forms.NumberInput(attrs={"step": "1"}),
    )

    def __init__(self, *args, **kwargs):
        warehouse = kwargs.pop("warehouse")
        super().__init__(*args, **kwargs)
        self.fields["bin"].queryset = StorageBin.objects.filter(
            warehouse=warehouse, is_active=True
        ).order_by("code")


class PutAwayForm(forms.Form):
    bin_code = forms.CharField(label="Ячейка", max_length=40, required=False, help_text="Можно оставить пустым")
    barcode = forms.CharField(label="Штрихкод", max_length=64)
    quantity = forms.DecimalField(label="Кол-во", min_value=0.001, decimal_places=3, max_digits=14)
    create_bin = forms.BooleanField(label="Создавать ячейку, если нет", required=False, initial=True)


class MoveForm(forms.Form):
    bin_from = forms.CharField(label="Из ячейки", max_length=40)
    bin_to = forms.CharField(label="В ячейку", max_length=40)
    barcode = forms.CharField(label="Штрихкод", max_length=64)
    quantity = forms.DecimalField(label="Кол-во", min_value=0.001, decimal_places=3, max_digits=14)
    create_bin = forms.BooleanField(label="Создать ячейку-получателя, если нет", required=False, initial=True)


# =============================
# Товары (инлайн-создание)
# =============================
class ProductInlineCreateForm(forms.Form):
    name = forms.CharField(
        label="Название",
        max_length=512,
        widget=forms.TextInput(attrs={"class": "input w-full"}),
    )
    barcode = forms.CharField(
        label="Штрихкод",
        max_length=128,
        required=False,
        widget=forms.TextInput(attrs={"class": "input w-full font-mono"}),
    )
    brand = forms.CharField(
        label="Бренд",
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"class": "input w-full"}),
    )
    vendor = forms.CharField(
        label="Поставщик",
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"class": "input w-full"}),
    )
    image_url = forms.URLField(
        label="URL изображения",
        required=False,
        widget=forms.URLInput(attrs={"class": "input w-full", "placeholder": "https://..."}),
    )
    description = forms.CharField(
        label="Описание",
        required=False,
        widget=forms.Textarea(attrs={"class": "input w-full", "rows": 5}),
    )

    # удобные поля вместо JSON
    country = forms.CharField(
        label="Страна",
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"class": "input w-full"}),
    )
    weight_kg = forms.DecimalField(
        label="Вес, кг",
        required=False,
        decimal_places=3,
        max_digits=12,
        widget=forms.NumberInput(attrs={"class": "input w-full", "step": "0.001"}),
    )
    volume_m3 = forms.DecimalField(
        label="Объём, м³",
        required=False,
        decimal_places=6,
        max_digits=12,
        widget=forms.NumberInput(attrs={"class": "input w-full", "step": "0.000001"}),
    )
    pkg_h_cm = forms.DecimalField(
        label="Высота, см",
        required=False,
        decimal_places=2,
        max_digits=12,
        widget=forms.NumberInput(attrs={"class": "input w-full", "step": "0.01"}),
    )
    pkg_w_cm = forms.DecimalField(
        label="Ширина, см",
        required=False,
        decimal_places=2,
        max_digits=12,
        widget=forms.NumberInput(attrs={"class": "input w-full", "step": "0.01"}),
    )
    pkg_d_cm = forms.DecimalField(
        label="Глубина, см",
        required=False,
        decimal_places=2,
        max_digits=12,
        widget=forms.NumberInput(attrs={"class": "input w-full", "step": "0.01"}),
    )
    description_ext = forms.CharField(
        label="Расширенное описание",
        required=False,
        widget=forms.Textarea(attrs={"class": "input w-full", "rows": 6}),
    )
    vendor_code = forms.CharField(
        label="Артикул поставщика",
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={"class": "input w-full font-mono"}),
    )
    price_contracts = forms.DecimalField(
        label="Цена (contracts), ₽",
        required=False,
        decimal_places=2,
        max_digits=12,
        widget=forms.NumberInput(attrs={"class": "input w-full", "step": "0.01"}),
    )


# =============================
# Контрагенты
# =============================
class CounterpartyCreateForm(forms.ModelForm):
    class Meta:
        model = Counterparty
        fields = [
            "inn", "name", "full_name", "kpp", "ogrn",
            "registration_country", "address", "actual_address",
            "bank_name", "bank_bik", "bank_account",
            "website", "managers",
        ]
        widgets = {
            "inn": forms.TextInput(attrs={"class": "input", "placeholder": "ИНН"}),
            "name": forms.TextInput(attrs={"class": "input"}),
            "full_name": forms.TextInput(attrs={"class": "input"}),
            "kpp": forms.TextInput(attrs={"class": "input"}),
            "ogrn": forms.TextInput(attrs={"class": "input"}),
            "registration_country": forms.TextInput(attrs={"class": "input"}),
            "address": forms.TextInput(attrs={"class": "input", "placeholder": "Юридический адрес"}),
            "actual_address": forms.TextInput(attrs={"class": "input", "placeholder": "Фактический адрес / адрес доставки"}),
            "bank_name": forms.TextInput(attrs={"class": "input", "placeholder": "Наименование банка"}),
            "bank_bik": forms.TextInput(attrs={"class": "input", "placeholder": "9 цифр"}),
            "bank_account": forms.TextInput(attrs={"class": "input", "placeholder": "Номер счёта (20 цифр)"}),
            "website": forms.URLInput(attrs={"class": "input", "placeholder": "https://"}),
            "managers": forms.SelectMultiple(attrs={"class": "w-full h-36 rounded-lg border px-3 py-2"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # только пользователи из группы "manager"
        try:
            managers_group = Group.objects.get(name="manager")
            qs = User.objects.filter(groups=managers_group).order_by(
                "last_name", "first_name", "username"
            ).distinct()
        except Group.DoesNotExist:
            qs = User.objects.filter(groups__name="manager").order_by(
                "last_name", "first_name", "username"
            ).distinct()

        self.fields["managers"].queryset = qs
        self.fields["managers"].label = "Закреплённые менеджеры"
        self.fields["managers"].help_text = "Можно выбрать несколько."


class CounterpartyContactForm(forms.ModelForm):
    class Meta:
        model = CounterpartyContact
        fields = ["full_name", "position", "email", "phone", "mobile", "note"]


ContactFormSet = inlineformset_factory(
    parent_model=Counterparty,
    model=CounterpartyContact,
    form=CounterpartyContactForm,
    fields=["full_name", "position", "email", "phone", "mobile", "note"],
    extra=1,
    can_delete=True,
)


# =============================
# Документы контрагента (сканы)
# =============================
class CounterpartyDocumentForm(forms.ModelForm):
    class Meta:
        model = CounterpartyDocument
        fields = ["title", "file"]
        widgets = {
            "title": forms.TextInput(
                attrs={"class": "w-full", "placeholder": "Например: Устав, Договор №..."}
            ),
            "file": forms.ClearableFileInput(attrs={"class": "w-full"}),
        }
        labels = {"title": "Название документа", "file": "Файл"}


CounterpartyDocumentFormSet = inlineformset_factory(
    parent_model=Counterparty,
    model=CounterpartyDocument,
    form=CounterpartyDocumentForm,
    fields=("title", "file"),
    extra=1,
    can_delete=True,
)

class CounterpartyDeletionRequestForm(forms.ModelForm):
    class Meta:
        model = CounterpartyDeletionRequest
        fields = ["comment"]
        labels = {"comment": "Комментарий (почему удалить)"}
        widgets = {
            "comment": forms.Textarea(
                attrs={"rows": 3, "class": "w-full rounded-lg border px-3 py-2"}
            )
        }

class CounterpartyCreateRequestForm(forms.ModelForm):
    class Meta:
        model = CounterpartyCreateRequest
        fields = [
            "inn", "kpp", "ogrn",
            "name", "full_name",
            "registration_country",
            "address", "actual_address",
            "bank_name", "bank_bik", "bank_account",
            "website",
        ]
        widgets = {
            "inn": forms.TextInput(attrs={"class": "w-full rounded-xl border px-3 py-2", "placeholder": "ИНН"}),
            "name": forms.TextInput(attrs={"class": "w-full rounded-xl border px-3 py-2"}),
            "full_name": forms.Textarea(attrs={"rows": 2, "class": "w-full rounded-xl border px-3 py-2"}),
            "kpp": forms.TextInput(attrs={"class": "w-full rounded-xl border px-3 py-2"}),
            "ogrn": forms.TextInput(attrs={"class": "w-full rounded-xl border px-3 py-2"}),
            "registration_country": forms.TextInput(attrs={"class": "w-full rounded-xl border px-3 py-2"}),
            "address": forms.Textarea(attrs={"rows": 2, "class": "w-full rounded-xl border px-3 py-2"}),
            "actual_address": forms.Textarea(attrs={"rows": 2, "class": "w-full rounded-xl border px-3 py-2"}),
            "bank_name": forms.TextInput(attrs={"class": "w-full rounded-xl border px-3 py-2"}),
            "bank_bik": forms.TextInput(attrs={"class": "w-full rounded-xl border px-3 py-2"}),
            "bank_account": forms.TextInput(attrs={"class": "w-full rounded-xl border px-3 py-2"}),
            "website": forms.URLInput(attrs={"class": "w-full rounded-xl border px-3 py-2"}),
        }

class CounterpartyCreateRequestDocumentForm(forms.ModelForm):
    class Meta:
        model = CounterpartyCreateRequestDocument
        fields = ["title", "file"]

CounterpartyCreateRequestDocFormSet = inlineformset_factory(
    CounterpartyCreateRequest,
    CounterpartyCreateRequestDocument,
    form=CounterpartyCreateRequestDocumentForm,
    extra=1,
    can_delete=True,
)