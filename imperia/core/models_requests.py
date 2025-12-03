# core/models_requests.py
from django.conf import settings
from django.db import models
from django.utils import timezone
import os
from django.utils.translation import gettext_lazy as _


class RequestStatus(models.TextChoices):
    DRAFT            = "draft",            "Черновик"
    SUBMITTED        = "submitted",        "Отправлена"
    QUOTE            = "quote",            "Коммерческое предложение"
    PENDING_APPROVAL = "pending_approval", "На согласовании"
    APPROVED         = "approved",         "Согласована"
    REJECTED         = "rejected",         "Не согласована"
    TO_PICK          = "to_pick",          "Передана на сборку"
    IN_PROGRESS      = "in_progress",      "Собирается"
    READY_TO_SHIP    = "ready_to_ship",    "Готова к отгрузке"
    PARTIALLY_SHIPPED = "partially_shipped", "Частично отгружена"
    SHIPPED          = "shipped",          "Отгружена"
    DELIVERED        = "delivered",        "Доставлена"
    DONE             = "done",             "Завершена"
    CANCELED         = "canceled",         "Отменена"

class Request(models.Model):
    number = models.CharField("Номер", max_length=32, unique=True, blank=True)
    title = models.CharField("Тема", max_length=200)
    initiator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="requests_created", verbose_name="Инициатор"
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        null=True, blank=True, related_name="requests_assigned", verbose_name="Ответственный"
    )
    counterparty = models.ForeignKey(
        "core.Counterparty", on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name="Контрагент"
    )
    delivery_date = models.DateField("Дата доставки", null=True, blank=True)
    delivery_address = models.ForeignKey(
        "core.CounterpartyAddress", on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name="Адрес доставки",
        related_name="requests"
    )
    delivery_contact = models.ForeignKey(
        "core.CounterpartyContact", on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name="Контактное лицо",
        related_name="requests"
    )
    status = models.CharField("Статус", max_length=20, choices=RequestStatus.choices, default=RequestStatus.DRAFT)
    comment_internal = models.TextField("Внутренний комментарий", blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_paid = models.BooleanField("Оплачена", default=False)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Заявка"
        verbose_name_plural = "Заявки"

    def __str__(self):
        return f"#{self.number or self.pk} {self.title}"

    @property
    def is_editable(self) -> bool:
        """Можно ли редактировать заявку (только в начальных статусах)"""
        return self.status in {RequestStatus.DRAFT, RequestStatus.SUBMITTED, RequestStatus.REJECTED}
    
    @property
    def can_add_items(self) -> bool:
        """Могут ли оператор/директор добавлять товары (в любой момент кроме завершенных/отмененных)"""
        return self.status not in {RequestStatus.DONE, RequestStatus.CANCELED, RequestStatus.DELIVERED}
    
    @property
    def active_quote(self):
        """Активное коммерческое предложение"""
        return self.quotes.filter(is_active=True).first()
    
    def get_quote_total(self):
        """Итоговая сумма активного КП"""
        quote = self.active_quote
        if quote:
            return sum(item.total for item in quote.items.all())
        return 0
    
    def get_shipped_quantity(self, quote_item):
        """Получить количество отгруженного товара для позиции КП"""
        shipped = sum(
            si.quantity for si in RequestShipmentItem.objects.filter(
                shipment__request=self,
                quote_item=quote_item
            )
        )
        return shipped
    
    def is_fully_shipped(self):
        """Проверить, полностью ли отгружена заявка"""
        quote = self.active_quote
        if not quote:
            return False
        
        for quote_item in quote.items.all():
            shipped = self.get_shipped_quantity(quote_item)
            if shipped < quote_item.quantity:
                return False
        return True
    
    def get_shipped_total(self):
        """Итоговая сумма отгруженных товаров"""
        total = 0
        for shipment in self.shipments.all():
            for item in shipment.items.all():
                if item.price:
                    total += item.quantity * item.price
        return total

class RequestItem(models.Model):
    request = models.ForeignKey(Request, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("core.Product", on_delete=models.PROTECT, null=True, blank=True, verbose_name="Товар")
    title = models.CharField("Наименование", max_length=255, default="")  # ← добавили default
    quantity = models.DecimalField("Кол-во", max_digits=12, decimal_places=3, default=1)
    note = models.CharField("Примечание", max_length=200, blank=True, default="")

    def __str__(self):
        return self.title or (self.product.name if self.product_id else f"Позиция #{self.pk}")


class RequestComment(models.Model):
    request = models.ForeignKey(Request, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    text = models.TextField("Комментарий")
    created_at = models.DateTimeField(auto_now_add=True)

class RequestHistory(models.Model):
    request = models.ForeignKey(Request, on_delete=models.CASCADE, related_name="history")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    from_status = models.CharField(max_length=20, choices=RequestStatus.choices, blank=True, default="")
    to_status = models.CharField(max_length=20, choices=RequestStatus.choices)
    created_at = models.DateTimeField(default=timezone.now)
    note = models.CharField(max_length=255, blank=True, default="")

# --- Коммерческие предложения (вложения) ---


def _quote_upload_to(instance, filename: str):
    # created_at ещё нет до .save(), поэтому используем "сейчас" как безопасный дефолт
    dt = getattr(instance, "created_at", None) or timezone.now()
    # request_id уже есть (мы присваиваем q.request перед save)
    req_id = getattr(instance, "request_id", None) or "tmp"
    # чуть чистим имя файла
    name = os.path.basename(filename)
    return f"quotes/{dt:%Y/%m/%d}/{req_id}/{name}"

class RequestQuote(models.Model):
    request = models.ForeignKey(Request, on_delete=models.CASCADE, related_name="quotes")
    file = models.FileField("Файл КП", upload_to=_quote_upload_to, max_length=500, null=True, blank=True)
    original_name = models.CharField("Оригинальное имя", max_length=255, blank=True, default="")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="uploaded_quotes")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField("Активное КП", default=True, help_text="Только одно активное КП на заявку")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Коммерческое предложение"
        verbose_name_plural = "Коммерческие предложения"

    def __str__(self):
        return self.original_name or (self.file.name.split("/")[-1] if self.file else f"Quote #{self.pk}")

    def save(self, *args, **kwargs):
        # При создании нового активного КП, делаем все остальные неактивными
        if self.is_active and self.pk is None:
            RequestQuote.objects.filter(request=self.request, is_active=True).update(is_active=False)
        super().save(*args, **kwargs)


class RequestQuoteItem(models.Model):
    """Товар в коммерческом предложении с ценой"""
    quote = models.ForeignKey(RequestQuote, on_delete=models.CASCADE, related_name="items")
    request_item = models.ForeignKey(RequestItem, on_delete=models.CASCADE, related_name="quote_items")
    product = models.ForeignKey("core.Product", on_delete=models.PROTECT, null=True, blank=True, verbose_name="Товар")
    title = models.CharField("Наименование", max_length=255)
    quantity = models.DecimalField("Количество", max_digits=12, decimal_places=3)
    price = models.DecimalField("Цена за единицу", max_digits=12, decimal_places=2)
    total = models.DecimalField("Итого", max_digits=14, decimal_places=2)
    note = models.CharField("Примечание", max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "Позиция КП"
        verbose_name_plural = "Позиции КП"

    def __str__(self):
        return f"{self.title} - {self.quantity} x {self.price}"

    def save(self, *args, **kwargs):
        # Автоматически рассчитываем итого
        self.total = self.quantity * self.price
        super().save(*args, **kwargs)


class RequestShipment(models.Model):
    """Отгрузка заявки (может быть частичной или полной)"""
    request = models.ForeignKey(Request, on_delete=models.CASCADE, related_name="shipments")
    shipment_number = models.CharField("Номер отгрузки", max_length=32, blank=True)
    shipped_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="shipments_created")
    shipped_at = models.DateTimeField("Дата отгрузки", auto_now_add=True)
    is_partial = models.BooleanField("Частичная отгрузка", default=False)
    comment = models.TextField("Комментарий к отгрузке", blank=True, default="")
    
    class Meta:
        ordering = ["-shipped_at"]
        verbose_name = "Отгрузка"
        verbose_name_plural = "Отгрузки"

    def __str__(self):
        return f"Отгрузка #{self.shipment_number or self.pk} заявки {self.request}"


class RequestShipmentItem(models.Model):
    """Товар в отгрузке"""
    shipment = models.ForeignKey(RequestShipment, on_delete=models.CASCADE, related_name="items")
    quote_item = models.ForeignKey(RequestQuoteItem, on_delete=models.PROTECT, null=True, blank=True)
    product = models.ForeignKey("core.Product", on_delete=models.PROTECT, null=True, blank=True)
    title = models.CharField("Наименование", max_length=255)
    quantity = models.DecimalField("Количество", max_digits=12, decimal_places=3)
    price = models.DecimalField("Цена", max_digits=12, decimal_places=2, null=True, blank=True)
    
    class Meta:
        ordering = ["id"]
        verbose_name = "Позиция отгрузки"
        verbose_name_plural = "Позиции отгрузки"

    def __str__(self):
        return f"{self.title} - {self.quantity}"

