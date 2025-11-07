from django.db import models
from django.conf import settings

class PickList(models.Model):
    """
    Лист на сборку по заявке.
    """
    request = models.ForeignKey("core.Request", on_delete=models.CASCADE, related_name="picklists")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    comment = models.TextField(blank=True, default="")
    # draft -> submitted -> picking -> picked -> shipped
    status = models.CharField(max_length=20, default="submitted")

    class Meta:
        ordering = ("-created_at",)

class PickItem(models.Model):
    """
    Строка листа сборки. Цена отсутствует (склад не видит).
    """
    picklist = models.ForeignKey(PickList, on_delete=models.CASCADE, related_name="items")
    product_id = models.PositiveIntegerField()                 # id Product (без жёсткой FK)
    barcode = models.CharField(max_length=64, db_index=True)
    name = models.CharField(max_length=255)
    location = models.CharField(max_length=64, default="", blank=True)  # код ячейки на момент брони
    unit = models.CharField(max_length=16, default="шт")
    qty = models.DecimalField(max_digits=12, decimal_places=3)
    price = models.DecimalField(
        max_digits=12, decimal_places=2,
        null=True, blank=True, verbose_name="Цена"
    )

    class Meta:
        indexes = [models.Index(fields=["barcode"])]
