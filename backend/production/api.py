from decimal import Decimal

from django.db import transaction
from django.db.models.deletion import ProtectedError
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from catalog.api import graph_lock

from .engine import digest, topology
from .models import Allocation, Audit, Plan, ProductionOrder, Report
from .progress import display_percentage, percentage
from .services import (
    adjust_demo_progress,
    audit,
    cancel_component,
    cancel_preview,
    dependencies,
    edit_plan,
    materialize,
    new_plan,
    report,
    reverse_report,
    revision,
    transition,
)


class PlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plan
        fields = "__all__"


class ChangeSerializer(serializers.Serializer):
    key = serializers.CharField()
    sequence = serializers.IntegerField(min_value=1, required=False)
    adjustment_reason = serializers.CharField(required=False, allow_blank=True)
    target_start = serializers.DateField(required=False, allow_null=True)
    target_end = serializers.DateField(required=False, allow_null=True)
    line_label = serializers.CharField(max_length=100, required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)


class PlanInputSerializer(serializers.Serializer):
    inputs = serializers.JSONField(required=False)
    name = serializers.CharField(max_length=120, required=False)
    revision = serializers.IntegerField(min_value=1, required=False)
    tasks = ChangeSerializer(many=True, required=False)

    def validate(self, attrs):
        for change in attrs.get("tasks", []):
            for field in ("target_start", "target_end"):
                if change.get(field):
                    change[field] = change[field].isoformat()
        return attrs


class PlanViewSet(viewsets.ModelViewSet):
    queryset = Plan.objects.all()
    serializer_class = PlanSerializer

    def create(self, request, *args, **kwargs):
        serializer = PlanInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        plan = new_plan(data.get("inputs", {}), data.get("name", "排产方案"), request.user)
        return Response(self.get_serializer(plan).data, status=201)

    def update(self, request, *args, **kwargs):
        serializer = PlanInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        plan = edit_plan(self.get_object(), serializer.validated_data, request.user)
        return Response(self.get_serializer(plan).data)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        graph_lock()
        plan = self.get_object()
        if plan.status != "DRAFT":
            raise ValidationError("已发布方案不可删除。")
        if hasattr(plan, "batch"):
            if plan.batch.orders.exclude(status="DRAFT").exists():
                raise ValidationError("关联生产批次不可删除。")
            plan.batch.orders.all().delete()
            plan.batch.delete()
        audit(request.user, "PLAN_DELETE", plan)
        plan.delete()
        return Response(status=204)

    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        batch = materialize(self.get_object(), actor=request.user)
        return Response({"batch_id": str(batch.pk), "orders": ProductionSerializer(
            batch.orders.all(), many=True
        ).data})


class AllocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Allocation
        fields = ["id", "sales_line", "parent", "kind", "path", "quantity", "fulfilled_qty",
                  "priority", "rank", "source"]


class ReportSerializer(serializers.ModelSerializer):
    distributions = serializers.SerializerMethodField()
    reversed = serializers.SerializerMethodField()

    class Meta:
        model = Report
        fields = ["id", "quantity", "request_key", "reversal_of", "reversed", "batch_code", "notes",
                  "created_at", "distributions"]

    def get_distributions(self, obj):
        return [{"allocation": entry.allocation_id, "quantity": str(entry.quantity)}
                for entry in obj.distributions.all()]

    def get_reversed(self, obj):
        return hasattr(obj, "reversal")


class ProductionSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="item.name", read_only=True)
    item_code = serializers.CharField(source="item.code", read_only=True)
    unit = serializers.CharField(source="item.unit", read_only=True)
    allocations = AllocationSerializer(many=True, read_only=True)
    reports = ReportSerializer(many=True, read_only=True)
    dependencies = serializers.SerializerMethodField()
    plan_id = serializers.UUIDField(source="batch.plan_id", read_only=True)
    display_progress_percent = serializers.SerializerMethodField()
    actual_progress_percent = serializers.SerializerMethodField()

    class Meta:
        model = ProductionOrder
        fields = "__all__"

    def get_dependencies(self, obj):
        return [{"id": str(item.pk), "number": item.number, "status": item.status}
                for item in dependencies(obj)]

    def get_display_progress_percent(self, obj):
        return float(display_percentage(obj))

    def get_actual_progress_percent(self, obj):
        return float(percentage(obj.completed_qty, obj.quantity))


class DemoProgressSerializer(serializers.Serializer):
    percent = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=Decimal(0),
                                       max_value=Decimal("99.99"), allow_null=True)
    revision = serializers.IntegerField(min_value=1, required=False)


class ProductionInputSerializer(serializers.Serializer):
    item = serializers.IntegerField(min_value=1, required=False)
    quantity = serializers.DecimalField(max_digits=20, decimal_places=6, min_value=Decimal("0.000001"),
                                        required=False)
    priority = serializers.IntegerField(min_value=1, max_value=5, required=False)
    revision = serializers.IntegerField(min_value=1, required=False)
    target_start = serializers.DateField(required=False, allow_null=True)
    target_end = serializers.DateField(required=False, allow_null=True)
    line_label = serializers.CharField(max_length=100, required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    adjustment_reason = serializers.CharField(required=False, allow_blank=True)
    sequence = serializers.IntegerField(min_value=1, required=False)


class ReportInputSerializer(serializers.Serializer):
    quantity = serializers.DecimalField(max_digits=20, decimal_places=6, min_value=Decimal("0.000001"))
    request_key = serializers.CharField(max_length=100)
    batch_code = serializers.CharField(max_length=100, required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)


class ProductionViewSet(viewsets.ModelViewSet):
    queryset = ProductionOrder.objects.select_related("item", "batch__plan").prefetch_related(
        "allocations", "reports__distributions"
    )
    serializer_class = ProductionSerializer

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        serializer = ProductionInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if "item" not in data or "quantity" not in data:
            raise ValidationError("请选择产品并填写计划量。")
        plan = new_plan({"standalone": [{"item": data["item"], "quantity": str(data["quantity"]),
                                        "priority": data.get("priority", 3)}]}, "手工生产草稿", request.user)
        changes = [{"key": task["key"], **{key: value.isoformat() if hasattr(value, "isoformat")
                                            else value for key, value in data.items()
                                            if key in ("target_start", "target_end", "line_label", "notes")}}
                   for task in plan.data["tasks"] if task["item"]["id"] == data["item"]]
        plan = edit_plan(plan, {"tasks": changes}, request.user)
        batch = materialize(plan, "DRAFT", request.user)
        root = batch.orders.get(item_id=data["item"])
        return Response(self.get_serializer(root).data, status=201)

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        graph_lock()
        order = self.get_object()
        serializer = ProductionInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        revision(order, data.get("revision"))
        if order.status == "DRAFT":
            plan = order.batch.plan
            payload = {"tasks": [{"key": order.task_key, **{
                key: value.isoformat() if hasattr(value, "isoformat") else value
                for key, value in data.items() if key in ("target_start", "target_end", "line_label",
                                                         "notes", "sequence", "adjustment_reason")
            }}]}
            if any(key in data for key in ("quantity", "priority", "item")):
                roots = plan.inputs.get("standalone", [])
                if len(roots) != 1 or roots[0]["item"] != order.item_id:
                    raise ValidationError("请在完整排产方案内修改来源量，不能孤立修改必要组件。")
                inputs = {"standalone": [{**roots[0], **{key: str(value) if key == "quantity" else value
                                                        for key, value in data.items()
                                                        if key in ("quantity", "priority", "item")}}]}
                payload["inputs"] = inputs
            edit_plan(plan, payload, request.user)
            return Response({"detail": "已重建完整草稿批次，请刷新列表。"})
        if order.status in ("CANCELLED", "COMPLETED"):
            raise ValidationError("当前状态不可编辑。")
        if any(key in data for key in ("quantity", "priority", "item", "sequence")):
            raise ValidationError("已发布数量和来源优先级冻结；调序请使用全局队列。")
        for key in ("target_start", "target_end", "line_label", "notes"):
            if key in data:
                setattr(order, key, data[key])
        if order.target_start and order.target_end and order.target_start > order.target_end:
            raise ValidationError("开始日期不能晚于结束日期。")
        if order.target_start and dependencies(order).filter(target_end__gt=order.target_start).exists():
            raise ValidationError("开始日期早于前置任务结束日期。")
        if order.target_end and Allocation.objects.filter(order=order,
            parent__order__target_start__lt=order.target_end).exists():
            raise ValidationError("结束日期晚于下游任务开始日期。")
        order.revision += 1
        order.save()
        audit(request.user, "PRODUCTION_UPDATE", order)
        return Response(self.get_serializer(order).data)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        graph_lock()
        order = self.get_object()
        if order.batch.orders.exclude(status="DRAFT").exists():
            raise ValidationError("只可删除完整草稿批次；已发布任务请先查看取消影响。")
        batch, plan = order.batch, order.batch.plan
        audit(request.user, "DRAFT_BATCH_DELETE", batch)
        try:
            batch.orders.all().delete()
            batch.delete()
            plan.delete()
        except ProtectedError:
            raise ValidationError("草稿已被历史记录引用。") from None
        return Response(status=204)

    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        batch = materialize(self.get_object().batch.plan, actor=request.user)
        return Response({"batch_id": str(batch.pk)})

    @action(detail=True, methods=["post"], url_path="(?P<operation>start|pause|resume)")
    def state(self, request, pk=None, operation=None):
        order = transition(self.get_object(), operation, request.user, request.data.get("revision"))
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"])
    def report(self, request, pk=None):
        serializer = ReportInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = report(self.get_object(), serializer.validated_data, request.user)
        return Response(ReportSerializer(entry).data)

    @action(detail=True, methods=["post"], url_path="demo-progress")
    def demo_progress(self, request, pk=None):
        serializer = DemoProgressSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        order = adjust_demo_progress(self.get_object(), data["percent"], request.user,
                                     data.get("revision"))
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["get"], url_path="cancel-preview")
    @transaction.atomic
    def preview(self, request, pk=None):
        graph_lock()
        return Response(cancel_preview(self.get_object()))

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        token = request.data.get("token")
        if not isinstance(token, str):
            raise ValidationError("必须先查看取消影响，并提交该预览 token。")
        return Response(cancel_component(self.get_object(), token, request.user))

    @action(detail=False, methods=["get", "post"])
    @transaction.atomic
    def queue(self, request):
        graph_lock()
        orders = list(ProductionOrder.objects.filter(status="RELEASED"))
        tasks = [{"key": str(order.pk), "sort_key": [order.priority, order.target_end.isoformat()
                  if order.target_end else "9999-12-31", order.number]} for order in orders]
        ids = {order.pk for order in orders}
        edges = sorted({(str(child), str(parent)) for child, parent in Allocation.objects.filter(
            order_id__in=ids, parent__order_id__in=ids
        ).values_list("order_id", "parent__order_id")})
        suggestion = topology(tasks, edges)
        current = [str(order.pk) for order in sorted(orders, key=lambda row: (row.sequence, row.number))]
        token = digest([(str(row.pk), row.revision) for row in orders])
        if request.method == "POST":
            if request.data.get("token") != token:
                from .services import conflict
                conflict("QUEUE_STALE", "队列已变化，请刷新插入建议。")
            ordered = request.data.get("order_ids")
            reason = request.data.get("reason", "")
            if not isinstance(ordered, list) or not isinstance(reason, str) or not reason.strip():
                raise ValidationError("请提交完整队列顺序和调整原因。")
            topology(tasks, edges, {key: index for index, key in enumerate(ordered, 1)})
            for index, key in enumerate(ordered, 1):
                row = next(row for row in orders if str(row.pk) == key)
                row.sequence = index
                row.revision += 1
                row.save(update_fields=["sequence", "revision"])
            Audit.objects.create(actor=request.user, operation="QUEUE_ADJUST", object_id="global",
                                 details={"before": current, "after": ordered, "reason": reason})
            current = ordered
        return Response({"suggested": suggestion, "current": current, "token": token,
                         "orders": ProductionSerializer(orders, many=True).data})


class ReportViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Report.objects.prefetch_related("distributions")
    serializer_class = ReportSerializer

    @action(detail=True, methods=["post"])
    def reverse(self, request, pk=None):
        key = request.data.get("request_key")
        if not isinstance(key, str) or not key or len(key) > 100:
            raise ValidationError("请提供有效的冲销幂等键。")
        return Response(ReportSerializer(reverse_report(self.get_object(), key, request.user)).data)


class AuditSerializer(serializers.ModelSerializer):
    class Meta:
        model = Audit
        fields = "__all__"


class AuditViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Audit.objects.all()
    serializer_class = AuditSerializer
