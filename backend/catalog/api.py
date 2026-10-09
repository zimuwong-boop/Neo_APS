from django.db import connection, transaction
from django.db.models import Max
from django.db.models.deletion import ProtectedError
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .models import BOM, BOMLine, Item


def graph_lock():
    # Serialize graph writes across requests, including item type changes.
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(71024001)")


class ItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = Item
        fields = "__all__"

    def validate(self, attrs):
        item = self.instance
        if item:
            from production.services import revision
            revision(item, attrs.pop("revision", None))
        if (item and any(attrs.get(key, getattr(item, key)) != getattr(item, key)
                         for key in ("kind", "unit"))
                and (item.boms.exists() or item.bom_usages.exists() or item.sales_lines.exists()
                     or item.production_orders.exists())):
            raise ValidationError("已被引用的物料不能修改类型或单位。")
        return attrs

    def update(self, instance, validated_data):
        instance.revision += 1
        return super().update(instance, validated_data)


class ItemViewSet(viewsets.ModelViewSet):
    queryset = Item.objects.all()
    serializer_class = ItemSerializer

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        graph_lock()
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        from production.services import audit
        audit(self.request.user, "ITEM_CREATE", serializer.save())

    def perform_update(self, serializer):
        from production.services import audit
        audit(self.request.user, "ITEM_UPDATE", serializer.save())

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        graph_lock()
        return super().update(request, *args, **kwargs)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        graph_lock()
        return super().destroy(request, *args, **kwargs)

    def perform_destroy(self, instance):
        from production.services import audit
        audit(self.request.user, "ITEM_DELETE", instance)
        try:
            instance.delete()
        except ProtectedError:
            raise ValidationError("物料已被引用，请停用而不是删除。") from None


class BOMLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = BOMLine
        fields = ["component", "quantity"]


class BOMSerializer(serializers.ModelSerializer):
    lines = BOMLineSerializer(many=True)

    class Meta:
        model = BOM
        fields = ["id", "item", "version", "output_qty", "status", "lines", "revision"]
        read_only_fields = ["status"]
        # Database constraints remain authoritative; validate uniqueness explicitly.
        validators = []

    def validate(self, attrs):
        instance = self.instance
        if instance:
            from production.services import revision
            revision(instance, attrs.pop("revision", None))
        if instance and instance.status != "DRAFT":
            raise ValidationError("已发布的 BOM 不可修改。")
        item = attrs.get("item", instance.item if instance else None)
        version = attrs.get("version", instance.version if instance else 1)
        duplicate = BOM.objects.filter(item=item, version=version)
        if instance:
            duplicate = duplicate.exclude(pk=instance.pk)
        if duplicate.exists():
            raise ValidationError("该物料的 BOM 版本已存在。")
        if item.kind == Item.Kind.RAW or not item.is_active:
            raise ValidationError("BOM 主件必须是启用的成品或半成品。")
        lines = attrs.get("lines")
        if lines is None:
            lines = [{"component": line.component} for line in instance.lines.all()]
        if not lines:
            raise ValidationError("BOM 至少需要一个组成物料。")
        components = [line["component"] for line in lines]
        if len({part.pk for part in components}) != len(components):
            raise ValidationError("组成物料不能重复。")
        if any(part.kind == Item.Kind.FINISHED or not part.is_active for part in components):
            raise ValidationError("组成物料只能是启用的半成品或原料。")
        # Conservatively check all non-retired versions. Iterative traversal has no depth limit.
        edges = {}
        queryset = BOMLine.objects.exclude(bom__status="RETIRED")
        if instance:
            queryset = queryset.exclude(bom_id=instance.pk)
        for parent, child in queryset.values_list("bom__item_id", "component_id"):
            edges.setdefault(parent, set()).add(child)
        edges.setdefault(item.pk, set()).update(part.pk for part in components)
        pending = [part.pk for part in components]
        visited = set()
        while pending:
            node = pending.pop()
            if node == item.pk:
                raise ValidationError("BOM 存在循环引用。")
            if node not in visited:
                visited.add(node)
                pending.extend(edges.get(node, ()))
        return attrs

    def create(self, validated_data):
        lines = validated_data.pop("lines")
        bom = BOM.objects.create(**validated_data)
        BOMLine.objects.bulk_create([BOMLine(bom=bom, **line) for line in lines])
        return bom

    def update(self, instance, validated_data):
        lines = validated_data.pop("lines", None)
        instance.revision += 1
        instance = super().update(instance, validated_data)
        if lines is not None:
            instance.lines.all().delete()
            BOMLine.objects.bulk_create([BOMLine(bom=instance, **line) for line in lines])
        return instance


class BOMViewSet(viewsets.ModelViewSet):
    queryset = BOM.objects.select_related("item").prefetch_related("lines__component")
    serializer_class = BOMSerializer

    def perform_create(self, serializer):
        from production.services import audit
        audit(self.request.user, "BOM_CREATE", serializer.save())

    def perform_update(self, serializer):
        from production.services import audit
        audit(self.request.user, "BOM_UPDATE", serializer.save())

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
            raise ValidationError("已发布的 BOM 不可删除。")
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def publish(self, request, pk=None):
        graph_lock()
        bom = self.get_object()
        serializer = self.get_serializer(bom, data={}, partial=True)
        serializer.is_valid(raise_exception=True)
        BOM.objects.filter(item=bom.item, status="ACTIVE").update(status="RETIRED")
        bom.status = "ACTIVE"
        bom.revision += 1
        bom.save(update_fields=["status", "revision"])
        from production.services import audit
        audit(request.user, "BOM_PUBLISH", bom)
        return Response(self.get_serializer(bom).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def clone(self, request, pk=None):
        graph_lock()
        source = self.get_object()
        version = (BOM.objects.filter(item=source.item).aggregate(value=Max("version"))["value"] or 0) + 1
        data = {"item": source.item_id, "version": version, "output_qty": str(source.output_qty),
                "lines": [{"component": line.component_id, "quantity": str(line.quantity)}
                          for line in source.lines.all()]}
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=201)

    @action(detail=True, methods=["get"])
    def tree(self, request, pk=None):
        root = self.get_object()
        boms = {bom.item_id: bom for bom in BOM.objects.filter(status="ACTIVE").prefetch_related(
            "lines__component"
        )}
        boms[root.item_id] = root
        nodes, edges, pending = {}, [], [root.item]
        while pending:
            item = pending.pop()
            if item.pk in nodes:
                continue
            nodes[item.pk] = ItemSerializer(item).data
            bom = boms.get(item.pk)
            if bom:
                for line in bom.lines.all():
                    edges.append({"parent": item.pk, "component": line.component_id,
                                  "quantity": str(line.quantity), "output_qty": str(bom.output_qty),
                                  "bom_id": bom.pk, "version": bom.version})
                    pending.append(line.component)
        return Response({"root": root.item_id, "nodes": list(nodes.values()), "edges": edges})
