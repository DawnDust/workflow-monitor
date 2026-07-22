# Project Maintenance Template

这是一个只使用项目内 Hook 的可克隆维护模板，不需要安装 Codex 插件，也不会创建或使用全局 Codex/Git Hook。

## 开始使用

```powershell
git clone <repository-url> <new-project>
Set-Location <new-project>
python -m project_hooks install
python -m project_hooks check
```

普通 `git clone` 出于安全原因不会复制 `.git/config`，因此每个新 clone 或 worktree 需要运行一次 `install`。该命令只为当前仓库设置 `core.hooksPath=.githooks`；Hook 文件本身已在版本控制中。

之后由仓库内 [AGENTS.md](./AGENTS.md) 约束 Codex：每次任务先读取维护上下文并执行 `start`，结束前归档并执行 `end`。

## 常用命令

```powershell
python -m project_hooks install
python -m project_hooks check
python -m project_hooks status
```

项目维护文档索引见 [maintenance/README.md](./maintenance/README.md)。
