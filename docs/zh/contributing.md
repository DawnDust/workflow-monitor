# 维护者指南

修改仓库前先阅读根目录[贡献指南](https://github.com/DawnDust/workflow-monitor/blob/main/CONTRIBUTING.md)和 `AGENTS.md`。

## 架构

Python 应用分为 core、application、infrastructure 和 UI 四层，依赖方向向内；UI 不直接拥有持久化或系统进程能力。除非另有明确批准的迁移方案，否则公共 CLI、WebView2 bridge、事件 Schema 3 和既有项目数据必须保持兼容。

## 文档

英文是默认站点，简体中文在 `docs/zh/` 中对等维护。两种语言保持相同的页面名和导航。系统界面文字可以翻译，但包含项目内容或数据记载的示例必须保持原文。

本地构建：

```powershell
python -m pip install -r requirements-docs.txt
python -m mkdocs build --strict
```

不要提交生成的 `site/` 目录。

## 发布准备

本地测试构建运行 `python scripts/build_windows_exe.py --channel test`。文件名固定为 `workflow-monitor.exe`，测试构建会在 `version` 和“关于”中标为 `<应用版本>-test.<YYYYMMDD>.<短构建 ID>`；正式发布构建使用 `--channel stable`，正式文件名也保持不变。测试渠道不能生成正式 release manifest。

同步更新程序版本和更新日志，运行 fast、full 和严格文档构建，最后运行 release 测试。`v*` 标签会触发 Windows 发布工作流，但创建标签或 Release 必须获得维护者明确批准。

## 测试版交付

测试构建默认读取根目录 `TEST_BUILD_NOTES.md`，也支持 `--notes <路径>`；必要章节不可为空。说明内嵌并生成 `dist/test-build-notes.md`，构建身份自动填写，构建结束核对源码和说明未变化。`build-notes --format markdown|json` 不需要项目环境。源码、运行构建和项目模板升级分别核对；只有 EXE 时不能宣称源码已同步。科研项目通过 `update --from-exe <路径> --check` 只读预览，通过相同命令去掉 `--check` 实际升级，不能强制覆盖定制冲突。
