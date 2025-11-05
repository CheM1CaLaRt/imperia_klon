# core/forms_pick.py
from django import forms
from django.forms import formset_factory

class PickItemForm(forms.Form):
    barcode  = forms.CharField(
        required=False,
        label="Штрихкод",
        widget=forms.TextInput(attrs={
            "class": "pick-barcode w-full",
            "placeholder": "Штрихкод",
            "autocomplete": "off",
            "inputmode": "numeric",
        })
    )
    name     = forms.CharField(
        required=False,
        label="Название",
        widget=forms.TextInput(attrs={
            "class": "w-full",
            "placeholder": "Название",
            "autocomplete": "off",
        })
    )
    location = forms.CharField(
        required=False,
        label="Ячейка",
        widget=forms.TextInput(attrs={
            "class": "w-full",
            "placeholder": "Ячейка",
            "autocomplete": "off",
        })
    )
    unit     = forms.CharField(
        required=False,
        label="Ед.",
        widget=forms.TextInput(attrs={
            "class": "w-full",
            "placeholder": "Ед.",
            "autocomplete": "off",
        })
    )
    qty      = forms.IntegerField(
        required=False,
        min_value=1,
        label="Кол-во",
        widget=forms.NumberInput(attrs={
            "class": "w-full",
            "placeholder": "1",
        })
    )
    DELETE   = forms.BooleanField(required=False, label="Удалить")

PickItemFormSet = formset_factory(PickItemForm, extra=1, can_delete=True)
