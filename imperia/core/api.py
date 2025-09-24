# core/api.py
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.contrib.auth.models import Group
from core.models import Product

def user_in_group(user, group_name: str) -> bool:
    return user.is_authenticated and user.groups.filter(name=group_name).exists()

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def product_by_barcode(request, barcode: str):
    if not (user_in_group(request.user, "warehouse") or user_in_group(request.user, "director")):
        return Response({"detail": "Недостаточно прав"}, status=403)

    bc = "".join(ch for ch in barcode if ch.isdigit())
    try:
        p = Product.objects.select_related("supplier").prefetch_related("images", "certificates", "prices").get(barcode=bc)
    except Product.DoesNotExist:
        return Response({"detail": "Не найдено"}, status=404)

    return Response({
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "description_ext": p.description_ext,
        "barcode": p.barcode,
        "supplier": p.supplier.code,
        "sku": p.sku,
        "brand": p.brand,
        "vendor_code": p.vendor_code,
        "manufacturer_country": p.manufacturer_country,
        "dimensions_cm": {"h": p.pkg_height_cm, "w": p.pkg_width_cm, "d": p.pkg_depth_cm},
        "weight_kg": p.weight_kg,
        "volume_m3": p.volume_m3,
        "images": [img.url for img in p.images.all()],
        "certificates": [
            {"issued_by": c.issued_by, "name": c.name, "active_to": c.active_to, "url": c.url}
            for c in p.certificates.all()
        ],
        "prices": [
            {"type": pr.price_type, "value": str(pr.value), "currency": pr.currency}
            for pr in p.prices.all()
        ],
    })
