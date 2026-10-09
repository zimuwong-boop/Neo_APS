# 05 接口与页面设计

版本：0.2｜2026-10-09｜已同步跨订单合并与五级优先级。

## 1. API 约定

前缀 `/api/v1/`，JSON 请求响应，会话认证 + CSRF。所有写接口要求登录；仅一个全权限账号。数量用字符串返回（如 `"10.500000"`），前端不得用浮点加法作为保存依据。日期 ISO 8601，时间带时区。

列表分页采用 `page/page_size`，默认 20，最大 100；支持明确的过滤和排序白名单。单资源响应含 revision；PATCH、状态操作与删除携带当前 revision，过期返回 409。发布、取消、报工、冲销等提交 `Idempotency-Key`；同键不同载荷报错，同键同载荷返回原成功响应。

成功状态：查询/编辑 200，创建 201，删除 204。错误：400 字段或业务校验、403 未认证/无权限/CSRF（会话认证）、404 不存在、409 冲突/重复键载荷不符/状态或版本过期、503 计算资源预算超限。后端统一响应例如：

```json
{
  "code": "BOM_CYCLE",
  "message": "BOM 存在循环：S1 → S2 → S1",
  "fields": {"component_item_id": ["该组件会形成循环"]},
  "request_id": "request-uuid"
}
```

## 2. 端点清单

| 模块 | 方法与路径 | 行为 |
| --- | --- | --- |
| 登录 | GET `/auth/csrf/`；POST `/auth/login/`；POST `/auth/logout/`；GET `/auth/me/` | 建立 CSRF、登录、退出、当前账号 |
| 物料 | GET/POST `/items/` | 列表、新增，可按类型筛选 |
| 物料 | GET/PATCH/DELETE `/items/{id}/` | 查看、修改、删除未引用物料 |
| BOM | GET/POST `/boms/`；GET/PATCH/DELETE `/boms/{id}/` | 草稿 CRUD；PATCH 含完整直接组件列表，事务替换 |
| BOM | POST `/boms/{id}/publish/`；POST `/boms/{id}/clone/` | 校验并生效；从历史版本复制新草稿 |
| BOM | GET `/boms/{id}/children/`；POST `/boms/{id}/explode/` | 查看直接组件；按数量展开预览，不创建生产单 |
| 销售单 | GET/POST `/sales-orders/` | 列表、新增草稿（含明细） |
| 销售单 | GET/PATCH/DELETE `/sales-orders/{id}/` | 详情、编辑及合法删除 |
| 销售单 | POST `/sales-orders/{id}/confirm/`；POST `/sales-orders/{id}/cancel/` | 确认；无有效生产分配才可取消，有分配时返回影响入口 |
| 销售单 | GET `/sales-orders/{id}/progress/` | 行级需求、SALE安排量/已回分量、共用生产单与内部组件进度 |
| 建议 | POST `/plans/generate/`；GET `/plans/`；GET `/plans/{id}/` | 根据订单 ID 列表生成、查看候选方案 |
| 建议 | GET `/plans/{id}/tasks/`；GET `/plans/{id}/allocations/` | 分页任务/来源分配；返回前置/后续任务ID列表，不用单一parent_task_id |
| 建议 | PATCH `/plans/{id}/` | 按来源行改本次数量，重算合并；保存人工顺序、日期和调整原因 |
| 建议 | POST `/plans/{id}/publish/`；POST `/plans/{id}/discard/` | 原子发布整个调整后方案；废弃候选方案 |
| 生产单 | GET/POST `/production-orders/` | 列表；手工建最终产品草稿，支持多个销售行或独立生产，并生成完整批次 |
| 生产单 | GET/PATCH/DELETE `/production-orders/{id}/` | 查询/编辑；草稿数量或来源变动重算整个批次；DELETE需预览并确认删除整个草稿批次 |
| 生产单 | POST `/production-orders/{id}/release/` | 发布该单所属完整手工草稿批次，不允许孤立发布共享任务 |
| 生产单 | GET `/production-orders/{id}/allocations/` | 查询各来源订单/用途/等级快照/计划量/已回分量 |
| 生产单 | GET `/production-orders/{id}/impact/` | 返回删除或取消影响预览：批次/关联组件、受影响订单、释放量、版本指纹 |
| 生产单 | POST `/production-orders/resequence/` | 原子调整全局未开工任务顺序，检查依赖 |
| 生产单 | POST `/production-orders/{id}/start/`；`pause/`；`resume/`；`cancel/` | 状态机操作；cancel携带影响指纹和确认范围，仅允许完整未开工关联组件 |
| 报工 | GET/POST `/production-orders/{id}/reports/` | 流水查询、正数增量报工 |
| 报工 | POST `/production-reports/{id}/reverse/` | 只冲销最新未冲销正报告及其原回分；原因必填，下游已开工时拒绝 |
| 进度 | POST `/progress/query/` | 只读批量查询销售/生产 ID，最多 100 个；供轮询使用 |
| 审计 | GET `/audit-logs/` | 按对象查询操作历史 |
| 部署健康 | GET `/health/` | 仅返回服务/数据库就绪结果；供容器健康检查，无须登录且不返回业务数据或连接配置 |

所有删除和取消的具体限制以 [需求规格](01-requirements.md) 为准。不能提供任意 PATCH status/完成数量接口绕过状态机。销售 PATCH 按明细 ID 差异更新，已有生产引用行不得通过全量替换被删除重建。

## 3. 核心载荷示例

创建销售草稿：

```json
{
  "customer_name": "演示客户 A",
  "priority": 1,
  "due_date": "2026-10-20",
  "notes": "优先演示订单",
  "lines": [{"line_no": 1, "item_id": "finished-item-uuid", "quantity": "5.000000"}]
}
```

生成建议：`{"sales_order_ids": ["order-a-uuid", "order-b-uuid"]}`。默认跨单合并本次兼容需求，返回方案ID、revision、合并任务、最高来源等级、各等级数量分布、来源分配、前置/后续依赖、原料汇总。大结果分别分页获取任务及分配。

修改建议：

```json
{
  "revision": 1,
  "demands": [{"sales_order_line_id": "sale-line-uuid", "quantity": "3.000000"}]
}
```

quantity=0 表示排除该行。数量变化重建整个方案的合并任务、分配和依赖，返回新revision及 requires_sequence_review=true。随后按新任务ID另一次PATCH提交 task_changes（manual_sequence、target_end、line_label、adjustment_reason）及 reviewed_revision，检查完整排序后解除复核标记；不得在同一次数量变更中引用旧任务ID。

手工新增生产草稿示意：`{"item_id":"product-uuid","mode":"SALES","allocations":[{"sales_order_line_id":"line-a","quantity":"5.000000"},{"sales_order_line_id":"line-b","quantity":"4.000000"}]}`。各销售行产品必须一致，最终产品总量由分配求和。独立模式 STANDALONE 传 quantity 和 priority，不传销售分配。两者均自动展开和合并组件。

报工：请求头 `Idempotency-Key: <UUID>`，请求体 `{"revision": 3, "quantity_delta": "6.000000", "reason": "本次完成"}`。返回生产单累计量、状态、revision、本次 allocation_deltas 及所有受影响销售行进度。例如A需求5等级1、B需求4等级3，报6返回A+5/B+1，不把6重复返回给两单。同幂等键先查成功记录，不因旧revision拒绝重试。

取消请求包含当前revision、impact_fingerprint、confirmed_order_ids和reason。服务端锁内核对影响范围与预览完全一致，变化则409；不能以客户端传来的范围决定遗漏哪些共享任务。销售取消有有效占用时返回 ORDER_HAS_ALLOCATIONS，不自动取消共享生产或其他销售单。

## 4. 页面信息架构

| 页面 | 主要内容与操作 |
| --- | --- |
| 工作台 | 待排销售行、待开工/生产中/暂停生产单、最新报工；跳转处理 |
| 物料管理 | 编码、名称、类型、单位、溯源信息、启用状态；新增/编辑/停用/删除 |
| BOM 管理 | 产品筛选、版本列表、直接组件编辑、按需展开多级树、发布及复制版本 |
| 销售订单 | 优先级、交期、状态筛选；新增/编辑/删除草稿；确认/取消 |
| 销售详情 | 需求、SALE安排量/回分完成量、待排量、各批次分配和内部组件进度 |
| 排产建议 | 多单合并；五级选择器；任务最高来源等级及等级数量分布；来源量调整、依赖图、原料汇总、人工排序和发布 |
| 生产安排 | 合并任务表、多个前置/后续依赖、来源订单分配面板、手工生产CRUD、取消影响预览 |
| 产线报工 | 可开工任务、阻塞原因、开工/暂停/恢复、本次合格量、本次回分明细、流水及冲销 |

首版以列表和依赖图表达安排，共享节点只表示一张生产单，可在多个来源视图引用；不引入暗示精确工时的甘特图。所有页面共用同一账号及导航。

## 5. 关键交互规则

- BOM 组件选择器只展示半成品与原料；当前父物料和导致循环的候选可提示，但后端仍做完整校验。
- 销售与手工生产产品选择器只显示成品、半成品。
- “生成建议”和“发布生产单”是两个动作；发布前展示合并前来源数量、合并后任务数量、等级分布和全局顺序变化。
- 建议表并列展示“系统建议顺序”和“人工安排顺序”，修改高亮；过期方案显示原因和重新生成入口。
- 生产页显示“等待子件完成”的阻塞原因，不能用按钮置灰而没有解释。
- 销售页分别显示已安排与生产完成，不把排产完成表示为生产完成；子件进度单独展开。
- 报工表单标注“本次新增合格数量”，显示剩余可报数量，避免用户误填累计数。
- 删除/取消展示完整批次或关联组件及所有受影响订单；明确其他订单仅释放生产占用，不取消销售。失败保留输入。
- 五级使用固定下拉框：1最高、2高、3普通、4低、5最低；显示来源等级与发布快照差异，不引入复杂评分。
- 自动刷新不覆盖正在编辑的表单；发现 revision 变化提示重新载入，后台进度可单独更新。
