import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from catalog.models import BOM, BOMLine, Item


@pytest.fixture
def client(db):
    user = get_user_model().objects.create_user(username="tester", password="test-password")
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def items(db):
    return {kind: Item.objects.create(code=kind, name=kind, kind=kind, unit="件")
            for kind in ("FINISHED", "SEMI", "RAW")}


def bom_payload(parent, child, version=1):
    return {"item": parent.pk, "version": version, "output_qty": "1.000000",
            "lines": [{"component": child.pk, "quantity": "2.000000"}]}


def test_authentication_required(db):
    assert APIClient().get("/api/v1/items/").status_code == 403


def test_bom_rules_and_cycle(client, items):
    raw, semi, finished = (items[key] for key in ("RAW", "SEMI", "FINISHED"))
    assert client.post("/api/v1/boms/", bom_payload(raw, semi), format="json").status_code == 400
    assert client.post("/api/v1/boms/", bom_payload(semi, finished), format="json").status_code == 400
    other = Item.objects.create(code="OTHER", name="其他半成品", kind="SEMI", unit="件")
    assert client.post("/api/v1/boms/", bom_payload(semi, other), format="json").status_code == 201
    assert client.post("/api/v1/boms/", bom_payload(other, semi), format="json").status_code == 400


def test_publish_and_reference_protection(client, items):
    semi, raw = items["SEMI"], items["RAW"]
    response = client.post("/api/v1/boms/", bom_payload(semi, raw), format="json")
    assert response.status_code == 201
    url = f'/api/v1/boms/{response.data["id"]}/'
    assert client.post(url + "publish/").status_code == 200
    assert client.patch(url, {"output_qty": "3"}, format="json").status_code == 400
    assert client.delete(url).status_code == 400
    assert client.patch(f"/api/v1/items/{raw.pk}/", {"kind": "SEMI"},
                        format="json").status_code == 400
    assert client.delete(f"/api/v1/items/{raw.pk}/").status_code == 400
    second = client.post("/api/v1/boms/", bom_payload(semi, raw, 2), format="json")
    assert client.post(f'/api/v1/boms/{second.data["id"]}/publish/').status_code == 200
    assert client.get(url).data["status"] == "RETIRED"


def test_sales_crud_priorities_and_states(client, items):
    payload = {"number": "SO-001", "customer_name": "演示客户", "priority": 1,
               "lines": [{"item": items["RAW"].pk, "quantity": "3"}]}
    assert client.post("/api/v1/sales-orders/", payload, format="json").status_code == 400
    payload["lines"][0]["item"] = items["SEMI"].pk
    payload["priority"] = 6
    assert client.post("/api/v1/sales-orders/", payload, format="json").status_code == 400
    payload["priority"] = 1
    response = client.post("/api/v1/sales-orders/", payload, format="json")
    assert response.status_code == 201
    url = f'/api/v1/sales-orders/{response.data["id"]}/'
    assert client.patch(url, {"priority": 2}, format="json").status_code == 200
    assert client.post(url + "confirm/").status_code == 200
    assert client.patch(url, {"priority": 4}, format="json").status_code == 200
    assert client.delete(url).status_code == 400
    assert client.post(url + "cancel/").status_code == 200
    assert client.get(url).data["status"] == "CANCELLED"


def test_login_requires_csrf(db):
    get_user_model().objects.create_user(username="tester", password="test-password")
    client = APIClient(enforce_csrf_checks=True)
    payload = {"username": "tester", "password": "test-password"}
    assert client.post("/api/v1/auth/login/", payload, format="json").status_code == 403
    token = client.get("/api/v1/auth/session/").json()["csrfToken"]
    response = client.post("/api/v1/auth/login/", payload, format="json", HTTP_X_CSRFTOKEN=token)
    assert response.status_code == 200
    assert client.get("/api/v1/items/").status_code == 200


def test_bom_graph_exceeds_python_recursion_limit(client):
    nodes = Item.objects.bulk_create([
        Item(code=f"DEEP-{i}", name="深层半成品", kind="SEMI", unit="件") for i in range(1100)
    ])
    boms = BOM.objects.bulk_create([BOM(item=item, output_qty=1) for item in nodes[:-1]])
    BOMLine.objects.bulk_create([
        BOMLine(bom=bom, component=nodes[i + 1], quantity=1) for i, bom in enumerate(boms)
    ])
    # A closing edge traverses 1100 levels and must return validation, not RecursionError.
    assert client.post("/api/v1/boms/", bom_payload(nodes[-1], nodes[0]),
                       format="json").status_code == 400
