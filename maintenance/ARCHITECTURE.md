# Workflow Monitor 架构

## 目标

本项目采用克制的四层结构，以高内聚、低耦合和明确依赖方向支持长期维护。目录不是按文件大小划分，而是按变化原因和外部依赖划分。

```text
ui → application → core
 ↓
infrastructure
```

`composition.py` 是唯一同时组装应用服务、基础设施实现和 UI 数据提供器的位置。根目录的 `cli.py`、`launcher.py` 与 `windows_entry.py` 只是稳定入口。

## 各层职责

| 层 | 职责 | 禁止事项 |
|:---|:---|:---|
| `core/` | 事件协议、生命周期、分支、动作和资料的纯规则 | 文件、进程、SQLite、网络、UI、外层导入 |
| `application/` | 用例编排、端口协议、动作和查询服务 | 导入 infrastructure 或 ui、解析命令行、执行 SQL |
| `infrastructure/` | JSONL、SQLite、Git、更新、诊断和项目文件实现 | 导入 ui |
| `ui/cli/` | 参数解析、命令适配和文本/JSON 输出 | 把 `argparse.Namespace` 传入 application |
| `ui/web/` | WebView2 桥接、Web 投影和静态资源 | 导入 CLI、暴露任意命令或任意文件读取 |
| `ui/windows/` | 便携初始化、加载壳和冻结 EXE 入口 | 承载业务规则 |

## 稳定契约

- `workflow-monitor.exe` 文件名、CLI 命令和输出保持兼容。
- `maintenance/events.jsonl` 是追加式永久事件源；Schema v1、v2、v3 均可读取。
- SQLite Schema v4 是可重建投影，不是唯一事实来源；v1-v3 事件保持只读兼容。
- Web 桥接只保留白名单方法，Dashboard 不执行生命周期写入。
- 根入口允许兼容导入；其他旧的扁平模块路径不是公共 API。

## 新代码放置规则

1. 不访问外部环境的规则优先放入 `core`。
2. 一项用户操作需要协调多个端口时放入 `application`。
3. SQL、文件、Git、网络和操作系统调用放入 `infrastructure`。
4. 参数、窗口、HTML、CSS、JavaScript 和展示格式放入 `ui`。
5. 不为单一实现预先增加工厂或接口；只有跨层边界才定义 Protocol。
6. 修改边界后必须运行架构测试、快速测试和完整测试。
