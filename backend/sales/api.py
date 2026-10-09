from django.db import transaction
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from catalog.api import graph_lock
from catalog.models import Item
from production.engine import ACTIVE_STATES
from production.progress import percentage, preview_allocations
from production.services import audit, conflict, refresh_sales, revision

from .models import SalesLine, SalesOrder


class SalesLineSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(required=False)
    class Meta:
        model = SalesLine
        fields = ["id", "item", "quantity"]

    def validate_item(self, item):
        if item.kind == Item.Kind.RAW or not item.is_active:
            raise ValidationError("销售单只能选择启用的成品或半成品。")
        return item


class SalesSerializer(serializers.ModelSerializer):
    lines = SalesLineSerializer(many=True)
    progress = serializers.SerializerMethodField()

    class Meta:
        model = SalesOrder
        fields = ["id", "number", "customer_name", "priority", "due_date", "status",
                  "created_at", "lines", "revision", "progress"]
        read_only_fields = ["status", "created_at"]

    def validate(self, attrs):
        if self.instance:
            revision(self.instance, attrs.pop("revision", None))
            if self.instance.status in ("CANCELLED", "COMPLETED"):
                raise ValidationError("已取消或已完成销售单不可修改。")
        if "lines" in attrs and not attrs["lines"]:
            raise ValidationError("销售单至少需要一个明细。")
        return attrs

    def get_progress(self, obj):
        from decimal import Decimal
        result = []
        preview_cache = {}
        for line in obj.lines.all():
            allocations = list(line.allocations.filter(kind="SALE", order__status__in=ACTIVE_STATES
                                                       ).select_related("order"))
            planned = sum((entry.quantity for entry in allocations), Decimal(0))
            fulfilled = sum((entry.fulfilled_qty for entry in allocations), Decimal(0))
            displayed = Decimal(0)
            demo_active = False
            for entry in allocations:
                if entry.order.demo_progress_percent is not None:
                    demo_active = True
                    if entry.order_id not in preview_cache:
                        preview_cache[entry.order_id] = preview_allocations(entry.order)
                    displayed += preview_cache[entry.order_id][entry.pk]
                else:
                    displayed += entry.fulfilled_qty
            display_percent = percentage(displayed, line.quantity)
            # A high-priority virtual source can fill first, but only real reporting may show 100%.
            if fulfilled < line.quantity:
                display_percent = min(display_percent, Decimal("99.99"))
            result.append({"line_id": line.pk, "item": line.item_id, "quantity": str(line.quantity),
                           "planned": str(planned), "fulfilled": str(fulfilled),
                           "remaining": str(line.quantity - planned),
                           "percent": float(fulfilled / line.quantity * 100),
                           "display_percent": float(display_percent), "demo_active": demo_active,
                           "production_orders": [str(entry.order_id) for entry in allocations]})
        return result

    def create(self, validated_data):
        lines = validated_data.pop("lines")
        for line in lines:
            line.pop("id", None)
        order = SalesOrder.objects.create(**validated_data)
        SalesLine.objects.bulk_create([SalesLine(order=order, **line) for line in lines])
        return order

    def update(self, instance, validated_data):
        lines = validated_data.pop("lines", None)
        if lines is not None:
            existing = {line.pk: line for line in instance.lines.all()}
            supplied = [line["id"] for line in lines if "id" in line]
            if len(supplied) != len(set(supplied)) or set(supplied) - set(existing):
                raise ValidationError("销售明细 ID 重复或不属于本订单。")
            for key, line in existing.items():
                if key not in supplied and line.allocations.exists():
                    raise ValidationError("历史生产引用的销售明细不能移除。")
            for data in lines:
                line = existing.get(data.get("id"))
                if line:
                    planned = sum(entry.quantity for entry in line.allocations.filter(
                        kind="SALE", order__status__in=ACTIVE_STATES
                    ))
                    if data["quantity"] < planned:
                        raise ValidationError("销售数量不能低于已安排的数量。")
                    if line.allocations.exists() and data["item"].pk != line.item_id:
                        raise ValidationError("已有生产引用的销售产品不能更换。")
        instance.revision += 1
        instance = super().update(instance, validated_data)
        if lines is not None:
            instance.lines.exclude(pk__in=supplied).delete()
            for data in lines:
                key = data.pop("id", None)
                if key:
                    SalesLine.objects.filter(pk=key, order=instance).update(**data)
                else:
                    SalesLine.objects.create(order=instance, **data)
        if instance.status == "CONFIRMED":
            refresh_sales([instance.pk])
            instance.refresh_from_db()
        return instance


class SalesViewSet(viewsets.ModelViewSet):
    queryset = SalesOrder.objects.prefetch_related("lines")
    serializer_class = SalesSerializer

    def perform_create(self, serializer):
        audit(self.request.user, "SALES_CREATE", serializer.save())

    def perform_update(self, serializer):
        audit(self.request.user, "SALES_UPDATE", serializer.save())

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        graph_lock()
        return super().create(request, *args, **kwargs)

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        graph_lock()
        return super().update(request, *args, **kwargs)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        graph_lock()
        if self.get_object().status != "DRAFT":
            raise ValidationError("只能删除草稿销售单。")
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def confirm(self, request, pk=None):
        graph_lock()
        order = self.get_object()
        if order.status != "DRAFT":
            raise ValidationError("只能确认草稿销售单。")
        if not order.lines.exists() or order.lines.filter(item__is_active=False).exists():
            raise ValidationError("销售明细为空或包含停用物料。")
        order.status = "CONFIRMED"
        order.revision += 1
        order.save(update_fields=["status", "revision"])
        audit(request.user, "SALES_CONFIRM", order)
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def cancel(self, request, pk=None):
        graph_lock()
        order = self.get_object()
        if order.status not in ("DRAFT", "CONFIRMED"):
            raise ValidationError("当前状态不允许取消。")
        if order.lines.filter(allocations__kind="SALE",
                              allocations__order__status__in=ACTIVE_STATES).exists():
            conflict("ORDER_HAS_ALLOCATIONS", "销售单仍有有效生产分配，请先预览并取消共享生产组件。")
        order.status = "CANCELLED"
        order.revision += 1
        order.save(update_fields=["status", "revision"])
        audit(request.user, "SALES_CANCEL", order)
        return Response(self.get_serializer(order).data)
