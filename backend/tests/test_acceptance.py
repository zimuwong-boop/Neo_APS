from decimal import Decimal
from time import perf_counter

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from catalog.models import BOM, BOMLine, Item
from production.engine import generate, qty
from production.models import Allocation, Audit, ProductionOrder, Report, ReportAllocation
from production.services import materialize, new_plan, report, reverse_report, transition
from sales.models import SalesLine, SalesOrder


@pytest.fixture
def api(db):
    user = get_user_model().objects.create_user(username="admin")
    client = APIClient()
    client.force_authenticate(user)
    return client, user


def item(code, kind):
    return Item.objects.create(code=code, name=code, kind=kind, unit="kg")


def bom(parent, *parts):
    instance = BOM.objects.create(item=parent, output_qty=1, status="ACTIVE")
    BOMLine.objects.bulk_create([BOMLine(bom=instance, component=child, quantity=amount)
                                for child, amount in parts])
    return instance


def sale(number, product, amount, priority):
    order = SalesOrder.objects.create(number=number, customer_name="测试", priority=priority,
                                     status="CONFIRMED")
    SalesLine.objects.create(order=order, item=product, quantity=amount)
    return order


def test_priority_same_product_and_sale_progress(api):
    client, actor = api
    raw, product, other = item("R", "RAW"), item("P", "FINISHED"), item("Q", "FINISHED")
    bom(product, (raw, 2)); bom(other, (raw, 1))
    first, second, third = sale("A", product, 5, 1), sale("B", product, 4, 3), sale("C", other, 2, 2)
    plan = new_plan({"order_ids": [first.pk, second.pk, third.pk]})
    assert [(row["item"]["code"], Decimal(row["quantity"]), row["priority"])
            for row in plan.data["tasks"]] == [("P", 9, 1), ("Q", 2, 2)]
    batch = materialize(plan)
    order = batch.orders.get(item=product)
    transition(order, "start", actor)
    report(order, {"quantity": 6, "request_key": "same-product"}, actor)
    first.refresh_from_db(); second.refresh_from_db()
    assert first.status == "COMPLETED" and second.status == "CONFIRMED"
    assert client.get(f"/api/v1/sales-orders/{second.pk}/").data["progress"][0]["percent"] == 25
    assert sum(Decimal(row["quantity"]) for row in plan.data["materials"]) == 20


def test_diamond_paths_and_multiple_consumers(api):
    shared, left, right, root, raw = [item(code, kind) for code, kind in [
        ("S", "SEMI"), ("L", "SEMI"), ("R", "SEMI"), ("F", "FINISHED"), ("RAW", "RAW")
    ]]
    bom(shared, (raw, 1)); bom(left, (shared, 2)); bom(right, (shared, 3))
    bom(root, (left, 1), (right, 1))
    plan = new_plan({"standalone": [{"item": root.pk, "quantity": "1"}]})
    shared_task = next(row for row in plan.data["tasks"] if row["item"]["id"] == shared.pk)
    assert Decimal(shared_task["quantity"]) == 5
    assert len([edge for edge in plan.data["dependencies"] if edge[0] == shared_task["key"]]) == 2
    assert len([entry for entry in plan.data["allocations"] if entry["task"] == shared_task["key"]]) == 2
    assert Decimal(plan.data["materials"][0]["quantity"]) == 5


def test_deep_full_expansion_without_recursion(api):
    raw = item("DEEP-RAW", "RAW")
    nodes = Item.objects.bulk_create([Item(code=f"S-{index}", name="半成品", kind="SEMI", unit="kg")
                                     for index in range(1050)])
    boms = BOM.objects.bulk_create([BOM(item=node, output_qty=1, status="ACTIVE") for node in nodes])
    BOMLine.objects.bulk_create([BOMLine(bom=instance, component=nodes[index + 1]
                                        if index + 1 < len(nodes) else raw, quantity=1)
                                for index, instance in enumerate(boms)])
    data, _ = generate({"standalone": [{"item": nodes[0].pk, "quantity": "1"}]})
    assert len(data["tasks"]) == 1050
    assert Decimal(data["materials"][0]["quantity"]) == 1


@pytest.mark.parametrize("bad", ["0", "-1", "NaN", "Infinity", "0.0000001", "100000000000000"])
def test_numeric_limits(bad):
    with pytest.raises(ValidationError):
        qty(bad)


def test_publish_failure_rolls_back_entire_graph(api, monkeypatch):
    product, raw = item("P", "FINISHED"), item("R", "RAW")
    bom(product, (raw, 1))
    plan = new_plan({"standalone": [{"item": product.pk, "quantity": "2"}]})
    def fail(*args, **kwargs):
        raise RuntimeError("simulated allocation failure")
    monkeypatch.setattr(Allocation.objects, "create", fail)
    with pytest.raises(RuntimeError):
        materialize(plan)
    assert ProductionOrder.objects.count() == 0
    plan.refresh_from_db()
    assert plan.status == "DRAFT" and not hasattr(plan, "batch")


def test_report_failure_rolls_back_all_ledgers(api, monkeypatch):
    actor = api[1]
    product, raw = item("P", "FINISHED"), item("R", "RAW")
    bom(product, (raw, 1))
    order = materialize(new_plan({"standalone": [{"item": product.pk, "quantity": "2"}]})).orders.get()
    transition(order, "start", actor)
    def fail(*args, **kwargs):
        raise RuntimeError("simulated ledger failure")
    monkeypatch.setattr(ReportAllocation.objects, "create", fail)
    with pytest.raises(RuntimeError):
        report(order, {"quantity": 1, "request_key": "rollback"}, actor)
    order.refresh_from_db()
    assert order.completed_qty == 0 and Report.objects.count() == 0
    assert order.allocations.get().fulfilled_qty == 0


def test_api_state_revision_and_plan_stale(api):
    client, _ = api
    product, raw = item("P", "FINISHED"), item("R", "RAW")
    bom(product, (raw, 1))
    sale_order = sale("A", product, 2, 3)
    response = client.post("/api/v1/plans/", {"inputs": {"order_ids": [sale_order.pk]}}, format="json")
    assert response.status_code == 201
    url = f'/api/v1/plans/{response.data["id"]}/'
    assert client.patch(url, {"revision": 999, "name": "冲突"}, format="json").status_code == 409
    assert client.patch(f"/api/v1/sales-orders/{sale_order.pk}/", {"priority": 1}, format="json").status_code == 200
    assert client.post(url + "publish/").status_code == 409
    assert client.delete(url).status_code == 204
    draft = client.post("/api/v1/production-orders/", {"item": product.pk, "quantity": "2"}, format="json")
    root_url = f'/api/v1/production-orders/{draft.data["id"]}/'
    assert client.post(root_url + "publish/").status_code == 200
    assert client.post(root_url + "start/", {"revision": 999}, format="json").status_code == 409
    assert client.post(root_url + "start/").status_code == 200
    assert client.post(root_url + "pause/").status_code == 200
    assert client.post(root_url + "resume/").status_code == 200
    assert client.post(root_url + "report/", {"quantity": "2", "request_key": "api-report"}, format="json").status_code == 200


def test_reverse_latest_only_and_exact_allocations(api):
    actor = api[1]
    product, raw = item("P", "FINISHED"), item("R", "RAW")
    bom(product, (raw, 1))
    first, second = sale("A", product, 5, 1), sale("B", product, 4, 3)
    root = materialize(new_plan({"order_ids": [first.pk, second.pk]})).orders.get()
    transition(root, "start", actor)
    older = report(root, {"quantity": 3, "request_key": "old"}, actor)
    latest = report(root, {"quantity": 3, "request_key": "latest"}, actor)
    with pytest.raises(ValidationError):
        reverse_report(older, "bad-old", actor)
    reverse_report(latest, "undo-latest", actor)
    assert sum(entry.fulfilled_qty for entry in root.allocations.all()) == 3
    assert root.allocations.get(sales_line__order=second).fulfilled_qty == 0
    with pytest.raises(ValidationError):
        reverse_report(latest, "duplicate", actor)
    assert reverse_report(latest, "undo-latest", actor).quantity == -3


def test_seed_is_repeat_safe_and_simulated(api):
    call_command("seed_demo")
    counts = (Item.objects.count(), SalesOrder.objects.count(), ProductionOrder.objects.count(), Report.objects.count())
    assert counts[:2] == (20, 9)
    call_command("seed_demo")
    assert counts == (Item.objects.count(), SalesOrder.objects.count(), ProductionOrder.objects.count(), Report.objects.count())
    direct = SalesOrder.objects.get(number="DEMO-SO-006")
    assert direct.lines.get().allocations.get(kind="SALE").fulfilled_qty == 20
    assert Item.objects.get(code="DEMO-LIQ500").allergens == "大豆、小麦"
    assert Audit.objects.filter(operation="DEMO_SEED").count() == 1


def test_100_lines_5000_nodes_performance(api):
    raw = item("PERF-RAW", "RAW")
    nodes = Item.objects.bulk_create([Item(code=f"PERF-{index}", name="基料", kind="SEMI", unit="kg")
                                     for index in range(50)])
    boms = BOM.objects.bulk_create([BOM(item=node, output_qty=1, status="ACTIVE") for node in nodes])
    BOMLine.objects.bulk_create([BOMLine(bom=instance, component=nodes[index + 1]
                                        if index < 49 else raw, quantity=1)
                                for index, instance in enumerate(boms)])
    order = SalesOrder.objects.create(number="PERF-SO", customer_name="性能测试", status="CONFIRMED")
    SalesLine.objects.bulk_create([SalesLine(order=order, item=nodes[0], quantity=1) for _ in range(100)])
    with CaptureQueriesContext(connection) as queries:
        started = perf_counter()
        data, _ = generate({"order_ids": [order.pk]})
        elapsed = perf_counter() - started
    assert len(data["allocations"]) == 5000 and len(data["tasks"]) == 50
    assert len(queries) <= 12 and elapsed < 5
    print(f"BENCHMARK: lines=100 nodes=5000 tasks=50 seconds={elapsed:.4f} sql={len(queries)}")


def test_global_queue_manual_review_new_batch_and_started_preserved(api):
    client, actor = api
    product, raw = item("QUEUE-P", "FINISHED"), item("QUEUE-R", "RAW")
    bom(product, (raw, 1))
    low = materialize(new_plan({"standalone": [{"item": product.pk, "quantity": "1", "priority": 5}]})).orders.get()
    high = materialize(new_plan({"standalone": [{"item": product.pk, "quantity": "1", "priority": 1}]})).orders.get()
    assert low.batch_id != high.batch_id
    queue = client.get("/api/v1/production-orders/queue/").data
    assert queue["suggested"] == [str(high.pk), str(low.pk)]
    assert client.post("/api/v1/production-orders/queue/", {
        "token": "outdated", "order_ids": queue["suggested"], "reason": "调整"
    }, format="json").status_code == 409
    manual = [str(low.pk), str(high.pk)]
    result = client.post("/api/v1/production-orders/queue/", {
        "token": queue["token"], "order_ids": manual, "reason": "人工决定先执行原批次"
    }, format="json")
    assert result.status_code == 200 and result.data["current"] == manual
    assert Audit.objects.filter(operation="QUEUE_ADJUST").exists()
    transition(high, "start", actor)
    high.refresh_from_db()
    saved_sequence = high.sequence
    current = client.get("/api/v1/production-orders/queue/").data
    assert current["suggested"] == [str(low.pk)]
    client.post("/api/v1/production-orders/queue/", {
        "token": current["token"], "order_ids": current["suggested"], "reason": "复核待开工"
    }, format="json")
    high.refresh_from_db()
    assert high.sequence == saved_sequence and high.status == "IN_PROGRESS"


def test_bom_clone_versions_and_traceability_frozen(api):
    client, _ = api
    product, raw = item("VERSION-P", "FINISHED"), item("VERSION-R", "RAW")
    original = bom(product, (raw, 1))
    old = materialize(new_plan({"standalone": [{"item": product.pk, "quantity": "1"}]})).orders.get()
    result = client.post(f"/api/v1/boms/{original.pk}/clone/")
    assert result.status_code == 201 and result.data["version"] == 2
    assert client.post(f'/api/v1/boms/{result.data["id"]}/publish/').status_code == 200
    newer = materialize(new_plan({"standalone": [{"item": product.pk, "quantity": "1"}]})).orders.get()
    assert newer.task_key != old.task_key
    assert old.snapshot["bom"]["version"] == 1 and newer.snapshot["bom"]["version"] == 2
    assert client.patch(f"/api/v1/items/{raw.pk}/", {"traceability_info": "模拟新批次"}, format="json").status_code == 200
    fresh = materialize(new_plan({"standalone": [{"item": product.pk, "quantity": "1"}]})).orders.get()
    assert fresh.task_key != newer.task_key
    old.refresh_from_db()
    assert old.snapshot["components"][0]["item"]["traceability_info"] == ""
    assert client.patch(f"/api/v1/items/{raw.pk}/", {"revision": 999, "name": "冲突"}, format="json").status_code == 409
