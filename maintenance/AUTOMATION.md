# 自动填报与验证

日常使用 `start → report（按需）→ end`。短任务只需开始和结束两次写调用。旧 `state update`、`attempt update` 继续兼容，不要求重复调用。

三个入口支持 `--input-json <文件或 ->`；`-` 从标准输入读取 JSON 对象。键使用参数名称的下划线形式，例如 `current_step`、`stage_update`。同名字段不得同时从 JSON 和命令行提供。省略表示保持原值；`clear` 可列出要清空的进展、判断、阻塞、假设或结论字段。证据追加去重，不通过清空删除历史。

`start` 提供 `kind`、`scope`、`acceptance`，按需选择 `track` 和 `topic`。`stage` 默认为 `auto`：维护不关联篇章，探索关联当前分支活动篇章。`new` 同时提供 `new_stage` 对象，包含 `title`、`goal`、`acceptance`，可选 `stage_id`（默认 topic）。已有活动篇章时，必须以 `previous_stage` 提供 `status: paused` 和 `reason`；不自动结束其他篇章。`none` 用于不关联，探索必须填写 `without_stage_reason`。

`report` 接收 `current_step`、`judgment`、`blocker`、`breakpoint`、`next`，探索可同时填 `hypothesis`、`progress`、`evidence`、`conclusion`。程序将科研记录关联到 checkpoint，避免重复输入。`stage_update` 接收关联篇章的 `summary`、`revision`、`evidence`、状态等现有篇章字段。改变判断必须同时提交修订说明、概况和证据。

完成代码批次使用 `report --current-step ... --verify fast`。`end` 填写 `result`、`note`，可同时提交汇报字段；程序复用已有进展，并在尚无进展时使用结束说明。关联篇章且没有修订时必须提供 `stage_review: reviewed-no-change`。科研结果 `negative`、`inconclusive` 需要结论和证据；暂停需要 `progress` 说明原因和 `next` 给出恢复条件。

`end` 自动补齐所需的本地测试；`--check-only` 只检查，不保存填报或运行测试。项目配置 `verification_commands` 按套件设置 `argv` 参数数组、`cwd`（项目内，默认 `.`）、`timeout_seconds`（默认 1800）。未配置时明确报缺项，不猜测 Python 环境。新项目默认不配置执行器；配置受项目维护权限控制。执行器必须是本地验证命令，不包含发布操作。

有效回执不重跑；失败或超时停止，保留任务和汇报。日志保存到 `.project_hooks/verification-logs/`，命令返回日志位置；修复后再次结束即可。运行期间任务、HEAD 或软件输入改变，结果不可作为通过凭据。恢复使用 `task recover`，不要删除活动状态。

`stage list --all-branches` 查看本地已知分支的篇章版本；`stage show <ID> --source <完整ref或working-tree>` 明确查看来源。当前分支是本任务的权威版本；其他分支未合并的判断不会覆盖它。分叉判断由 AI 比较证据后在当前关联篇章追加修订，不自动合并或按时间选择。

操作后更新记录和监控视图，不增加常驻监控服务。完整输出继续可用，日常加 `--compact`。提交、推送、PR、合并及对外发布仍遵循原有授权边界。

## 按变化审阅

AI 自主处理当前任务有关的登记与审阅，无关事项留待相关任务处理。程序根据明确关联生成变化摘要，不按关键词猜测研究关系。默认 `review_interval_days: 14`：活跃对象超过 14 天未审阅，在下次相关任务提醒；暂停对象恢复时检查，封存对象只提示结构问题。内容更新时间与审阅时间分开；时间久不代表科研失败或阻塞。

`review list` 查看当前相关的待审阅对象，`review list --all` 查看全项目对象，`review show <object>` 获取变化、来源和范围令牌。读取不产生已读记录。需要主动整理时也可使用这些入口，写入仍在已有任务内进行。

`report` 或 `end` 可提交 `reviews` 数组，成员包含 `token`、`result`，按需填写 `reason`、`until`、`evidence`。结果选择 `updated`（程序关联本任务对应修改事件，可用 evidence 指定事件 ID）、`reviewed-no-change` 或 `deferred`。延后必须说明原因，`until` 使用项目时区的 ISO 日期时间，或暂停对象使用 `resume`；省略时默认延后 14 天。延期内出现新变化时重新提示。审阅覆盖令牌中的版本，读取后新增的证据继续待审阅。

关联篇章的 `end --stage-review` 自动形成同一套审阅记录；已通过 `reviews` 填报的篇章不要求再填同义确认。延后不等于完成审阅，也不能替代既有篇章、资料一致性或测试门禁。无变化的重复填报不新增记录；允许审阅后不修改内容，禁止为刷新时间而改写科学结论。

`end --check-only` 无需结果和说明，只读返回阻塞及审阅摘要。正常结束仍需 `result` 和 `note`。精简失败回执在标准错误流输出 JSON，说明本次已保存事件、最近 checkpoint、日志位置及重试方式；执行测试时该流还可能包含进度文字。保存状态为未知时先检查，不假设操作成功或重复提交。恢复仍使用 `task recover`。
