# 集成验证与交付记录

日期：2026-10-09。展示范围按01需求和用户补充实现；不含库存、设备产能、采购或食品检验放行。

## 演示进度功能追加验收

2026-10-09追加生产单手调进度，规则与接口见[15演示进度](15-demo-progress.md)。本机后端51项通过（4.89秒），Docker后端51项通过（4.72秒）；本机与Docker各4项浏览器测试通过，覆盖手调70%、跨页签销售反馈及正常报工恢复真实进度。原调味品数据与账号保留，数据守恒检查通过。更新前备份为 `backups/neo-aps-20261009-190853.dump`。新增时序图后共16张Mermaid图，已生成SVG/PNG；以下38项测试及15张图记录为追加前的首次交付记录。

## 验证方式

后端使用真实 PostgreSQL 测试数据库，不用 SQLite 代替锁与并发测试。pytest 分为环境、基础业务、生产服务和验收算例。浏览器 Playwright 使用真实 Vue/Django/数据库，独立页签验证进度轮询；测试数据使用唯一前缀并在 finally 中清理，保留 DEMO 数据。

本机完整后端 **38项测试通过**，Ruff、Django system check、迁移一致性和16份Markdown文档链接检查通过；Vue TypeScript及构建通过，4项浏览器测试覆盖基础录入和完整排产闭环。容器验证及最终快照记录见下方交付状态。

## 最终交付状态

- 本机：38项后端测试通过；4项浏览器测试通过，最终一轮9.5秒。
- Docker：38项后端测试通过（3.44秒）；4项浏览器测试通过（8.1秒）。
- 两边均导入20物料、7 BOM、9销售单。初始生产状态保持：完成2、在制1、待开工2、暂停1、草稿1。
- 数据守恒命令检查通过；Docker重启后再次检查仍保持上述状态，模拟进度和原账号未重置。
- 更新前备份：`backups/neo-aps-20261009-183110.dump`。
- 演示初始快照：`backups/neo-aps-20261009-183220.dump`，pg_dump自定义归档验证通过；包含完整业务表与调味品模拟进度。
- 本机Vue http://127.0.0.1:5173；Docker http://127.0.0.1:8080；唯一用户名admin，凭据位置见根README。

已验证的功能与原验收编号对应如下；编号用于需求追踪，不表示每个编号单独对应一个自动化函数。

| 原验收范围 | 实现与证据 |
| --- | --- |
| T01/T03/T04/T20 | 物料类型、溯源、引用保护、BOM循环与发布、登录和CSRF：test_business.py |
| T02 | 1100层循环检查、1050层完整展开：test_business.py / test_acceptance.py；页面按图关系逐层导航 |
| T05/T06/T14/T26 | 生产完整草稿CRUD、独立需求不冲抵销售、已安排销售不能删/减到占用以下：test_production.py |
| T07/T08/T17/T28/T30/T31/T33/T36 | 同成品9件跨单、T42/S14/F5共享层级、菱形多路径、多个消耗任务、按用途和冻结等级回分、数量守恒：test_production.py / test_acceptance.py |
| T09/T10/T32 | 来源量修改重建、依赖调序拒绝、等级快照不追溯、人工原因保存：服务测试及production.spec.ts真实页面调序 |
| T11/T12/T13/T16/T22 | 幂等发布/报工、两个线程并发抢占与超量保护、过期指纹、发布及报工失败原子回滚：PostgreSQL集成测试 |
| T15/T18/T19/T27/T34/T35 | 前置整单完成开工、暂停恢复、两页签进度更新、最新报告精确冲销、共享取消完整预览和开工后拒绝：服务/API及浏览器测试 |
| T21/T23 | Decimal逐来源逐边向上6位量化、负数/零/精度/溢出拒绝；100销售行5000节点性能测试 |
| T24 | 实际外键ER与UML更新；15个Mermaid图成功解析并生成SVG/PNG，源文件保留 |
| T25/T29 | 两线程同时写可能形成环的BOM，仅一项成功；克隆/发布新版本及主数据溯源改变产生不同签名，已发布历史保持旧快照；测试生产与验收文件 |
| T37 | 新批次独立创建；API验证全局五级建议、过期token拒绝、人工覆盖原因记录；在制任务不进入未开工调序、不被修改 |

## 性能实测

本机16逻辑处理器、33832931328字节物理内存（约31.5 GiB），Windows，Python3.12.15，PostgreSQL17.11。100个销售行分别展开50层，生成5000个来源节点、50个合并任务：**0.0423秒，7次SQL**。这是本机样本，未在4核8GB限配机器上复测。算法预算100000分配节点，超限明确失败，不保存截断方案。

浏览器闭环：P1销售5 kg与P3销售4 kg合并9 kg；报工6 kg后另一页签展示P1完成、P3=25%，且生产来源完成量分别5/1。刷新周期2秒，测试在3.5秒等待窗口内确认跨页签变化。

## 图形产物

- [实际ER：SVG](diagrams/rendered/13-implementation-design-01.svg) / [PNG](diagrams/rendered/13-implementation-design-01.png)
- [UML类图：SVG](diagrams/rendered/13-implementation-design-02.svg)
- [UML发布/报工时序：SVG](diagrams/rendered/13-implementation-design-03.svg)
- [UML生产状态：SVG](diagrams/rendered/13-implementation-design-04.svg)

全部15张图同时保存.mmd、.svg、.png。渲染工具为项目.tools/doc-render内Mermaid11.13.0和已有Playwright Chromium；不加入业务运行依赖、不增加容器。

重新渲染（已安装环境可直接运行最后一行）：

```powershell
. .\scripts\use-tools.ps1
npm install --prefix .tools/doc-render --save-exact mermaid@11.13.0 --no-audit --no-fund
node scripts/render-diagrams.mjs
```

## 运维与复核命令

```powershell
. .\scripts\use-tools.ps1
python -m pytest -q backend/tests -c backend/pyproject.toml
python -m ruff check backend scripts deploy/*.py
python backend/manage.py check
python backend/manage.py makemigrations --check --dry-run
python backend/manage.py check_integrity
npm --prefix frontend run build
npm --prefix frontend run test:e2e
docker exec --workdir /app/backend neo-aps python -m pytest -q --reuse-db
docker exec --workdir /app/backend neo-aps python manage.py check_integrity
```

Docker仍只有本项目neo-aps一个容器，数据卷neo_aps_data沿用，数据库端口未暴露。更新前先备份；原账号与密码不重置。Vue整包使用Element Plus，构建仍有大chunk提示；功能/类型检查通过，按需导入可作为以后优化。

当前请求中的展示系统功能已实现。真实生产使用需要另行定义库存、损耗、质量检验、食品放行、产能、权限分工和运营要求，均未作为本次展示系统功能加入。
