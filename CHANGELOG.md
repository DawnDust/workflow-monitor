# Changelog

## 1.0.3

- 修复 GitHub Release 工作流中版本一致性校验的 Bash/Python 引号错误。
- 使用可读的 heredoc 校验标签、包和 `pyproject.toml` 三处版本，避免嵌套转义。

## 1.0.2

- 修复普通 CLI 在 Windows 英文系统传统代码页下输出中文时的编码失败。
- 统一 CLI 子进程测试的 UTF-8 解码，并兼容 Windows 8.3 短路径与规范路径的差异。

## 1.0.1

- 修复 Windows 英文环境中升级子进程无法输出中文 JSON 的编码错误。
- 启动器和升级器显式使用 UTF-8，并增加非 UTF-8 父环境回归测试。

## 1.0.0

- 将项目内复制源码的模板改为可通过 `pipx` 安装的全局工具。
- 增加 `init`、`update`、`version` 和公共 `--project` 接口。
- 增加 GitHub Release 核心包校验、版本化缓存、项目迁移与失败回滚。
- 增加受管文件冲突保护、安装清单和升级审计事件。
- 保持既有任务、探索、Dashboard、资料索引和事件 schema 1/2 兼容。
