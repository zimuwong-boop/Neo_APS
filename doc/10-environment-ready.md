# 10 环境准备与验证记录

日期：2026-10-09｜开发与单容器环境已准备，业务模块尚未实现。

## 1. 版本与组件

| 组件 | 本机 | 单容器 |
| --- | --- | --- |
| Python | 3.12.15，项目.venv | 3.12.15 |
| Django / DRF | 5.2.18 / 3.16.1 | 同锁定版本 |
| psycopg | 3.3.6，binary驱动 | 同版本 |
| PostgreSQL | 17.11，便携工具 | 17.11，Debian包17.11-1.pgdg12+2 |
| Node.js | 24.21.0 LTS | 构建阶段24.21.0，运行时已编译Vue |
| Vue / Vite / TypeScript | 3.5.43 / 7.3.7 / 5.9.3 | 同锁文件生成的静态产物 |
| Pinia / Router / Element Plus | 3.0.4 / 4.6.4 / 2.14.7 | 构建产物包含对应依赖 |
| pytest / Ruff | 9.1.1 / 0.16.10 | 同版本测试工具 |
| Vitest / Playwright | 4.1.11 / 1.64.0，Chromium已安装 | 从本机浏览器验证容器页面 |
| Web服务 | Django开发服务 | Gunicorn23.0.0 + WhiteNoise |

后端运行与开发依赖均已锁定，环境镜像包含测试工具；前端提交package-lock.json。Node归档校验官方SHASUMS，PostgreSQL来自官方推荐的EDB并记录下载校验值；Python由uv安装在项目工具目录。工具版本见scripts/tool-versions.json，Python/Node镜像固定摘要。

## 2. 地址、数据及账号

| 对象 | 配置 |
| --- | --- |
| 本机Vue / Django | http://127.0.0.1:5173 / http://127.0.0.1:8000 |
| 开发数据库 | 127.0.0.1:15432，neo_aps_dev；测试库独立 |
| 开发数据目录 | C:\Users\NeoWang\AppData\Local\NeoAPS\postgres-dev |
| 容器页面 | http://127.0.0.1:8080 |
| 项目容器 / 镜像 | neo-aps / neo-aps:0.1.0；本项目只有一个运行容器 |
| 容器数据库 / 卷 | 内部127.0.0.1:5432，neo_aps；neo_aps_data挂载/var/lib/neo-aps |
| 配置 | .env.dev、deploy/.env，含各自随机凭据 |
| 唯一全权限账号 | 两个环境分别为admin；随机密码在.tools/access.local.json和.tools/access.docker.json |

Docker已有其他项目的容器保留，本项目不依赖它们。开发集群初始化账号供专用开发与测试使用；容器数据库应用账号不是超级用户。数据不放OneDrive源码目录，也不写进镜像层。

## 3. 实际验证

| 检查 | 结果 |
| --- | --- |
| venv与依赖 | Python解释器指向项目.venv，pip check通过 |
| 本机数据库与迁移 | PostgreSQL连接正常，Django内置迁移完成 |
| Django与代码检查 | manage.py check、collectstatic、Ruff通过 |
| 本机数据库测试 | 3项通过：真实PostgreSQL健康查询、未知API404、缺失资源404 |
| 前端构建 | TypeScript与Vite构建通过 |
| 本机浏览器测试 | 2项通过：Vue显示连接正常，未知API正确404 |
| 镜像与容器 | 构建完成，单容器healthy，版本及pip check通过 |
| 容器数据库测试 | 独立test_neo_aps库中3项pytest通过 |
| 容器浏览器测试 | 2项通过，静态页面/API/PostgreSQL连接正常 |
| 重启持久化 | 正常重启后数据库可读、唯一admin保留、healthy |
| 备份恢复 | custom归档导出并验证可读；同容器临时空库恢复成功，admin数量1，随后删除临时库 |
| 镜像配置隔离 | 镜像未含本机.env.dev、deploy/.env或Windows.venv |
| Shell脚本 | entrypoint/backup/restore的bash语法检查通过 |

备份文件：backups/neo-aps-20261009-154039.dump，未提交源码。恢复验证没有覆盖开发或部署数据库。

## 4. 环境配置调整

- 保留本机默认Python3.14，项目独立使用3.12及.venv。
- Windows保留了55432端口，开发数据库已改为15432，配置样例同步。
- Docker Hub连接被远端关闭，改用Docker官方镜像的公共ECR源并固定摘要，不修改全局Docker配置。
- .tools保存开发组件和浏览器，use-tools.ps1只设置当前终端PATH；数据库使用LocalAppData/命名卷。

## 5. 完成边界

已建立环境骨架、健康接口、连接验证页、单账号和环境脚本。物料/BOM/销售/跨订单排产/生产报工尚未实现；业务用例T01～T37及D03仍待开发后验收。当前环境测试不代表业务功能验收。

日常命令见[项目README](../README.md)，部署设计见[09](09-development-deployment.md)。下一阶段按已有需求和数据库设计实现主数据与销售模块。
