from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from catalog.models import BOM, BOMLine, Item
from production.models import Audit, Report
from production.services import (
    Conflict,
    adjust_demo_progress,
    cancel_component,
    cancel_preview,
    materialize,
    new_plan,
    report,
    reverse_report,
    transition,
)
from sales.models import SalesLine, SalesOrder


@pytest.fixture
def scenario(db):
    user = get_user_model().objects.create_user(username="demo-operator")
    client = APIClient()
    client.force_authenticate(user)
    raw = Item.objects.create(code="DEMO-TEST-R", name="原料", kind="RAW", unit="kg")
    product = Item.objects.create(code="DEMO-TEST-P", name="调味酱", kind="SEMI", unit="kg")
    bom = BOM.objects.create(item=product, output_qty=1, status="ACTIVE")
    BOMLine.objects.create(bom=bom, component=raw, quantity=2)
    sales = []
    for number, amount, priority in [("DEMO-TEST-A", 5, 1), ("DEMO-TEST-B", 4, 3)]:
        sale = SalesOrder.objects.create(number=number, customer_name="演示", priority=priority,
                                        status="CONFIRMED")
        SalesLine.objects.create(order=sale, item=product, quantity=amount)
        sales.append(sale)
    order = materialize(new_plan({"order_ids": [sale.pk for sale in sales]})).orders.get()
    return client, user, order, sales


def progress(client, sale):
    return client.get(f"/api/v1/sales-orders/{sale.pk}/").data["progress"][0]


def test_demo_overlay_reaches_sales_without_changing_ledger(scenario):
    client, user, order, sales = scenario
    result = client.post(f"/api/v1/production-orders/{order.pk}/demo-progress/",
                         {"percent": "70.00", "revision": order.revision}, format="json")
    assert result.status_code == 200 and result.data["display_progress_percent"] == 70
    order.refresh_from_db()
    assert order.completed_qty == 0 and order.status == "RELEASED" and order.started_at is None
    assert sum(entry.fulfilled_qty for entry in order.allocations.all()) == 0
    assert Report.objects.count() == 0 and Audit.objects.filter(operation="DEMO_PROGRESS").exists()
    first, second = [progress(client, sale) for sale in sales]
    assert first["display_percent"] == 99.99 and second["display_percent"] == 32.5
    assert first["percent"] == second["percent"] == 0
    assert first["fulfilled"] == "0.000000" and first["remaining"] == "0.000000"
    for sale in sales:
        sale.refresh_from_db()
        assert sale.status == "CONFIRMED"
    adjust_demo_progress(order, None, user)
    assert all(progress(client, sale)["display_percent"] == 0 for sale in sales)


@pytest.mark.parametrize("value", ["100", "100.00", "101", "-1", "99.999", "NaN", "Infinity"])
def test_manual_completion_and_invalid_progress_rejected(scenario, value):
    client, _, order, _ = scenario
    assert client.post(f"/api/v1/production-orders/{order.pk}/demo-progress/",
                       {"percent": value}, format="json").status_code == 400
    order.refresh_from_db()
    assert order.demo_progress_percent is None and order.completed_qty == 0


def test_real_reporting_completes_and_clears_overlay(scenario):
    client, user, order, sales = scenario
    adjust_demo_progress(order, Decimal("99.99"), user)
    transition(order, "start", user)
    report(order, {"quantity": 1, "request_key": "demo-partial"}, user)
    order.refresh_from_db()
    assert order.demo_progress_percent is None and order.completed_qty == 1
    assert progress(client, sales[0])["display_percent"] == 20
    assert client.post(f"/api/v1/production-orders/{order.pk}/demo-progress/",
                       {"percent": 1}, format="json").status_code == 400
    adjust_demo_progress(order, Decimal(90), user)
    report(order, {"quantity": 8, "request_key": "demo-complete"}, user)
    order.refresh_from_db()
    assert order.completed_qty == 9 and order.status == "COMPLETED"
    assert order.demo_progress_percent is None
    assert all(progress(client, sale)["display_percent"] == 100 for sale in sales)
    assert client.post(f"/api/v1/production-orders/{order.pk}/demo-progress/",
                       {"percent": 99}, format="json").status_code == 400


def test_real_reversal_clears_demo_and_keeps_actual_sales(scenario):
    client, user, order, sales = scenario
    transition(order, "start", user)
    entry = report(order, {"quantity": 1, "request_key": "undo-demo"}, user)
    adjust_demo_progress(order, Decimal(80), user)
    reverse_report(entry, "undo-demo-reverse", user)
    order.refresh_from_db()
    assert order.demo_progress_percent is None and order.completed_qty == 0
    assert order.status == "PAUSED" and progress(client, sales[0])["display_percent"] == 0


def test_stale_revision_and_database_constraint(scenario):
    client, _, order, _ = scenario
    assert client.post(f"/api/v1/production-orders/{order.pk}/demo-progress/",
                       {"percent": 50, "revision": 999}, format="json").status_code == 409
    with pytest.raises(IntegrityError), transaction.atomic():
        type(order).objects.filter(pk=order.pk).update(demo_progress_percent=100)


def test_demo_cancel_does_not_leave_sales_overlay(scenario):
    client, user, order, sales = scenario
    adjust_demo_progress(order, Decimal(50), user)
    cancel_component(order, cancel_preview(order)["token"], user)
    order.refresh_from_db()
    assert order.demo_progress_percent is None and order.status == "CANCELLED"
    assert progress(client, sales[0])["display_percent"] == 0
    assert progress(client, sales[0])["remaining"] == "5.000000"


def test_component_preview_never_completes_parent_sale_or_unlocks_it(scenario):
    client, user, _, _ = scenario
    semi = Item.objects.get(code="DEMO-TEST-P")
    finished = Item.objects.create(code="DEMO-TEST-F", name="袋装调味酱", kind="FINISHED", unit="袋")
    bom = BOM.objects.create(item=finished, output_qty=1, status="ACTIVE")
    BOMLine.objects.create(bom=bom, component=semi, quantity=1)
    sale = SalesOrder.objects.create(number="DEMO-TEST-F-SALE", customer_name="成品客户", status="CONFIRMED")
    SalesLine.objects.create(order=sale, item=finished, quantity=5)
    batch = materialize(new_plan({"order_ids": [sale.pk]}))
    parent, child = batch.orders.get(item=finished), batch.orders.get(item=semi)
    adjust_demo_progress(child, Decimal("99.99"), user)
    assert progress(client, sale)["display_percent"] == 0
    with pytest.raises(Conflict):
        transition(parent, "start", user)
