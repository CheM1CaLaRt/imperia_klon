from django.db import models
from django.utils import timezone
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
