from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models


class Item(models.Model):
    class Kind(models.TextChoices):
        FINISHED = "FINISHED", "成品"
        SEMI = "SEMI", "半成品"
        RAW = "RAW", "原料"

    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=8, choices=Kind.choices)
    unit = models.CharField(max_length=32)
    traceability_info = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)
    specification = models.CharField(max_length=120, blank=True)
    product_standard = models.CharField(max_length=120, blank=True)
    allergens = models.CharField(max_length=200, blank=True)
    shelf_life_days = models.PositiveIntegerField(null=True, blank=True)
    storage_condition = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["code"]


class BOM(models.Model):
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="boms")
    version = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    revision = models.PositiveIntegerField(default=1)
    output_qty = models.DecimalField(
        max_digits=20, decimal_places=6, validators=[MinValueValidator(Decimal("0.000001"))]
    )
    status = models.CharField(
        max_length=8, choices=[("DRAFT", "草稿"), ("ACTIVE", "生效"), ("RETIRED", "停用")],
        default="DRAFT",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["item", "version"], name="bom_item_version"),
            models.UniqueConstraint(fields=["item"], condition=models.Q(status="ACTIVE"),
                                    name="bom_one_active"),
            models.CheckConstraint(condition=models.Q(output_qty__gt=0), name="bom_output_positive"),
        ]


class BOMLine(models.Model):
    bom = models.ForeignKey(BOM, on_delete=models.CASCADE, related_name="lines")
    component = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="bom_usages")
    quantity = models.DecimalField(
        max_digits=20, decimal_places=6, validators=[MinValueValidator(Decimal("0.000001"))]
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["bom", "component"], name="bom_component_unique"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="bom_qty_positive"),
        ]
