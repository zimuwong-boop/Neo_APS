from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError

from catalog.api import graph_lock
from sales.models import SalesOrder

from .engine import ACTIVE_STATES, digest, generate, qty, topology
from .models import Allocation, Audit, Batch, Plan, ProductionOrder, Report, ReportAllocation
from .progress import percentage


class Conflict(APIException):
    status_code = 409
    default_code = "conflict"


def conflict(code, detail):
    raise Conflict({"code": code, "detail": detail})


def audit(actor, operation, obj, details=None):
    Audit.objects.create(actor=actor, operation=operation, object_id=str(obj.pk), details=details or {})


def revision(obj, value):
    if value is not None and value != obj.revision:
        conflict("REVISION_CONFLICT", "记录已更新，请刷新后重试。")


@transaction.atomic
def new_plan(inputs, name="排产方案", actor=None):
    graph_lock()
    data, fingerprint = generate(inputs)
    plan = Plan.objects.create(inputs=inputs, name=name, data=data, fingerprint=fingerprint)
    audit(actor, "PLAN_CREATE", plan)
    return plan


def validate_schedule(data):
    sequences = {task["key"]: task["sequence"] for task in data["tasks"]}
    topology(data["tasks"], data["dependencies"], sequences)
    tasks = {task["key"]: task for task in data["tasks"]}
    for task in tasks.values():
        if task["target_start"] and task["target_end"] and task["target_start"] > task["target_end"]:
            raise ValidationError("目标开始日期不能晚于结束日期。")
    for child, parent in data["dependencies"]:
        if (tasks[child]["target_end"] and tasks[parent]["target_start"]
                and tasks[parent]["target_start"] < tasks[child]["target_end"]):
            raise ValidationError("父任务开始日期不能早于前置任务结束日期。")


@transaction.atomic
def edit_plan(plan, payload, actor=None):
    graph_lock()
    plan = Plan.objects.get(pk=plan.pk)
    revision(plan, payload.get("revision"))
    if plan.status != "DRAFT":
        raise ValidationError("只能修改草稿方案。")
    if "inputs" in payload:
        plan.inputs = payload["inputs"]
        plan.data, plan.fingerprint = generate(plan.inputs)
    if "name" in payload:
        plan.name = payload["name"]
    tasks = {task["key"]: task for task in plan.data["tasks"]}
    for change in payload.get("tasks", []):
        task = tasks.get(change["key"])
        if task is None:
            raise ValidationError("任务不属于当前方案。")
        if ("sequence" in change and change["sequence"] != task["sequence"]
                and not change.get("adjustment_reason", "").strip()):
            raise ValidationError("人工改变顺序必须填写原因。")
        for field in ("sequence", "target_start", "target_end", "line_label", "notes", "adjustment_reason"):
            if field in change:
                task[field] = change[field]
    validate_schedule(plan.data)
    plan.revision += 1
    plan.save()
    if hasattr(plan, "batch"):
        if plan.batch.orders.exclude(status="DRAFT").exists():
            raise ValidationError("已发布生产批次不能重建。")
        plan.batch.orders.all().delete()
        materialize(plan, "DRAFT", actor)
    audit(actor, "PLAN_UPDATE", plan, {"revision": plan.revision})
    return plan


@transaction.atomic
def materialize(plan, state="RELEASED", actor=None):
    graph_lock()
    plan = Plan.objects.get(pk=plan.pk)
    if plan.status == "PUBLISHED":
        return plan.batch
    try:
        fresh_data, fingerprint = generate(plan.inputs)
    except ValidationError:
        conflict("PLAN_STALE", "原需求已变化或已被其他方案安排，请重新生成。")
    if fingerprint != plan.fingerprint:
        conflict("PLAN_STALE", "销售需求、占用或 BOM/溯源发生变化，请重新生成方案。")
    del fresh_data
    validate_schedule(plan.data)
    batch, _ = Batch.objects.get_or_create(plan=plan)
    if batch.orders.exists():
        if batch.orders.exclude(status="DRAFT").exists():
            raise ValidationError("草稿批次存在不可发布状态。")
        if state == "RELEASED":
            batch.orders.update(status="RELEASED", revision=2)
    else:
        orders = {}
        for task in plan.data["tasks"]:
            orders[task["key"]] = ProductionOrder.objects.create(
                number=f"MO-{batch.pk.hex[:10].upper()}-{task['sequence']:03d}", batch=batch,
                task_key=task["key"], item_id=task["item"]["id"], bom_id=task["bom_id"],
                quantity=task["quantity"], priority=task["priority"], sequence=task["sequence"],
                snapshot=task["snapshot"], materials=[entry for entry in plan.data["materials"]
                                                     if entry["task"] == task["key"]],
                target_start=task["target_start"], target_end=task["target_end"],
                line_label=task["line_label"], notes=task["notes"], status=state,
            )
        allocations = []
        for entry in plan.data["allocations"]:
            allocations.append(Allocation.objects.create(
                order=orders[entry["task"]], sales_line_id=entry["sales_line"], kind=entry["kind"],
                parent=allocations[entry["parent"]] if entry["parent"] is not None else None,
                path=entry["path"], quantity=entry["quantity"], priority=entry["priority"],
                rank=entry["rank"], source=entry["source"],
            ))
    if state == "RELEASED":
        plan.status = "PUBLISHED"
        plan.revision += 1
        plan.save(update_fields=["status", "revision"])
    audit(actor, "BATCH_" + state, batch)
    return batch


def dependencies(order):
    ids = Allocation.objects.filter(parent__order=order).values_list("order_id", flat=True)
    return ProductionOrder.objects.filter(pk__in=ids).distinct()


def descendants(order):
    result, pending = set(), [order.pk]
    while pending:
        node = pending.pop()
        children = Allocation.objects.filter(order_id=node, parent__isnull=False).values_list(
            "parent__order_id", flat=True
        )
        for child in children:
            if child not in result:
                result.add(child)
                pending.append(child)
    return ProductionOrder.objects.filter(pk__in=result)


def refresh_sales(order_ids):
    for sale in SalesOrder.objects.filter(pk__in=order_ids).prefetch_related("lines"):
        complete = all(sum((allocation.fulfilled_qty for allocation in line.allocations.filter(
            kind="SALE", order__status__in=ACTIVE_STATES
        )), Decimal(0)) >= line.quantity for line in sale.lines.all())
        new_status = "COMPLETED" if complete else "CONFIRMED"
        if sale.status != new_status:
            sale.status = new_status
            sale.revision += 1
            sale.save(update_fields=["status", "revision"])


@transaction.atomic
def transition(order, action, actor=None, expected_revision=None):
    graph_lock()
    order = ProductionOrder.objects.get(pk=order.pk)
    revision(order, expected_revision)
    if action in ("start", "resume"):
        expected = "RELEASED" if action == "start" else "PAUSED"
        if order.status != expected:
            raise ValidationError("当前状态不允许开工/恢复。")
        if dependencies(order).exclude(status="COMPLETED").exists():
            conflict("DEPENDENCY_BLOCKED", "前置半成品尚未全部完成。")
        order.status = "IN_PROGRESS"
        if order.started_at is None:
            order.started_at = timezone.now()
    elif action == "pause":
        if order.status != "IN_PROGRESS":
            raise ValidationError("只能暂停生产中的任务。")
        order.status = "PAUSED"
    else:
        raise ValidationError("未知状态操作。")
    order.revision += 1
    order.save()
    audit(actor, action.upper(), order)
    return order


@transaction.atomic
def adjust_demo_progress(order, percent, actor=None, expected_revision=None):
    graph_lock()
    order = ProductionOrder.objects.get(pk=order.pk)
    revision(order, expected_revision)
    if order.status not in ("RELEASED", "IN_PROGRESS", "PAUSED"):
        raise ValidationError("只能调整待开工、生产中或暂停生产单的演示进度。")
    if percent is not None:
        if not percent.is_finite() or not 0 <= percent < 100:
            raise ValidationError("演示进度必须在 0～99.99%，不能手动完成生产单。")
        if percent < percentage(order.completed_qty, order.quantity):
            raise ValidationError("演示进度不能低于真实报工进度；可清除演示值回到真实进度。")
    previous = order.demo_progress_percent
    order.demo_progress_percent = percent
    order.revision += 1
    order.save(update_fields=["demo_progress_percent", "revision"])
    audit(actor, "DEMO_PROGRESS", order, {
        "before": str(previous) if previous is not None else None,
        "after": str(percent) if percent is not None else None,
        "actual_completed_qty": str(order.completed_qty),
    })
    return order


@transaction.atomic
def report(order, payload, actor):
    graph_lock()
    order = ProductionOrder.objects.get(pk=order.pk)
    amount = qty(payload["quantity"])
    key = payload["request_key"]
    old = Report.objects.filter(request_key=key).first()
    if old:
        if (old.order_id != order.pk or old.quantity != amount or old.reversal_of_id
                or old.batch_code != payload.get("batch_code", "")
                or old.notes != payload.get("notes", "")):
            conflict("IDEMPOTENCY_CONFLICT", "相同报工键携带了不同内容。")
        return old
    if order.status != "IN_PROGRESS":
        raise ValidationError("只能对生产中的任务报工。")
    if order.completed_qty + amount > order.quantity:
        raise ValidationError("累计合格产量不能超过计划量。")
    entry = Report.objects.create(order=order, quantity=amount, request_key=key, actor=actor,
                                  batch_code=payload.get("batch_code", ""), notes=payload.get("notes", ""))
    remaining = amount
    touched_sales = set()
    for allocation in order.allocations.order_by("rank"):
        take = min(remaining, allocation.quantity - allocation.fulfilled_qty)
        if take > 0:
            ReportAllocation.objects.create(report=entry, allocation=allocation, quantity=take)
            allocation.fulfilled_qty += take
            allocation.save(update_fields=["fulfilled_qty"])
            if allocation.kind == "SALE":
                touched_sales.add(allocation.sales_line.order_id)
            remaining -= take
        if remaining == 0:
            break
    if remaining:
        raise ValidationError("分配数量不守恒，事务已回滚。")
    order.completed_qty += amount
    order.demo_progress_percent = None
    if order.completed_qty == order.quantity:
        order.status = "COMPLETED"
    order.revision += 1
    order.save()
    refresh_sales(touched_sales)
    audit(actor, "REPORT", entry, {"quantity": str(amount)})
    return entry


@transaction.atomic
def reverse_report(entry, request_key, actor):
    graph_lock()
    entry = Report.objects.get(pk=entry.pk)
    old = Report.objects.filter(request_key=request_key).first()
    if old:
        if old.reversal_of_id != entry.pk:
            conflict("IDEMPOTENCY_CONFLICT", "冲销请求键已被其他操作使用。")
        return old
    if entry.quantity <= 0 or Report.objects.filter(reversal_of=entry).exists():
        raise ValidationError("该报工不能再次冲销。")
    latest = entry.order.reports.filter(quantity__gt=0, reversal__isnull=True).order_by(
        "-created_at", "-pk"
    ).first()
    if latest.pk != entry.pk:
        raise ValidationError("请先冲销最新的未冲销报告。")
    order = entry.order
    if descendants(order).filter(started_at__isnull=False).exists():
        conflict("DOWNSTREAM_STARTED", "下游已开工，不能冲销前置产量。")
    reversal = Report.objects.create(order=order, quantity=-entry.quantity, request_key=request_key,
                                     reversal_of=entry, actor=actor, notes="冲销原报工")
    touched_sales = set()
    for distribution in entry.distributions.select_related("allocation__sales_line"):
        allocation = distribution.allocation
        ReportAllocation.objects.create(report=reversal, allocation=allocation,
                                        quantity=-distribution.quantity)
        allocation.fulfilled_qty -= distribution.quantity
        allocation.save(update_fields=["fulfilled_qty"])
        if allocation.kind == "SALE":
            touched_sales.add(allocation.sales_line.order_id)
    order.completed_qty -= entry.quantity
    order.demo_progress_percent = None
    order.status = "PAUSED"
    order.revision += 1
    order.save()
    refresh_sales(touched_sales)
    audit(actor, "REPORT_REVERSE", reversal, {"original": str(entry.pk)})
    return reversal


def cancel_preview(order):
    orders = {item.pk: item for item in order.batch.orders.exclude(status="CANCELLED")}
    if order.pk not in orders:
        raise ValidationError("任务已取消。")
    edges = {key: set() for key in orders}
    for child, parent in Allocation.objects.filter(order__batch=order.batch, parent__isnull=False
                                                  ).values_list("order_id", "parent__order_id"):
        if child in edges and parent in edges:
            edges[child].add(parent)
            edges[parent].add(child)
    component, pending = set(), [order.pk]
    while pending:
        node = pending.pop()
        if node not in component:
            component.add(node)
            pending.extend(edges[node])
    affected = []
    for allocation in Allocation.objects.filter(order_id__in=component, kind="SALE"
                                                ).select_related("sales_line__order"):
        affected.append({"order_id": allocation.sales_line.order_id,
                         "number": allocation.sales_line.order.number,
                         "line_id": allocation.sales_line_id, "quantity": str(allocation.quantity)})
    tasks = [{"id": str(key), "number": orders[key].number, "revision": orders[key].revision,
              "status": orders[key].status, "started_at": str(orders[key].started_at or "")}
             for key in sorted(component)]
    return {"tasks": tasks, "affected_sales": affected,
            "allowed": all(orders[key].started_at is None for key in component),
            "token": digest([tasks, affected])}


@transaction.atomic
def cancel_component(order, token, actor=None):
    graph_lock()
    order = ProductionOrder.objects.get(pk=order.pk)
    if order.status == "DRAFT":
        raise ValidationError("草稿批次请使用删除操作。")
    preview = cancel_preview(order)
    if preview["token"] != token:
        conflict("CANCEL_PREVIEW_STALE", "取消预览已变化，请重新查看完整影响范围。")
    if not preview["allowed"]:
        conflict("COMPONENT_STARTED", "关联组件曾开工，不能取消。")
    ProductionOrder.objects.filter(pk__in=[task["id"] for task in preview["tasks"]]).update(
        status="CANCELLED", demo_progress_percent=None
    )
    audit(actor, "COMPONENT_CANCEL", order, preview)
    return preview
