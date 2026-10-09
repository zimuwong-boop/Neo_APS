"""Add a complete, repeat-safe condiment demo without replacing existing user data."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from catalog.api import graph_lock
from catalog.models import BOM, BOMLine, Item
from production.models import Audit
from production.services import edit_plan, materialize, new_plan, report, transition
from sales.models import SalesLine, SalesOrder

PREFIX = "DEMO-"
MARKER = "CONDIMENT_DEMO_V1"


class Command(BaseCommand):
    help = "创建调味品演示数据（幂等；不删除、不覆盖用户数据）。"

    @transaction.atomic
    def handle(self, *args, **options):
        graph_lock()
        if Audit.objects.filter(operation="DEMO_SEED", object_id=MARKER).exists():
            self.stdout.write("调味品演示数据已存在；保留当前演示进度，不重复创建。")
            return
        if Item.objects.filter(code__startswith=PREFIX).exists() or SalesOrder.objects.filter(
            number__startswith=PREFIX
        ).exists():
            raise CommandError("DEMO- 前缀已被占用；为避免覆盖数据，未执行导入。")
        actor = get_user_model().objects.filter(username="admin").first()
        if actor is None:
            raise CommandError("请先创建唯一 admin 账号。")
        items = {}
        raw_specs = [
            ("CHILI", "干辣椒粉", "kg", "25 kg/袋", "模拟香辛料供应商 A"),
            ("PEPPER", "花椒粉", "kg", "10 kg/袋", "模拟香辛料供应商 A"),
            ("CUMIN", "孜然粉", "kg", "10 kg/袋", "模拟香辛料供应商 B"),
            ("SALT", "食用盐", "kg", "25 kg/袋", "模拟盐业供应商"),
            ("SUGAR", "白砂糖", "kg", "25 kg/袋", "模拟糖业供应商"),
            ("OIL", "精炼植物油", "kg", "20 kg/桶", "模拟油脂供应商"),
            ("WATER", "生产用水", "kg", "厂内处理用水", "模拟水处理站"),
            ("SOY", "采购酿造酱油原液", "kg", "20 kg/桶", "模拟酿造酱油供应商"),
            ("BAG200", "200 g 调味酱袋", "个", "食品接触包装", "模拟包装供应商"),
            ("BAG100", "100 g 调味粉袋", "个", "食品接触包装", "模拟包装供应商"),
            ("BOTTLE", "500 g 调味液瓶", "个", "食品接触包装", "模拟瓶器供应商"),
            ("CAP", "调味液瓶盖", "个", "配套瓶盖", "模拟瓶器供应商"),
            ("LABEL", "演示标签", "张", "模拟产品标签", "模拟印刷供应商"),
        ]
        for index, (code, name, unit, specification, supplier) in enumerate(raw_specs, 1):
            items[code] = Item.objects.create(
                code=PREFIX + code, name=name, kind="RAW", unit=unit, specification=specification,
                traceability_info=f"模拟数据；供应商={supplier}；来料批次=RM-20261001-{index:03d}；检验记录=DEMO-IQC-{index:03d}（非真实检测）",
                allergens="大豆、小麦" if code == "SOY" else "", storage_condition="阴凉、干燥，按物料条件分类存放",
                product_standard="GB 2717-2018（采购酿造酱油分类参考）" if code == "SOY" else "供应商产品标准（模拟）",
            )
        manufactured = [
            ("SPICE", "复合香辛料基粉", "SEMI", "kg", "100 kg 基准批", 180),
            ("CHILI-OIL", "油椒基料", "SEMI", "kg", "100 kg 基准批", 90),
            ("SAUCE", "复合调味酱基料", "SEMI", "kg", "100 kg 基准批", 90),
            ("MALA200", "麻辣复合调味酱 200 g", "FINISHED", "袋", "净含量 200 g/袋", 365),
            ("HOT200", "香辣复合调味酱 200 g", "FINISHED", "袋", "净含量 200 g/袋", 365),
            ("BBQ100", "烧烤复合调味粉 100 g", "FINISHED", "袋", "净含量 100 g/袋", 365),
            ("LIQ500", "风味复合调味液 500 g", "FINISHED", "瓶", "净含量 500 g/瓶", 365),
        ]
        for code, name, kind, unit, specification, shelf_life in manufactured:
            items[code] = Item.objects.create(
                code=PREFIX + code, name=name, kind=kind, unit=unit, specification=specification,
                product_standard="GB 31644-2018（复合调味料分类参考）",
                allergens="大豆、小麦" if code == "LIQ500" else "",
                shelf_life_days=shelf_life, storage_condition="模拟：阴凉干燥避光；开封后按产品条件储存",
                traceability_info=f"模拟配方；配方编号=FORMULA-{code}-V1；生产机构=Neo 调味品演示工厂；模拟检验记录=DEMO-QC-{code}；保质期为展示参数，未经验证",
            )
        recipes = {
            "SPICE": (100, [("CHILI", 40), ("PEPPER", 10), ("CUMIN", 20), ("SALT", 20), ("SUGAR", 10)]),
            "CHILI-OIL": (100, [("CHILI", 35), ("OIL", 55), ("SALT", 10)]),
            "SAUCE": (100, [("CHILI-OIL", 60), ("SPICE", 20), ("WATER", 10), ("SUGAR", 10)]),
            "MALA200": (1, [("SAUCE", "0.18"), ("SPICE", "0.02"), ("BAG200", 1), ("LABEL", 1)]),
            "HOT200": (1, [("SAUCE", "0.19"), ("OIL", "0.01"), ("BAG200", 1), ("LABEL", 1)]),
            "BBQ100": (1, [("SPICE", "0.1"), ("BAG100", 1), ("LABEL", 1)]),
            "LIQ500": (1, [("SPICE", "0.03"), ("SOY", "0.15"), ("WATER", "0.30"), ("SUGAR", "0.02"), ("BOTTLE", 1), ("CAP", 1), ("LABEL", 1)]),
        }
        for code, (output, parts) in recipes.items():
            bom = BOM.objects.create(item=items[code], output_qty=output, status="ACTIVE")
            BOMLine.objects.bulk_create([BOMLine(bom=bom, component=items[part], quantity=Decimal(str(amount)))
                                        for part, amount in parts])
        today = timezone.localdate()
        orders = []
        order_specs = [
            ("001", "华东连锁餐饮（模拟）", 1, 2, [("MALA200", 500)]),
            ("002", "社区零售经销商（模拟）", 3, 7, [("MALA200", 300)]),
            ("003", "西南团餐中心（模拟）", 2, 3, [("HOT200", 600)]),
            ("004", "烧烤渠道经销商（模拟）", 4, 8, [("BBQ100", 800)]),
            ("005", "便利店配送中心（模拟）", 5, 10, [("LIQ500", 400)]),
            ("006", "餐饮中央厨房（模拟）", 2, 1, [("SAUCE", 50)]),
            ("007", "食品加工客户（模拟）", 3, None, [("SPICE", 20)]),
            ("008", "新渠道样品订单（模拟）", 3, 6, [("HOT200", 100), ("BBQ100", 200)]),
            ("009", "待确认客户（模拟）", 3, None, [("MALA200", 60)]),
        ]
        for number, customer, priority, days, parts in order_specs:
            sale = SalesOrder.objects.create(number=PREFIX + "SO-" + number, customer_name=customer,
                                            priority=priority, due_date=today + timedelta(days=days)
                                            if days is not None else None,
                                            status="DRAFT" if number == "009" else "CONFIRMED")
            SalesLine.objects.bulk_create([SalesLine(order=sale, item=items[code], quantity=amount)
                                          for code, amount in parts])
            orders.append(sale)
        live = new_plan({"order_ids": [orders[index].pk for index in (0, 1, 2, 5)]},
                        "演示 A · 共享基料与半成品直销", actor)
        live = edit_plan(live, {"tasks": [{"key": task["key"], "line_label": "混料线 A"
                                          if task["item"]["kind"] == "SEMI" else "包装线 B",
                                          "target_start": today.isoformat(),
                                          "target_end": today.isoformat(),
                                          "notes": "模拟生产安排"} for task in live.data["tasks"]]}, actor)
        batch = materialize(live, actor=actor)
        for row in batch.orders.filter(item__code__in=[PREFIX + "SPICE", PREFIX + "CHILI-OIL"]):
            transition(row, "start", actor)
            report(row, {"quantity": row.quantity, "request_key": MARKER + row.item.code,
                         "batch_code": "DEMO-LOT-BASE-202610", "notes": "模拟：基料完成"}, actor)
        sauce = batch.orders.get(item=items["SAUCE"])
        transition(sauce, "start", actor)
        # 90 kg fulfills P1 internal demand first, then 20 kg of P2 direct sale; sales F stays 0%.
        report(sauce, {"quantity": "110", "request_key": MARKER + "-SAUCE-PART",
                       "batch_code": "DEMO-LOT-SAUCE-202610", "notes": "模拟：分配内部组件90 kg + 半成品销售20 kg"}, actor)
        new_plan({"order_ids": [orders[index].pk for index in (3, 4, 6, 7)]},
                 "演示 B · 五级顺序与多产品待排", actor)
        independent = new_plan({"standalone": [{"item": items["SPICE"].pk, "quantity": "25", "priority": 3}]},
                               "演示 C · 独立半成品草稿", actor)
        materialize(independent, "DRAFT", actor)
        paused = materialize(new_plan({"standalone": [{"item": items["CHILI-OIL"].pk,
                                                       "quantity": "10", "priority": 4}]},
                                      "演示 D · 暂停批次", actor), actor=actor).orders.get(item=items["CHILI-OIL"])
        transition(paused, "start", actor)
        report(paused, {"quantity": "3", "request_key": MARKER + "-PAUSED",
                        "batch_code": "DEMO-LOT-PAUSE-202610"}, actor)
        transition(paused, "pause", actor)
        Audit.objects.create(actor=actor, operation="DEMO_SEED", object_id=MARKER,
                             details={"items": len(items), "sales_orders": len(orders),
                                      "warning": "全部配方、保质期、检验记录、供应商与批次为模拟数据。"})
        self.stdout.write(self.style.SUCCESS("演示数据已创建：20 物料、7 BOM、9 销售单、4 方案；包含共享基料在制、暂停、完成与独立草稿。"))
