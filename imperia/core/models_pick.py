from django.db import models
from django.utils import timezone
from django.conf import settings
from .models_requests import Request

class PickItem(models.Model):
    request = models.ForeignKey(
        Request,
        on_delete=models.CASCADE,
        related_name="pick_items",
    )
    barcode  = models.CharField(max_length=64, blank=True, default="")
    name     = models.CharField(max_length=255, blank=True, default="")
    location = models.CharField(max_length=64, blank=True, default="")
    unit     = models.CharField(max_length=16, blank=True, default="")
    qty      = models.PositiveIntegerField(default=1)
    price    = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # >>> НОВОЕ: прогресс склада
    picked_qty = models.PositiveIntegerField(default=0)
    missing    = models.BooleanField(default=False)
    note       = models.CharField(max_length=255, blank=True, default="")

    # ВАЖНО: ставим default=timezone.now, а не auto_now_add/auto_now,
    # чтобы миграция не требовала одноразовый default для старых строк.
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["id"]

    def save(self, *args, **kwargs):
        self.updated_at = timezone.now()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name or self.barcode} x {self.qty}"


class PickResult(models.Model):
    request = models.OneToOneField(Request, on_delete=models.CASCADE, related_name="pick_result")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    is_final = models.BooleanField(default=False)  # отправлено «готов к отправке»

class PickResultItem(models.Model):
    result  = models.ForeignKey(PickResult, on_delete=models.CASCADE, related_name="lines")
    barcode = models.CharField(max_length=64, db_index=True)
    name    = models.CharField(max_length=512, blank=True)
    planned_qty = models.IntegerField(default=0)
    picked_qty  = models.IntegerField(default=0)
    missing     = models.BooleanField(default=False)
    note        = models.CharField(max_length=255, blank=True)