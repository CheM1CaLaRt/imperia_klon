from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.contrib.auth import get_user_model
import re
from datetime import date
from .widgets import AvatarInput
from django.contrib.auth.models import User
from .models import Profile
from django import forms
from .models import Warehouse
from .models import Inventory, StorageBin
from decimal import Decimal
from django import forms

User = get_user_model()

PHONE_RE = re.compile(r"^\+?\d{7,15}$")
TG_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")

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
    # чистый FileInput, без clearable-блоков
    avatar = forms.ImageField(
        required=False,
        widget=forms.FileInput(attrs={
            "id": "id_avatar",
            "accept": "image/*",
            "style": "position:absolute;left:-9999px;width:1px;height:1px;opacity:0;"
        })
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
            "phone": forms.TextInput(attrs={
                "placeholder": "+79990001122",
                "pattern": r"^\+?\d{7,15}$",
                "title": "7–15 цифр, допустим + в начале",
            }),
            "whatsapp": forms.TextInput(attrs={
                "placeholder": "+79990001122 или 79990001122",
                "pattern": r"^\+?\d{7,15}$",
                "title": "7–15 цифр, допустим + в начале",
            }),
            "telegram": forms.TextInput(attrs={
                "placeholder": "@username",
                "pattern": r"^@?[A-Za-z0-9_]{5,32}$",
                "title": "5–32 символа: латиница/цифры/_; можно с @",
            }),
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
            v = v[len("https://t.me/"):]
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

class WarehouseCreateForm(forms.ModelForm):
    class Meta:
        model = Warehouse
        fields = ["code", "name", "address", "comment", "is_active"]
        widgets = {
            "comment": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_code(self):
        code = self.cleaned_data["code"].strip()
        if not code:
            raise forms.ValidationError("Код обязателен")
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
        widget=forms.NumberInput(attrs={"step": "1"})
    )

    def __init__(self, *args, **kwargs):
        warehouse = kwargs.pop("warehouse")
        super().__init__(*args, **kwargs)
        self.fields["bin"].queryset = StorageBin.objects.filter(
            warehouse=warehouse, is_active=True
        ).order_by("code")

class StorageBinForm(forms.ModelForm):
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
        # уникальность кода в рамках склада
        qs = StorageBin.objects.filter(warehouse=self.warehouse, code__iexact=code)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Такая ячейка уже есть в этом складе")
        return code