from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections
from rest_framework.test import APIClient

from catalog.models import BOM, BOMLine, Item
from production.engine import edge_qty
from production.models import Allocation, Plan, ProductionOrder, Report
from production.services import (
    Conflict,
    cancel_component,
    cancel_preview,
    edit_plan,
    materialize,
    new_plan,
    report,
    reverse_report,
    transition,
)
from sales.models import SalesLine, SalesOrder


@pytest.fixture
def graph(db):
    raw = Item.objects.create(code="R", name="原料", kind="RAW", unit="kg")
    third = Item.objects.create(code="T", name="三级基料", kind="SEMI", unit="kg")
    semi = Item.objects.create(code="S", name="半成品", kind="SEMI", unit="kg")
    finished = Item.objects.create(code="F", name="成品", kind="FINISHED", unit="瓶")
    for parent, parts in [(third, [(raw, 4)]), (semi, [(third, 3), (raw, 2)]),
                          (finished, [(semi, 2), (raw, 1)])]:
        bom = BOM.objects.create(item=parent, output_qty=1, status="ACTIVE")
        BOMLine.objects.bulk_create([BOMLine(bom=bom, component=item, quantity=q) for item, q in parts])
    first = SalesOrder.objects.create(number="A", customer_name="甲", priority=1, status="CONFIRMED")
    second = SalesOrder.objects.create(number="B", customer_name="乙", priority=2, status="CONFIRMED")
    first_line = SalesLine.objects.create(order=first, item=finished, quantity=5)
    second_line = SalesLine.objects.create(order=second, item=semi, quantity=4)
    user = get_user_model().objects.create_user(username="operator")
    return raw, third, semi, finished, first, second, first_line, second_line, user


def make_plan(graph):
    return new_plan({"order_ids": [graph[4].pk, graph[5].pk]}, actor=graph[-1])


def test_shared_graph_and_materials(graph):
    plan = make_plan(graph)
    tasks = plan.data["tasks"]
    assert [(task["item"]["code"], Decimal(task["quantity"])) for task in tasks] == [
        ("T", 42), ("S", 14), ("F", 5)
    ]
    assert sum(Decimal(material["quantity"]) for material in plan.data["materials"]) == 201
    assert len(plan.data["allocations"]) == 5
    assert len(plan.data["dependencies"]) == 2


def test_round_source_before_merge_and_base_quantity():
    assert edge_qty(Decimal("0.000001"), Decimal(1), Decimal(3)) == Decimal("0.000001")
    assert edge_qty(Decimal("0.000001"), Decimal(1), Decimal(3)) * 2 != edge_qty(
        Decimal("0.000002"), Decimal(1), Decimal(3)
    )


def test_dependency_reporting_sales_and_exact_reversal(graph):
    actor = graph[-1]
    batch = materialize(make_plan(graph), actor=actor)
    third, semi, finished = [batch.orders.get(item=item) for item in graph[1:4]]
    with pytest.raises(Conflict):
        transition(finished, "start", actor)
    third = transition(third, "start", actor)
    report(third, {"quantity": 42, "request_key": "t"}, actor)
    semi = transition(semi, "start", actor)
    entry = report(semi, {"quantity": 12, "request_key": "s1"}, actor)
    assert sum(row.quantity for row in entry.distributions.all()) == 12
    assert Allocation.objects.get(order=semi, kind="SALE").fulfilled_qty == 2
    assert Allocation.objects.get(order=semi, kind="COMPONENT").fulfilled_qty == 10
    assert Allocation.objects.get(order=finished, kind="SALE").fulfilled_qty == 0
    assert report(semi, {"quantity": 12, "request_key": "s1"}, actor).pk == entry.pk
    with pytest.raises(Conflict):
        report(semi, {"quantity": 11, "request_key": "s1"}, actor)
    last = report(semi, {"quantity": 2, "request_key": "s2"}, actor)
    graph[5].refresh_from_db()
    assert graph[5].status == "COMPLETED"
    reversal = reverse_report(last, "reverse-s2", actor)
    assert reversal.quantity == -2
    graph[5].refresh_from_db()
    assert graph[5].status == "CONFIRMED"
    semi.refresh_from_db()
    assert semi.status == "PAUSED"
    transition(semi, "resume", actor)
    replacement = report(semi, {"quantity": 2, "request_key": "s3"}, actor)
    transition(finished, "start", actor)
    with pytest.raises(Conflict):
        reverse_report(replacement, "blocked", actor)
    report(finished, {"quantity": 2, "request_key": "f1"}, actor)
    assert Allocation.objects.get(order=finished, kind="SALE").fulfilled_qty == 2


def test_duplicate_plan_publish_and_stale_competitor(graph):
    first, second = make_plan(graph), make_plan(graph)
    batch = materialize(first)
    assert materialize(first).pk == batch.pk
    assert batch.orders.count() == 3
    with pytest.raises(Conflict):
        materialize(second)
    assert not ProductionOrder.objects.exclude(batch=batch).exists()


def test_partial_demand_manual_order_and_stale_bom(graph):
    plan = make_plan(graph)
    plan = edit_plan(plan, {"inputs": {"order_ids": [graph[4].pk, graph[5].pk],
                                       "line_quantities": {str(graph[6].pk): "3"}}})
    assert sorted(Decimal(task["quantity"]) for task in plan.data["tasks"]) == [3, 10, 30]
    with pytest.raises(Exception, match="前置半成品"):
        edit_plan(plan, {"tasks": [{"key": task["key"], "sequence": 4 - task["sequence"],
                                   "adjustment_reason": "错误调序"} for task in plan.data["tasks"]]})
    graph[0].traceability_info = "新批次"
    graph[0].save()
    with pytest.raises(Conflict):
        materialize(plan)


def test_cancel_shared_component_releases_other_sale(graph):
    batch = materialize(make_plan(graph))
    root = batch.orders.get(item=graph[3])
    preview = cancel_preview(root)
    assert len(preview["tasks"]) == 3
    assert {entry["number"] for entry in preview["affected_sales"]} == {"A", "B"}
    cancel_component(root, preview["token"])
    assert batch.orders.filter(status="CANCELLED").count() == 3
    assert len(make_plan(graph).data["tasks"]) == 3
    graph[5].refresh_from_db()
    assert graph[5].status == "CONFIRMED"


def test_cancel_stale_preview_after_start(graph):
    batch = materialize(make_plan(graph))
    root = batch.orders.get(item=graph[3])
    preview = cancel_preview(root)
    transition(batch.orders.get(item=graph[1]), "start", graph[-1])
    with pytest.raises(Conflict):
        cancel_component(root, preview["token"])
    assert not batch.orders.filter(status="CANCELLED").exists()


def test_sales_edit_reserved_quantity_and_cancel_guard(graph):
    materialize(make_plan(graph))
    client = APIClient()
    client.force_authenticate(graph[-1])
    url = f"/api/v1/sales-orders/{graph[4].pk}/"
    assert client.post(url + "cancel/").status_code == 409
    assert client.patch(url, {"lines": [{"id": graph[6].pk, "item": graph[3].pk, "quantity": 4}]},
                        format="json").status_code == 400
    assert client.patch(url, {"priority": 5}, format="json").status_code == 200
    assert Allocation.objects.get(kind="SALE", sales_line=graph[6]).priority == 1
    assert client.get(url).data["progress"][0]["remaining"] == "0.000000"


def test_production_draft_crud_and_standalone_no_sales_credit(graph):
    client = APIClient()
    client.force_authenticate(graph[-1])
    result = client.post("/api/v1/production-orders/", {"item": graph[2].pk, "quantity": 2}, format="json")
    assert result.status_code == 201
    url = f'/api/v1/production-orders/{result.data["id"]}/'
    assert client.patch(url, {"quantity": 3}, format="json").status_code == 200
    root = ProductionOrder.objects.get(item=graph[2])
    assert root.quantity == 3
    assert root.batch.orders.count() == 2
    assert client.delete(f"/api/v1/production-orders/{root.pk}/").status_code == 204
    assert ProductionOrder.objects.count() == 0
    batch = materialize(new_plan({"standalone": [{"item": graph[1].pk, "quantity": "2"}]}))
    root = batch.orders.get(item=graph[1])
    transition(root, "start", graph[-1])
    report(root, {"quantity": 2, "request_key": "standalone"}, graph[-1])
    assert Allocation.objects.get(order=root).kind == "STANDALONE"
    assert not Allocation.objects.filter(kind="SALE").exists()


@pytest.mark.django_db(transaction=True)
def test_concurrent_publish_only_one_reserves(graph):
    plans = [make_plan(graph).pk, make_plan(graph).pk]

    def worker(key):
        close_old_connections()
        try:
            materialize(Plan.objects.get(pk=key))
            return "ok"
        except Conflict:
            return "stale"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(worker, plans)) == ["ok", "stale"]
    assert ProductionOrder.objects.count() == 3


@pytest.mark.django_db(transaction=True)
def test_concurrent_report_never_overproduces(graph):
    batch = materialize(make_plan(graph))
    order = batch.orders.get(item=graph[1])
    transition(order, "start", graph[-1])

    def worker(index):
        close_old_connections()
        try:
            report(ProductionOrder.objects.get(pk=order.pk),
                   {"quantity": 30, "request_key": f"parallel-{index}"}, graph[-1])
            return "ok"
        except Exception as error:
            from rest_framework.exceptions import ValidationError
            if isinstance(error, ValidationError):
                return "blocked"
            raise
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(worker, range(2))) == ["blocked", "ok"]
    order.refresh_from_db()
    assert order.completed_qty == 30
    assert Report.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_bom_graph_writes_cannot_create_cycle(graph):
    first = Item.objects.create(code="CYCLE-A", name="A", kind="SEMI", unit="kg")
    second = Item.objects.create(code="CYCLE-B", name="B", kind="SEMI", unit="kg")

    def worker(pair):
        close_old_connections()
        try:
            client = APIClient()
            client.force_authenticate(graph[-1])
            parent, child = pair
            return client.post("/api/v1/boms/", {"item": parent.pk, "output_qty": "1",
                "lines": [{"component": child.pk, "quantity": "1"}]}, format="json").status_code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(worker, [(first, second), (second, first)])) == [201, 400]
