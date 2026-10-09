"""Source-first Decimal expansion and priority-aware topological scheduling."""
import hashlib
import heapq
import json
from decimal import ROUND_CEILING, Decimal, InvalidOperation, localcontext

from django.db.models import Sum
from rest_framework.exceptions import ValidationError

from catalog.models import BOM, Item
from sales.models import SalesOrder

from .models import Allocation

ACTIVE_STATES = ("RELEASED", "IN_PROGRESS", "PAUSED", "COMPLETED")
QUANTUM = Decimal("0.000001")
MAX_QUANTITY = Decimal("99999999999999.999999")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def qty(value, *, allow_zero=False):
    try:
        result = Decimal(str(value))
        if (not result.is_finite() or result > MAX_QUANTITY or result < 0
                or (result == 0 and not allow_zero) or result != result.quantize(QUANTUM)):
            raise InvalidOperation
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError("数量必须为不超出 numeric(20,6) 的正数（最多六位小数）。") from None
    return result


def edge_qty(parent, component, output):
    with localcontext() as ctx:
        ctx.prec = 60
        result = (parent * component / output).quantize(QUANTUM, rounding=ROUND_CEILING)
    return qty(result)


def occupied():
    return {row["sales_line_id"]: row["total"] for row in Allocation.objects.filter(
        kind="SALE", order__status__in=ACTIVE_STATES
    ).values("sales_line_id").annotate(total=Sum("quantity"))}


def topology(tasks, dependencies, sequences=None):
    by_key = {task["key"]: task for task in tasks}
    outgoing = {key: set() for key in by_key}
    indegree = dict.fromkeys(by_key, 0)
    for child, parent in dependencies:
        if child == parent:
            raise ValidationError("制造依赖形成循环。")
        if parent not in outgoing[child]:
            outgoing[child].add(parent)
            indegree[parent] += 1
    heap = []
    for key, degree in indegree.items():
        if degree == 0:
            heapq.heappush(heap, (by_key[key]["sort_key"], key))
    ordered = []
    while heap:
        _, key = heapq.heappop(heap)
        ordered.append(key)
        for parent in outgoing[key]:
            indegree[parent] -= 1
            if indegree[parent] == 0:
                heapq.heappush(heap, (by_key[parent]["sort_key"], parent))
    if len(ordered) != len(tasks):
        raise ValidationError("制造依赖形成循环。")
    if sequences is not None:
        if set(sequences) != set(by_key) or sorted(sequences.values()) != list(range(1, len(tasks) + 1)):
            raise ValidationError("人工顺序必须完整且从 1 连续排列。")
        if any(sequences[child] >= sequences[parent] for child, parent in dependencies):
            raise ValidationError("前置半成品必须排在消耗它的产品之前。")
        ordered.sort(key=sequences.__getitem__)
    return ordered


def generate(inputs):
    if not isinstance(inputs, dict):
        raise ValidationError("排产输入必须是对象。")
    order_ids = inputs.get("order_ids", [])
    standalone = inputs.get("standalone", [])
    quantities = inputs.get("line_quantities", {})
    if (not isinstance(order_ids, list) or not isinstance(standalone, list)
            or not isinstance(quantities, dict) or bool(order_ids) == bool(standalone)):
        raise ValidationError("请选择销售订单或独立生产需求，两种模式不可混用。")
    if any(type(key) is not int or key <= 0 for key in order_ids):
        raise ValidationError("销售订单 ID 必须为正整数。")
    items = {item.pk: {"id": item.pk, "code": item.code, "name": item.name,
                       "kind": item.kind, "unit": item.unit, "active": item.is_active,
                       "traceability_info": item.traceability_info, "specification": item.specification,
                       "product_standard": item.product_standard, "allergens": item.allergens,
                       "shelf_life_days": item.shelf_life_days, "storage_condition": item.storage_condition}
             for item in Item.objects.all()}
    boms = {bom.item_id: {"id": bom.pk, "version": bom.version,
                         "output_qty": str(bom.output_qty),
                         "lines": [{"id": line.pk, "component": line.component_id,
                                    "quantity": str(line.quantity)} for line in bom.lines.all()]}
            for bom in BOM.objects.filter(status="ACTIVE").prefetch_related("lines")}
    roots, sales_snapshot = [], []
    usage = occupied()
    orders = list(SalesOrder.objects.filter(pk__in=order_ids).prefetch_related("lines"))
    if len(orders) != len(set(order_ids)):
        raise ValidationError("销售订单不存在。")
    known_lines = set()
    for order in orders:
        if order.status not in ("CONFIRMED", "COMPLETED"):
            raise ValidationError(f"{order.number} 尚未确认或已取消。")
        for line in order.lines.all().order_by("pk"):
            known_lines.add(str(line.pk))
            used = usage.get(line.pk, Decimal(0))
            available = line.quantity - used
            if available < 0:
                raise ValidationError("销售需求占用超过订单数量。")
            sales_snapshot.append([order.pk, order.revision, order.status, order.priority,
                                   str(order.due_date), line.pk, line.item_id,
                                   str(line.quantity), str(used)])
            amount = qty(quantities.get(str(line.pk), available), allow_zero=True)
            if amount > available:
                raise ValidationError(f"{order.number} 本次安排量超过待排量。")
            if amount:
                source = {"order_id": order.pk, "number": order.number, "line_id": line.pk,
                          "priority": order.priority, "due_date": str(order.due_date or ""),
                          "created_at": order.created_at.isoformat()}
                roots.append((line.item_id, amount, "SALE", line.pk, source, f"sale:{line.pk}"))
    if set(quantities) - known_lines:
        raise ValidationError("安排量包含不属于所选订单的明细。")
    for index, entry in enumerate(standalone):
        if not isinstance(entry, dict):
            raise ValidationError("独立需求格式无效。")
        if type(entry.get("item")) is not int or entry["item"] <= 0:
            raise ValidationError("独立生产产品 ID 必须为正整数。")
        priority = entry.get("priority", 3)
        if type(priority) is not int or not 1 <= priority <= 5:
            raise ValidationError("优先级必须是 1～5。")
        source = {"order_id": 0, "number": "独立生产", "line_id": None,
                  "priority": priority, "due_date": "", "created_at": ""}
        roots.append((entry.get("item"), qty(entry.get("quantity")), "STANDALONE", None,
                      source, f"standalone:{index}"))
    if not roots:
        raise ValidationError("所选需求已全部安排，无待排数量。")

    # Postorder hashes include the complete reachable manufacturing graph without recursion.
    signatures, snapshots, visiting = {}, {}, set()
    stack = [(root[0], False) for root in roots]
    while stack:
        item_id, expanded = stack.pop()
        if item_id in signatures:
            continue
        item = items.get(item_id)
        if not item or not item["active"] or item["kind"] == "RAW":
            raise ValidationError("生产产品必须为启用的成品或半成品。")
        bom = boms.get(item_id)
        if not bom or not bom["lines"]:
            raise ValidationError(f'{item["code"]} 缺少完整生效 BOM。')
        if not expanded:
            if item_id in visiting:
                raise ValidationError("生效 BOM 存在循环。")
            visiting.add(item_id)
            stack.append((item_id, True))
            for line in reversed(bom["lines"]):
                part = items[line["component"]]
                if not part["active"] or part["kind"] == "FINISHED":
                    raise ValidationError("BOM 含停用物料或成品组件。")
                if part["kind"] == "SEMI":
                    stack.append((part["id"], False))
        else:
            snapshot = {"item": item, "bom": bom, "components": [
                {"item": items[line["component"]],
                 "child_signature": signatures.get(line["component"])} for line in bom["lines"]
            ]}
            snapshots[item_id] = snapshot
            signatures[item_id] = digest(snapshot)
            visiting.remove(item_id)

    tasks, allocations, materials, dependencies = {}, [], {}, set()
    stack = [(item, amount, kind, sale, source, path, None)
             for item, amount, kind, sale, source, path in reversed(roots)]
    while stack:
        item_id, amount, kind, sale, source, path, parent_index = stack.pop()
        if len(allocations) >= 100000:
            raise ValidationError("需求路径超过本机计算预算；请分批排产，未保存截断数据。")
        key = digest([item_id, boms[item_id]["id"], items[item_id]["unit"], signatures[item_id]])
        sort_key = [source["priority"], source["due_date"] or "9999-12-31",
                    source["created_at"], source["order_id"], sale or 0, path]
        if key not in tasks:
            tasks[key] = {"key": key, "item": items[item_id], "bom_id": boms[item_id]["id"],
                          "signature": signatures[item_id], "snapshot": snapshots[item_id],
                          "quantity": "0", "priority": source["priority"], "sort_key": sort_key,
                          "target_start": None, "target_end": None, "line_label": "", "notes": ""}
        task = tasks[key]
        task["quantity"] = str(qty(Decimal(task["quantity"]) + amount))
        task["priority"] = min(task["priority"], source["priority"])
        task["sort_key"] = min(task["sort_key"], sort_key)
        index = len(allocations)
        allocations.append({"task": key, "sales_line": sale, "kind": kind,
                            "parent": parent_index, "path": path, "quantity": str(amount),
                            "priority": source["priority"], "sort_key": sort_key, "source": source})
        if parent_index is not None:
            dependencies.add((key, allocations[parent_index]["task"]))
        bom = boms[item_id]
        for line in reversed(bom["lines"]):
            part = items[line["component"]]
            component_qty = edge_qty(amount, Decimal(line["quantity"]), Decimal(bom["output_qty"]))
            if part["kind"] == "RAW":
                material_key = (key, part["id"])
                previous = materials.get(material_key, {"task": key, "item": part, "quantity": "0"})
                previous["quantity"] = str(qty(Decimal(previous["quantity"]) + component_qty))
                materials[material_key] = previous
            else:
                stack.append((part["id"], component_qty, "COMPONENT", sale, source,
                              f'{path}/{line["id"]}', index))
    grouped = {key: [] for key in tasks}
    for entry in allocations:
        grouped[entry["task"]].append(entry)
    for entries in grouped.values():
        entries.sort(key=lambda entry: entry["sort_key"])
        for rank, entry in enumerate(entries, 1):
            entry["rank"] = rank
    task_list = list(tasks.values())
    order = topology(task_list, sorted(dependencies))
    positions = {key: index for index, key in enumerate(order, 1)}
    for task in task_list:
        task["sequence"] = positions[task["key"]]
        task["suggested_sequence"] = task["sequence"]
        task["adjustment_reason"] = ""
    task_list.sort(key=lambda task: task["sequence"])
    fingerprint = digest({"sales": sales_snapshot, "roots": [
        [root[0], str(root[1]), root[2], root[3], root[4], root[5]] for root in roots],
        "manufacturing": signatures, "snapshots": snapshots})
    return {"tasks": task_list, "allocations": allocations,
            "materials": list(materials.values()), "dependencies": sorted(dependencies)}, fingerprint
