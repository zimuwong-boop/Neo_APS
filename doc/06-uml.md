# 06 UML 设计

版本：0.2｜2026-10-09｜已同步合并任务、多来源分配与回分关系。

本文件是设计基线，最终实体/服务及状态图见[13 实现设计](13-implementation-design.md)。所有 Mermaid 图已校验并导出 SVG/PNG。

本文件提供标准 UML 类图、时序图、状态图的 Mermaid 源码；用例图使用 PlantUML。系统结构图在 [02](02-architecture.md)，数据库 ER 图在 [03](03-database.md)，两者不冒充 UML 图。

## 1. 用例模型

标准用例图源文件：[diagrams/use-cases.puml](diagrams/use-cases.puml)。唯一参与者为全权限用户，分别访问销售、安排和产线业务视图。

| 用例 | 前置条件 | 主成功结果 |
| --- | --- | --- |
| UC-01 维护物料与 BOM | 已登录 | 保存合法分类、组件、溯源，发布无环 BOM |
| UC-02 管理销售单 | 有成品/半成品 | 草稿 CRUD 并确认需求 |
| UC-03 跨单合并与调整建议 | 销售已确认、BOM 完整 | 合并兼容需求，保留来源分配并按五级排序 |
| UC-04 发布生产安排 | 建议未过期、依赖合法 | 原子创建任务图和分配，按SALE数量占用需求 |
| UC-05 手工管理生产单 | 有合法可制造产品 | 完成生产草稿 CRUD 并可发布 |
| UC-06 执行生产与报工 | 依赖完成、合法状态 | 按冻结等级回分产量并更新各销售进度 |
| UC-07 查询进度与历史 | 已登录 | 查看SALE回分量、组件用途、报工及溯源快照 |
| UC-08 预览共享生产取消 | 完整关联组件均未开工 | 显示所有受影响订单，确认后释放分配占用 |

## 2. 领域类图

```mermaid
classDiagram
    class Item {
        +UUID id
        +String itemType
        +String traceabilityInfo
        +isManufacturable() bool
    }
    class BOM {
        +int version
        +Decimal outputQty
        +String status
        +publish()
    }
    class BOMLine {
        +Decimal quantity
    }
    class SalesOrder {
        +int priority
        +Date dueDate
        +confirm()
        +cancel()
    }
    class SalesOrderLine {
        +Decimal quantity
        +unplannedQty() Decimal
        +productionProgress() Decimal
    }
    class PlanRun {
        +String status
        +int revision
        +publish()
    }
    class PlanTask {
        +Decimal quantity
        +int effectivePriority
        +int suggestedSequence
        +int manualSequence
    }
    class PlanAllocation {
        +String kind
        +Decimal plannedQty
        +int prioritySnapshot
        +int allocationRank
    }
    class ProductionBatch {
        +String status
        +int revision
    }
    class ProductionAllocation {
        +String kind
        +Decimal plannedQty
        +Decimal fulfilledQty
        +int prioritySnapshot
        +int allocationRank
    }
    class ReportAllocation {
        +Decimal quantityDelta
    }
    class ProductionOrder {
        +Decimal plannedQty
        +Decimal completedQty
        +int effectivePriority
        +String status
        +start()
        +pause()
        +report()
    }
    class ProductionReport {
        +Decimal quantityDelta
        +String idempotencyKey
        +reverse()
    }
    class PlanningService {
        +generate()
        +expandBOM()
        +mergeCompatibleDemands()
        +topologicalSort()
    }
    Item "1" --> "0..*" BOM : 产品
    BOM "1" *-- "0..*" BOMLine : 草稿可为空
    BOMLine "0..*" --> "1" Item : 组件
    SalesOrder "1" *-- "0..*" SalesOrderLine
    SalesOrderLine "0..*" --> "1" Item
    PlanRun "1" *-- "0..*" PlanTask
    PlanTask "1" *-- "1..*" PlanAllocation
    PlanAllocation "0..*" --> "0..1" PlanAllocation : 父需求
    PlanAllocation "0..*" --> "0..1" SalesOrderLine : 来源
    PlanTask "0..1" --> "0..1" ProductionOrder : 发布为
    ProductionBatch "1" *-- "0..*" ProductionOrder
    ProductionOrder "1" *-- "1..*" ProductionAllocation
    ProductionAllocation "0..*" --> "0..1" ProductionAllocation : 父制造需求
    ProductionAllocation "0..*" --> "0..1" SalesOrderLine : 销售来源
    ProductionOrder "0..*" --> "1" Item : 产品
    ProductionOrder "1" *-- "0..*" ProductionReport
    ProductionReport "1" *-- "1..*" ReportAllocation
    ReportAllocation "0..*" --> "1" ProductionAllocation : 回分产量
    PlanningService ..> BOM
    PlanningService ..> SalesOrderLine
    PlanningService ..> PlanRun
```

类上的操作表达领域行为，具体实现统一进入服务层，不意味着让 Django 模型同时承担 API 和事务编排。

## 3. 生成、调整与发布时序图

```mermaid
sequenceDiagram
    actor U as 全权限用户
    participant V as Vue 排产页
    participant P as PlanningService
    participant W as PlanPublishService
    participant D as PostgreSQL
    U->>V: 选择销售单并生成建议
    V->>P: generate(order_ids)
    P->>D: 一致性读取需求、占用、BOM
    D-->>P: 输入快照
    P->>P: 按来源展开、兼容合并、五级拓扑排序
    P->>D: 保存候选方案与输入指纹
    P-->>V: 合并任务、来源分配、依赖图及排序解释
    U->>V: 按销售行调数量，复核顺序和日期
    V->>P: PATCH plan + revision
    P->>P: 重算来源分配及合并图，校验依赖
    P->>D: 保存调整与新 revision
    U->>V: 发布
    V->>W: publish(plan_id, revision, idempotency_key)
    W->>D: BEGIN / 图锁 / 销售行与方案锁
    W->>D: 校验幂等、输入版本、最新需求占用
    alt 已成功处理同一请求
        D-->>W: 原生产单集合
        W->>D: 结束事务
        W-->>V: 返回原结果
    else 输入过期或需求不足
        W->>D: ROLLBACK
        W-->>V: 409 PLAN_STALE
    else 合法
        W->>D: 创建批次、合并单、分配、快照及幂等结果
        W->>D: COMMIT
        W-->>V: 生产单集合
    end
```

## 4. 报工反馈时序图

```mermaid
sequenceDiagram
    actor U as 用户
    participant F as 产线页
    participant R as ReportingService
    participant D as PostgreSQL
    participant A as 安排页
    participant S as 销售页
    U->>F: 提交本次合格数量
    F->>R: report(delta, revision, key)
    R->>D: BEGIN / 查询幂等键 / 锁全部来源销售与批次
    R->>R: 校验状态、依赖与剩余数量
    R->>R: 按冻结来源等级依次填满分配
    R->>D: 写报工与回分，更新累计量及SALE对应销售状态
    R->>D: COMMIT
    R-->>F: 最新进度和本次各来源回分明细
    loop 可见页面每 2 秒一次
        A->>R: 批量查询生产进度
        R->>D: 查询已提交数据
        R-->>A: 状态、数量、更新时间
        S->>R: 批量查询销售进度
        R->>D: 仅聚合SALE分配完成量
        R-->>S: 行级已安排量与生产完成量
    end
```

## 5. 生产单状态图

```mermaid
stateDiagram-v2
    [*] --> DRAFT: 手工创建
    [*] --> RELEASED: 建议发布
    DRAFT --> RELEASED: 完整草稿批次发布
    DRAFT --> [*]: 完整草稿批次删除
    RELEASED --> IN_PROGRESS: 所有前置单完成后开工
    IN_PROGRESS --> PAUSED: 暂停
    PAUSED --> IN_PROGRESS: 恢复
    IN_PROGRESS --> COMPLETED: 累计合格量等于计划量
    COMPLETED --> PAUSED: 冲销最新未冲销报告且下游未开工
    RELEASED --> CANCELLED: 预览确认后取消未开工关联组件
    CANCELLED --> [*]
```

正常报工为 IN_PROGRESS 的内部动作；未达到计划数量时状态不变。COMPLETED 保留历史，可在符合约束时冲销，因此不画不可返回的终态。PAUSED 仍占用销售需求。

## 6. 销售单状态图

```mermaid
stateDiagram-v2
    [*] --> DRAFT
    DRAFT --> CONFIRMED: 明细合法且确认
    DRAFT --> [*]: 删除无引用草稿
    CONFIRMED --> COMPLETED: 所有行SALE回分完成量达标
    COMPLETED --> CONFIRMED: 合法冲销导致未全部完成
    CONFIRMED --> CANCELLED: 无有效生产分配后取消
    CANCELLED --> [*]
```

“未安排、部分安排、已安排”和“未开始、生产中、部分完成”是依据行级聚合得到的展示标签，不再创建多个容易相互矛盾的持久化状态字段。

## 7. 共享任务依赖示意（业务图）

```mermaid
flowchart LR
    A[订单A：F1一件，等级1] --> DA[需求：S两件]
    B[订单B：F2一件，等级5] --> DB[需求：S三件]
    DA --> S[合并生产S五件，等级1]
    DB --> S
    S -->|供给2件| F1[生产F1一件]
    S -->|供给3件| F2[生产F2一件]
```

供给依赖由 COMPONENT 分配及其父分配推导；S可有多个后续任务，不能在生产单上只保存一个父ID。
