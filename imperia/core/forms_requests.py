# core/forms_requests.py
from django import forms
from django.apps import apps
from django.conf import settings
from django.db.models import ForeignKey

from .models_requests import Request, RequestItem, RequestQuote
from .models import Counterparty


def _counterparty_manager_is_user_fk() -> bool:
    """
    Проверяем, что Counterparty.manager — это ForeignKey на модель пользователя.
    Нужно, чтобы безопасно фильтровать контрагентов для менеджера.
    """
    try:
        fld = Counterparty._meta.get_field("manager")
        if not isinstance(fld, ForeignKey):
            return False
        remote = fld.remote_field.model  # это класс модели пользователя
        # сверяем с фактическим классом AUTH_USER_MODEL
        UserModel = apps.get_model(settings.AUTH_USER_MODEL)
        return remote is UserModel
    except Exception:
        return False


class RequestForm(forms.ModelForm):
    counterparty = forms.ModelChoiceField(
        queryset=Counterparty.objects.none(), required=False, label="Контрагент"
    )

    class Meta:
        model = Request
        fields = ("title", "counterparty", "comment_internal")
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Например: заказ канцтоваров"}),
            "comment_internal": forms.Textarea(
                attrs={"rows": 6, "placeholder": "Доп. условия, сроки, контакты..."}
            ),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Counterparty.objects.all()

        # менеджер видит только своих клиентов, если поле manager — FK на User
        if user and user.groups.filter(name="manager").exists() and not user.is_superuser:
            if _counterparty_manager_is_user_fk():
                qs = qs.filter(manager=user)

        self.fields["counterparty"].queryset = qs.order_by("name")


class RequestCreateForm(RequestForm):
    """
    Форма для создания заявки: добавляет поле для пакетного ввода позиций.
    Формат каждой строки: "Наименование ; Кол-во ; Примечание"
    Кол-во и примечание можно не указывать.
    """
    items_bulk = forms.CharField(
        label="Позиции (по строкам)",
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 6,
                "placeholder": "Карандаш HB; 100; упаковка\nРучка синяя; 50\nСкотч прозрачный; 10; 48мм",
            }
        ),
        help_text="Каждая строка: Наименование ; Кол-во ; Примечание. Кол-во и примечание можно не заполнять.",
    )


class RequestItemForm(forms.ModelForm):
    class Meta:
        model = RequestItem
        fields = ("title", "quantity", "note")  # product намеренно не показываем в UI
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Наименование (пишем от руки)"}),
            "quantity": forms.NumberInput(attrs={"step": "0.001", "min": "0", "placeholder": "Кол-во"}),
            "note": forms.TextInput(attrs={"placeholder": "Примечание (необязательно)"}),
        }

    def clean(self):
        data = super().clean()
        title = (data.get("title") or "").strip()
        if not title:
            self.add_error("title", "Введите наименование позиции")
        return data


class RequestItemEditForm(forms.ModelForm):
    """
    Инлайн-форма редактирования позиции в карточке заявки.
    """
    class Meta:
        model = RequestItem
        fields = ("title", "quantity", "note")
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Наименование"}),
            "quantity": forms.NumberInput(attrs={"step": "0.001", "min": "0"}),
            "note": forms.TextInput(attrs={"placeholder": "Примечание"}),
        }

class RequestQuoteForm(forms.ModelForm):
    class Meta:
        model = RequestQuote
        fields = ("file",)
        widgets = {
            "file": forms.ClearableFileInput(attrs={
                "accept": ".pdf,.doc,.docx,.xls,.xlsx,.ods,.odt,.rtf,.csv,.txt,image/*"
            })
        }

    def clean_file(self):
        f = self.cleaned_data["file"]
        # простейшая защита: до 20 МБ
        max_mb = 20
        if f.size > max_mb * 1024 * 1024:
            raise forms.ValidationError(f"Файл слишком большой (>{max_mb} МБ)")
        return f