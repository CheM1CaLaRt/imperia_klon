# core/models_requests.py
from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

class RequestStatus(models.TextChoices):
    DRAFT = "draft", _("Черновик")
    SUBMITTED = "submitted", _("Отправлена")             # менеджер/оператор создал(а)
    APPROVED = "approved", _("Согласована")              # оператор подтвердил
    TO_PICK = "to_pick", _("В сборку (на склад)")        # передана складу
    IN_PROGRESS = "in_progress", _("Собирается")         # склад собирает
    READY_TO_SHIP = "ready_to_ship", _("Готова к отгрузке")
    DONE = "done", _("Завершена")
    REJECTED = "rejected", _("Отклонена")
    CANCELED = "canceled", _("Отменена")

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
