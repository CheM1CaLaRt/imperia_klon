# core/models_requests.py
from django.conf import settings
from django.db import models
from django.utils import timezone
import os
from django.utils.translation import gettext_lazy as _


class RequestStatus(models.TextChoices):
    DRAFT         = "draft",         "Черновик"
    SUBMITTED     = "submitted",     "Отправлена"
    QUOTE         = "quote",         "Коммерческое предложение"
    APPROVED      = "approved",      "Согласована"
    REJECTED      = "rejected",      "Не согласована"           # ← ДОБАВЛЕНО
    TO_PICK       = "to_pick",       "Передана на склад"
    IN_PROGRESS   = "in_progress",   "Собирается"
    READY_TO_SHIP = "ready_to_ship", "Готова к отгрузке"
    DELIVERED     = "delivered",     "Доставлена"
    DONE          = "done",          "Завершена"
    CANCELED      = "canceled",      "Отменена"

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
        return self.status in {RequestStatus.DRAFT, RequestStatus.SUBMITTED, RequestStatus.REJECTED}

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
    file = models.FileField("Файл КП", upload_to=_quote_upload_to, max_length=500)
    original_name = models.CharField("Оригинальное имя", max_length=255, blank=True, default="")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="uploaded_quotes")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.original_name or (self.file.name.split("/")[-1] if self.file else f"Quote #{self.pk}")

