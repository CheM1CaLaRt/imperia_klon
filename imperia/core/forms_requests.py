# core/forms_requests.py
from django import forms
from django.apps import apps
from django.conf import settings
from django.db.models import ForeignKey

from .models_requests import Request, RequestItem, RequestQuote, RequestQuoteItem, RequestShipment, RequestShipmentItem
from .models import Counterparty, CounterpartyAddress, CounterpartyContact
from django.forms import formset_factory


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
    delivery_address = forms.IntegerField(
        required=False, label="Адрес доставки", widget=forms.HiddenInput()
    )
    delivery_contact = forms.IntegerField(
        required=False, label="Контактное лицо", widget=forms.HiddenInput()
    )

    class Meta:
        model = Request
        fields = ("title", "counterparty", "delivery_date", "delivery_address", "delivery_contact", "comment_internal")
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Например: заказ канцтоваров"}),
            "delivery_date": forms.DateInput(attrs={"type": "date", "class": "date-input"}),
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
        
        # Инициализируем значения скрытых полей при редактировании
        if self.instance and self.instance.pk:
            if self.instance.delivery_address_id:
                self.fields["delivery_address"].initial = self.instance.delivery_address_id
            if self.instance.delivery_contact_id:
                self.fields["delivery_contact"].initial = self.instance.delivery_contact_id
    
    def clean_delivery_address(self):
        """Кастомная валидация адреса доставки - получаем объект по ID"""
        address_id = self.cleaned_data.get("delivery_address")
        if not address_id:
            return None
        
        counterparty_id = None
        if self.cleaned_data.get("counterparty"):
            counterparty_id = self.cleaned_data["counterparty"].id
        elif self.data and self.data.get("counterparty"):
            try:
                counterparty_id = int(self.data.get("counterparty"))
            except (ValueError, TypeError):
                pass
        
        try:
            address = CounterpartyAddress.objects.get(id=address_id)
            # Проверяем, что адрес принадлежит выбранному контрагенту
            if counterparty_id and address.counterparty_id != counterparty_id:
                raise forms.ValidationError("Выбранный адрес не принадлежит данному контрагенту.")
            return address
        except CounterpartyAddress.DoesNotExist:
            raise forms.ValidationError("Выбранный адрес не найден.")
    
    def clean_delivery_contact(self):
        """Кастомная валидация контакта - получаем объект по ID"""
        contact_id = self.cleaned_data.get("delivery_contact")
        if not contact_id:
            return None
        
        counterparty_id = None
        if self.cleaned_data.get("counterparty"):
            counterparty_id = self.cleaned_data["counterparty"].id
        elif self.data and self.data.get("counterparty"):
            try:
                counterparty_id = int(self.data.get("counterparty"))
            except (ValueError, TypeError):
                pass
        
        try:
            contact = CounterpartyContact.objects.get(id=contact_id)
            # Проверяем, что контакт принадлежит выбранному контрагенту
            if counterparty_id and contact.counterparty_id != counterparty_id:
                raise forms.ValidationError("Выбранный контакт не принадлежит данному контрагенту.")
            return contact
        except CounterpartyContact.DoesNotExist:
            raise forms.ValidationError("Выбранный контакт не найден.")
    
    def save(self, commit=True):
        """Сохраняем форму, правильно обрабатывая адрес и контакт"""
        instance = super().save(commit=False)
        
        # Получаем объекты из cleaned_data (clean методы вернули объекты)
        delivery_address = self.cleaned_data.get("delivery_address")
        delivery_contact = self.cleaned_data.get("delivery_contact")
        
        # Устанавливаем объекты напрямую
        if delivery_address:
            instance.delivery_address = delivery_address
        else:
            instance.delivery_address = None
            
        if delivery_contact:
            instance.delivery_contact = delivery_contact
        else:
            instance.delivery_contact = None
        
        if commit:
            instance.save()
        return instance


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


# Форма позиции заказа (для формсета, похожа на PickItemForm, но без цены)
class OrderItemForm(forms.Form):
    product_id = forms.IntegerField(required=False, widget=forms.HiddenInput())
    barcode = forms.CharField(
        required=False,
        label="Штрихкод",
        widget=forms.TextInput(attrs={
            "class": "order-barcode",
            "placeholder": "Штрихкод",
            "autocomplete": "off",
            "inputmode": "numeric",
        })
    )
    article = forms.CharField(
        required=False,
        label="Артикул",
        widget=forms.TextInput(attrs={
            "class": "order-article",
            "placeholder": "Артикул",
            "autocomplete": "off",
        })
    )
    name = forms.CharField(
        required=False,
        label="Название",
        widget=forms.TextInput(attrs={
            "class": "order-name",
            "placeholder": "Введите название (от 3 символов)",
            "autocomplete": "off",
        })
    )
    quantity = forms.DecimalField(
        required=False,
        min_value=0,
        max_digits=12,
        decimal_places=3,
        label="Кол-во",
        widget=forms.NumberInput(attrs={
            "class": "order-qty",
            "placeholder": "1",
            "step": "0.001",
        })
    )
    note = forms.CharField(
        required=False,
        label="Примечание",
        widget=forms.TextInput(attrs={
            "class": "order-note",
            "placeholder": "Примечание",
            "autocomplete": "off",
        })
    )
    DELETE = forms.BooleanField(required=False, label="Удалить")


OrderItemFormSet = formset_factory(OrderItemForm, extra=1, can_delete=True)


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
        f = self.cleaned_data.get("file")
        if f:
            # простейшая защита: до 20 МБ
            max_mb = 20
            if f.size > max_mb * 1024 * 1024:
                raise forms.ValidationError(f"Файл слишком большой (>{max_mb} МБ)")
        return f


class RequestQuoteItemForm(forms.Form):
    """Форма для позиции в коммерческом предложении"""
    request_item_id = forms.IntegerField(widget=forms.HiddenInput(), required=False)
    product_id = forms.IntegerField(required=False, widget=forms.HiddenInput())
    barcode = forms.CharField(
        required=False,
        label="Штрихкод",
        widget=forms.TextInput(attrs={
            "class": "input quote-barcode",
            "placeholder": "Штрихкод",
            "autocomplete": "off",
            "inputmode": "numeric",
        })
    )
    article = forms.CharField(
        required=False,
        label="Артикул",
        widget=forms.TextInput(attrs={
            "class": "input quote-article",
            "placeholder": "Артикул",
            "autocomplete": "off",
        })
    )
    title = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={
            "class": "input quote-product-name",
            "placeholder": "Наименование товара",
            "autocomplete": "off",
        })
    )
    quantity = forms.DecimalField(
        max_digits=12,
        decimal_places=3,
        widget=forms.NumberInput(attrs={
            "class": "input",
            "step": "0.001",
            "min": "0",
            "placeholder": "Количество",
        })
    )
    price = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=True,
        widget=forms.NumberInput(attrs={
            "class": "input",
            "step": "0.01",
            "min": "0",
            "placeholder": "Цена за единицу",
        })
    )
    markup_percent = forms.DecimalField(
        required=False,
        max_digits=8,
        decimal_places=2,
        widget=forms.NumberInput(attrs={
            "class": "input",
            "step": "0.1",
            "placeholder": "Наценка %",
        })
    )
    note = forms.CharField(
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs={
            "class": "input",
            "placeholder": "Примечание",
        })
    )
    DELETE = forms.BooleanField(required=False, widget=forms.HiddenInput())

    def clean_price(self):
        price = self.cleaned_data.get("price")
        if price is not None and price < 0:
            raise forms.ValidationError("Цена не может быть отрицательной")
        return price

    def clean_quantity(self):
        quantity = self.cleaned_data.get("quantity")
        if quantity is not None and quantity <= 0:
            raise forms.ValidationError("Количество должно быть больше нуля")
        return quantity


RequestQuoteItemFormSet = formset_factory(RequestQuoteItemForm, extra=0, can_delete=True)


class RequestShipmentItemForm(forms.Form):
    """Форма для позиции в отгрузке"""
    quote_item_id = forms.IntegerField(required=False, widget=forms.HiddenInput())
    product_id = forms.IntegerField(required=False, widget=forms.HiddenInput())
    title = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={
            "class": "input",
            "placeholder": "Наименование",
            "readonly": True,
            "style": "background-color: #f5f5f5;",
        })
    )
    quantity_available = forms.DecimalField(
        required=False,
        widget=forms.HiddenInput()
    )
    quantity = forms.DecimalField(
        max_digits=12,
        decimal_places=3,
        widget=forms.NumberInput(attrs={
            "class": "input",
            "step": "0.001",
            "min": "0",
            "placeholder": "Количество",
        })
    )
    price = forms.DecimalField(
        required=False,
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(attrs={
            "class": "input",
            "step": "0.01",
            "min": "0",
            "readonly": True,
            "style": "background-color: #f5f5f5;",
        })
    )

    def clean_quantity(self):
        quantity = self.cleaned_data.get("quantity")
        available = self.cleaned_data.get("quantity_available")
        if quantity and quantity <= 0:
            raise forms.ValidationError("Количество должно быть больше нуля")
        if quantity and available and quantity > available:
            raise forms.ValidationError(f"Можно отгрузить не более {available}")
        return quantity


RequestShipmentItemFormSet = formset_factory(RequestShipmentItemForm, extra=0, can_delete=True)