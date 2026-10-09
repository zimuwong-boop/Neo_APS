import uuid

from django.conf import settings
from django.db import models


class Plan(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120, default="排产方案")
    status = models.CharField(max_length=12, default="DRAFT")
    inputs = models.JSONField(default=dict)
    fingerprint = models.CharField(max_length=64)
    data = models.JSONField(default=dict)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class Batch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan = models.OneToOneField(Plan, on_delete=models.PROTECT, related_name="batch")
    created_at = models.DateTimeField(auto_now_add=True)


class ProductionOrder(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.CharField(max_length=64, unique=True)
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name="orders")
    task_key = models.CharField(max_length=64)
    item = models.ForeignKey("catalog.Item", on_delete=models.PROTECT, related_name="production_orders")
    bom = models.ForeignKey("catalog.BOM", on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=20, decimal_places=6)
    completed_qty = models.DecimalField(max_digits=20, decimal_places=6, default=0)
    demo_progress_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    priority = models.PositiveSmallIntegerField(default=3)
    sequence = models.PositiveIntegerField()
    status = models.CharField(max_length=12, default="DRAFT")
    snapshot = models.JSONField(default=dict)
    materials = models.JSONField(default=list)
    target_start = models.DateField(null=True, blank=True)
    target_end = models.DateField(null=True, blank=True)
    line_label = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["sequence", "number"]
        constraints = [
            models.CheckConstraint(condition=models.Q(demo_progress_percent__isnull=True)
                                   | (models.Q(demo_progress_percent__gte=0)
                                      & models.Q(demo_progress_percent__lt=100)),
                                   name="production_demo_progress_range"),
            models.UniqueConstraint(fields=["batch", "task_key"], name="production_task_unique"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="production_qty_positive"),
            models.CheckConstraint(condition=models.Q(completed_qty__gte=0)
                                   & models.Q(completed_qty__lte=models.F("quantity")),
                                   name="production_completed_range"),
            models.CheckConstraint(condition=models.Q(priority__gte=1, priority__lte=5),
                                   name="production_priority_range"),
        ]


class Allocation(models.Model):
    order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name="allocations")
    sales_line = models.ForeignKey("sales.SalesLine", on_delete=models.PROTECT, null=True,
                                  related_name="allocations")
    parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True, related_name="children")
    kind = models.CharField(max_length=12)
    path = models.TextField()
    quantity = models.DecimalField(max_digits=20, decimal_places=6)
    fulfilled_qty = models.DecimalField(max_digits=20, decimal_places=6, default=0)
    priority = models.PositiveSmallIntegerField()
    rank = models.PositiveIntegerField()
    source = models.JSONField(default=dict)

    class Meta:
        ordering = ["rank"]
        constraints = [
            models.UniqueConstraint(fields=["order", "rank"], name="allocation_rank_unique"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="allocation_qty_positive"),
            models.CheckConstraint(condition=models.Q(fulfilled_qty__gte=0)
                                   & models.Q(fulfilled_qty__lte=models.F("quantity")),
                                   name="allocation_fulfilled_range"),
        ]


class Report(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(ProductionOrder, on_delete=models.PROTECT, related_name="reports")
    quantity = models.DecimalField(max_digits=20, decimal_places=6)
    request_key = models.CharField(max_length=100, unique=True)
    reversal_of = models.OneToOneField("self", on_delete=models.PROTECT, null=True,
                                      related_name="reversal")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    batch_code = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]


class ReportAllocation(models.Model):
    report = models.ForeignKey(Report, on_delete=models.CASCADE, related_name="distributions")
    allocation = models.ForeignKey(Allocation, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=20, decimal_places=6)


class Audit(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True)
    operation = models.CharField(max_length=40)
    object_id = models.CharField(max_length=100)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
