from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class SalesOrder(models.Model):
    number = models.CharField(max_length=64, unique=True)
    customer_name = models.CharField(max_length=200)
    priority = models.PositiveSmallIntegerField(
        default=3, validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, default="DRAFT", choices=[
        ("DRAFT", "草稿"), ("CONFIRMED", "已确认"),
        ("COMPLETED", "已完成"), ("CANCELLED", "已取消"),
    ])
    created_at = models.DateTimeField(auto_now_add=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["priority", "created_at", "pk"]
        constraints = [models.CheckConstraint(
            condition=models.Q(priority__gte=1, priority__lte=5), name="sales_priority_range"
        )]


class SalesLine(models.Model):
    order = models.ForeignKey(SalesOrder, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("catalog.Item", on_delete=models.PROTECT, related_name="sales_lines")
    quantity = models.DecimalField(max_digits=20, decimal_places=6,
                                  validators=[MinValueValidator(Decimal("0.000001"))])

    class Meta:
        constraints = [models.CheckConstraint(
            condition=models.Q(quantity__gt=0), name="sales_quantity_positive"
        )]
