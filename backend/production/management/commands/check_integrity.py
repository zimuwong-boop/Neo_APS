import json
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count

from catalog.api import graph_lock
from catalog.models import BOM, Item
from production.engine import ACTIVE_STATES
from production.models import ProductionOrder, Report
from sales.models import SalesOrder


class Command(BaseCommand):
    help = "只读检查生产/回分/报工数量守恒，并输出演示数据统计。"

    @transaction.atomic
    def handle(self, *args, **options):
        graph_lock()
        errors = []
        for order in ProductionOrder.objects.prefetch_related("allocations", "reports"):
            planned = sum((entry.quantity for entry in order.allocations.all()), Decimal(0))
            fulfilled = sum((entry.fulfilled_qty for entry in order.allocations.all()), Decimal(0))
            reported = sum((entry.quantity for entry in order.reports.all()), Decimal(0))
            if planned != order.quantity or fulfilled != order.completed_qty or reported != fulfilled:
                errors.append(order.number)
            for allocation in order.allocations.select_related("parent", "sales_line"):
                if allocation.kind == "SALE" and (allocation.parent_id is not None
                    or allocation.sales_line_id is None or allocation.sales_line.item_id != order.item_id):
                    errors.append(f"allocation:{allocation.pk}")
                if allocation.kind == "COMPONENT" and (allocation.parent_id is None
                    or allocation.parent.order.batch_id != order.batch_id
                    or allocation.parent.sales_line_id != allocation.sales_line_id):
                    errors.append(f"allocation:{allocation.pk}")
                if allocation.kind == "STANDALONE" and (allocation.parent_id or allocation.sales_line_id):
                    errors.append(f"allocation:{allocation.pk}")
        for report in Report.objects.prefetch_related("distributions"):
            if report.quantity != sum((entry.quantity for entry in report.distributions.all()), Decimal(0)):
                errors.append(f"report:{report.pk}")
        for sale in SalesOrder.objects.prefetch_related("lines"):
            for line in sale.lines.all():
                active = line.allocations.filter(kind="SALE", order__status__in=ACTIVE_STATES)
                if sum((entry.quantity for entry in active), Decimal(0)) > line.quantity:
                    errors.append(sale.number)
        if errors:
            raise CommandError("数量或引用不一致：" + ", ".join(errors))
        summary = {
            "integrity": "ok", "demo_items": Item.objects.filter(code__startswith="DEMO-").count(),
            "demo_boms": BOM.objects.filter(item__code__startswith="DEMO-").count(),
            "demo_sales": SalesOrder.objects.filter(number__startswith="DEMO-").count(),
            "production_states": {row["status"]: row["count"] for row in ProductionOrder.objects.values(
                "status"
            ).annotate(count=Count("pk"))},
        }
        self.stdout.write(json.dumps(summary, ensure_ascii=False))
