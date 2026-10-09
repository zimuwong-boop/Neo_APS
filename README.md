# Neo APS

Django + Vue + PostgreSQL 定制化排产展示系统。已实现物料/BOM/销售单/生产单 CRUD、跨订单合并排产、五级优先级、人工调整、发布、报工回分、冲销、共享取消及销售进度轮询。已准备调味品演示数据；使用步骤见[演示脚本](doc/12-condiment-demo.md)，最终设计与 UML/ER 见[实现设计](doc/13-implementation-design.md)。

## 本机开发

环境当前已启动：Vue **http://127.0.0.1:5173**，Django **http://127.0.0.1:8000**，健康检查 `/api/v1/health/`。

在项目根目录的PowerShell中配置当前终端工具路径，再查看或管理服务：

```powershell
. .\scripts\use-tools.ps1
.\scripts\dev.ps1 Status
.\scripts\dev.ps1 Stop
.\scripts\dev.ps1 Start
```

`python`指向项目.venv，Node/npm/psql使用.tools内工具，不修改系统PATH。开发依赖已安装，前端依赖位于frontend/node_modules。启动脚本用隐藏后台进程运行Django和Vite，日志在.tools/logs。本机数据库neo_aps_dev位于127.0.0.1:15432，数据在 `%LOCALAPPDATA%\NeoAPS\postgres-dev`。

需要前台调试时，先Stop，再单独运行后端和前端；避免同端口重复启动。

## 单容器运行

唯一项目容器neo-aps当前已启动：**http://127.0.0.1:8080**。Django/Gunicorn与PostgreSQL在容器内，Vue使用构建产物；数据在命名卷neo_aps_data，数据库端口不暴露宿主机。

```powershell
.\scripts\deploy.ps1 Status
.\scripts\deploy.ps1 Stop
.\scripts\deploy.ps1 Start
.\scripts\deploy.ps1 Backup
```

Docker Desktop引擎需运行。脚本不删除现有容器或数据卷，备份导出至backups。

本机与容器各有唯一用户名 `admin` 的全权限账号，随机密码分别保存于.tools/access.local.json与.tools/access.docker.json；未写入源码、文档或镜像。管理页分别为 `http://127.0.0.1:8000/admin/` 和 `http://127.0.0.1:8080/admin/`。配置文件为.env.dev和deploy/.env，重启沿用原配置。

## 验证

```powershell
. .\scripts\use-tools.ps1
python -m pytest -q backend/tests -c backend/pyproject.toml
npm --prefix frontend run build
npm --prefix frontend run test:e2e
```

浏览器测试默认访问本机5173，测试容器时设置当前终端 `E2E_BASE_URL=http://127.0.0.1:8080`。后端测试使用独立测试库；容器已准备test_neo_aps，可执行 `docker exec --workdir /app/backend neo-aps python -m pytest -q --reuse-db`。

锁定文件为backend/requirements*.txt、frontend/package-lock.json和scripts/tool-versions.json。Docker基础镜像已锁定摘要。环境版本、实际检查结果和当前完成边界见[环境准备记录](doc/10-environment-ready.md)。

## 调味品演示

本机和 Docker 各有20个物料、7个生效BOM、9张销售单及4个方案。登录后进入“排产与生产”，查看共享酱基料在制、独立草稿和暂停批次。再次导入不会覆盖当前进度：

生产单的“调演示进度”支持0～99.99%，会同步关联销售展示；真实报工、状态与台账保持独立，新的报工/冲销自动清除演示值。操作和规则见[演示进度说明](doc/15-demo-progress.md)。

```powershell
python backend/manage.py seed_demo
docker exec --workdir /app/backend neo-aps python manage.py seed_demo
```

标准只用于产品分类参考，配方、保质期、供应商、批次和检验记录全部为模拟。无需额外容器、Redis或消息队列。展示系统不计算库存、产能、损耗或检验放行。
