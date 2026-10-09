# 08 公开项目与技术资料调研

调研日期：2026-10-09。仅查看公开项目和官方文档；本次未克隆第三方仓库、未运行其演示，也未复制其实现代码。以下“采用方式”是本项目的设计判断，不是来源对本项目的保证。

## 1. 相关开源系统

| 项目 | 核实资料 | 值得参考 | 本项目采用方式 |
| --- | --- | --- | --- |
| ERPNext | [BOM 官方说明](https://docs.frappe.io/erpnext/bill-of-materials)、[生产计划](https://docs.frappe.io/erpnext/production-plan)、[工单](https://docs.frappe.io/erpnext/work-order)、[BOM 模型源码](https://github.com/frappe/erpnext/blob/develop/erpnext/manufacturing/doctype/bom/bom.json) | 多级物料结构、销售需求到生产计划再到工单的流程 | 参考业务对象分离；按本项目三类物料规则重建领域模型，不引入完整 ERP |
| frePPLe | [GitHub 仓库](https://github.com/frePPLe/frepple)、[功能列表](https://github.com/frePPLe/frepple/blob/master/doc/features.rst)、[计划运行命令](https://github.com/frePPLe/frepple/blob/master/doc/command-reference.rst) | 生产计划与建议分析；其后端使用 Python、Django、PostgreSQL，技术方向有参考性 | 参考需求、供给、计划分层和人工调整思想；本项目使用确定性展开/排序，不接入完整求解引擎 |

ERPNext 官方工单说明明确描述了从基于销售订单的生产计划创建工单的路径，适合参考本项目的“建议”和“执行单据”分离。[来源](https://docs.frappe.io/erpnext/work-order)

frePPLe 的功能范围覆盖生产规划、产能和短期排程；与本项目明确不核算产能的演示范围相比更广。因此首版自行实现优先级与 BOM 算法，是控制范围的设计选择。[来源](https://github.com/frePPLe/frepple/blob/master/doc/features.rst)

不直接套用这些系统的物料分类语义：本项目的“成品禁止作为任何 BOM 子项”是用户特定约束，必须在自己的领域服务中实现。

## 2. 技术依据

| 主题 | 官方资料 | 对本设计的作用 |
| --- | --- | --- |
| Django 版本系列 | [Django 5.2 发布说明](https://docs.djangoproject.com/en/5.2/releases/5.2/) | 选择 LTS 系列，初始化阶段锁定支持的补丁版及依赖 |
| Vue | [Vue 官方介绍](https://vuejs.org/guide/introduction.html) | 使用 Vue 3 的组件化方式构建页面 |
| PostgreSQL 递归查询 | [WITH / Recursive Queries](https://www.postgresql.org/docs/current/queries-with.html) | 任意深度关系遍历与环路处理的数据库能力参考 |

动态文档和 GitHub 默认分支可能更新。若后续需要实际移植代码，再记录确切 commit、文件范围及适用许可证要求；当前只参考公开业务和设计思想，不作许可证兼容性结论。

## 3. 调研结论

现有公开项目可作为“多级 BOM → 生产计划 → 工单 → 进度”的结构参考。本项目首版进一步明确跨订单合并生产、简单五级优先级、来源分配与报工回分；这些规则以本项目需求和04算法文档为准。库存、工序和有限产能求解保留为后续需求，不在首版引入。本次是需求设计更新，未新增第三方代码或重新运行参考系统。
