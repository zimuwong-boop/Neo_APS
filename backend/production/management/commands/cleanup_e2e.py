"""Remove only the uniquely prefixed records created by the browser workflow test."""
import re

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from catalog.api import graph_lock
from catalog.models import BOM, BOMLine, Item
from production.models import (
    Allocation,
    Audit,
    Batch,
    Plan,
    ProductionOrder,
    Report,
    ReportAllocation,
)
from sales.models import SalesOrder


class Command(BaseCommand):
    help = "清理 E2E-CROSS-时间戳 测试数据；不接受通用前缀，不触碰 DEMO 数据。"

    def add_arguments(self, parser):
        parser.add_argument("--prefix", required=True)

    @transaction.atomic
    def handle(self, *args, **options):
        prefix = options["prefix"]
        if not re.fullmatch(r"E2E-CROSS-\d{13,16}", prefix):
            raise CommandError("仅允许浏览器测试的唯一时间戳前缀。")
        graph_lock()
        items = Item.objects.filter(code__startswith=prefix + "-")
        orders = ProductionOrder.objects.filter(item__in=items)
        batches = Batch.objects.filter(orders__in=orders).distinct()
        plans = Plan.objects.filter(batch__in=batches)
        plan_ids, batch_ids, order_ids = list(plans.values_list("pk", flat=True)), list(
            batches.values_list("pk", flat=True)
        ), list(orders.values_list("pk", flat=True))
        ReportAllocation.objects.filter(report__order_id__in=order_ids).delete()
        Report.objects.filter(order_id__in=order_ids, reversal_of__isnull=False).delete()
        Report.objects.filter(order_id__in=order_ids).delete()
        Allocation.objects.filter(order_id__in=order_ids).delete()
        ProductionOrder.objects.filter(pk__in=order_ids).delete()
        Batch.objects.filter(pk__in=batch_ids).delete()
        Plan.objects.filter(pk__in=plan_ids).delete()
        # Remove unpublished test plans too; inputs contain the unique source order IDs.
        sales = SalesOrder.objects.filter(number__startswith=prefix + "-")
        sale_ids = set(sales.values_list("pk", flat=True))
        for plan in Plan.objects.filter(status="DRAFT"):
            if set(plan.inputs.get("order_ids", [])) and set(plan.inputs["order_ids"]) <= sale_ids:
                plan.delete()
        sales.delete()
        BOMLine.objects.filter(bom__item__in=items).delete()
        BOM.objects.filter(item__in=items).delete()
        audit_ids = [str(value) for value in [*plan_ids, *batch_ids, *order_ids]]
        Audit.objects.filter(object_id__in=audit_ids).delete()
        items.delete()
        self.stdout.write("已清理该唯一前缀的浏览器测试数据；演示数据保留。")
