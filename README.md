# Claude Code 自我进化 Skill 系统

本目录包含一套为 **Claude Code** 设计的 Skill 自我进化机制，灵感来源于 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 的 `_spawn_background_review` + `skill_manage` 设计。

核心目标：**让 Claude 在完成任务后自动回顾对话，把可复用的经验、调试流程和用户偏好保存为 Skill，实现能力的持续积累。**

---

## 目录结构

```
.claude/
├── README.md                      # 本文件
├── settings.json                  # 项目级 Claude Code Hook 配置
├── skills/
│   └── self-improving/
│       ├── SKILL.md               # 核心 Skill：指导 Claude 如何判断并保存经验
│       └── evals/
│           └── evals.json         # 测试用例（可选）
└── scripts/
    ├── skill_manager.py           # Skill 管理 CLI（create/edit/patch/write_file/delete）
    └── review_skills.py           # Stop Hook 后台执行器
```

---

## 核心组件

### 1. `self-improving` Skill

**文件：** `skills/self-improving/SKILL.md`

当 Claude 被触发 review 时，会加载这个 Skill。它定义了：

- **何时保存**：任务涉及 3+ 个 tool call、经历了试错、发现新项目模式、或被用户纠正/表达偏好
- **保存到哪里**：
  - 项目特有知识 → 当前项目的 `.claude/skills/`
  - 跨项目通用知识 → `~/.claude/skills/`
  - 用户行为/风格偏好 → `~/.claude/CLAUDE.md`
- **怎么保存**：直接调用 `Read`/`Write`/`Edit` 工具，不空谈
- **何时拒绝**：不符合阈值时，必须回复 `Nothing to save.` 并停止

### 2. `skill_manager.py`

**文件：** `scripts/skill_manager.py`

复刻了 Hermes Agent 的 `skill_manage` 工具，提供命令行接口管理 Skill：

```bash
python3 .claude/scripts/skill_manager.py create --name my-skill --content "---\n..."
python3 .claude/scripts/skill_manager.py edit   --name my-skill --content "---\n..."
python3 .claude/scripts/skill_manager.py patch  --name my-skill --old-string "..." --new-string "..."
python3 .claude/scripts/skill_manager.py delete --name my-skill
```

特点：
- 带 YAML frontmatter 校验
- 限制 `SKILL.md` 最大 100,000 字符
- 原子写入（`tempfile` + `os.replace`）
- 防止路径逃逸攻击

### 3. `review_skills.py`

**文件：** `scripts/review_skills.py`

`Stop` Hook 的轻量级执行器。它不做任何外部 LLM API 调用，而是：

1. 读取 `~/.claude/history.jsonl` 获取最近会话
2. 用正则规则检测信号（失败、纠正、模式发现、多命令交互）
3. **有信号时**：调用 `claude -p --continue` 让 Claude 自己回顾并决定是否更新 Skill
4. **无信号时**：直接跳过，零开销

---

## 触发机制：双保险

### 主触发器：`TaskCompleted` Hook

当使用 `TaskCreate` 创建的任务标记为 `completed` 时，Claude Code 会自动发送 review prompt，让当前 Claude 实例直接判断是否需要保存 Skill。

**优点：**
- 无需启动子进程
- 能利用完整的当前会话上下文
- 最可靠、最及时

### 兜底触发器：`Stop` Hook

当 Claude Code 会话正常结束（`Ctrl+C`、输入 `/quit`、EOF）时，后台运行 `review_skills.py`：

- 如果检测到 skill-worthy 信号 → 启动 `claude -p --continue` 执行 review
- 如果没有 → 直接退出

**注意：** 直接关闭终端窗口可能导致 Hook 不被执行，因此 `Stop` 仅作为兜底，不能替代 `TaskCompleted`。

---

## 工作流程

```
用户发起复杂任务 ──► Claude 使用多个工具完成 ──► TaskCompleted 触发
                                                        │
                                                        ▼
                                            加载 self-improving SKILL.md
                                                        │
                                            ┌───────────┴───────────┐
                                            ▼                       ▼
                                      满足保存条件              不满足条件
                                            │                       │
                                            ▼                       ▼
                                    创建/更新 Skill           Nothing to save.
                                            │
                                            ▼
                                    .claude/skills/<name>/SKILL.md
                                    或 ~/.claude/skills/<name>/SKILL.md
```

---

## 快速测试

### 测试 1：应该被拒绝的简单任务

问：
> "当前目录有哪些 Python 文件？"

预期：`TaskCompleted` 触发后，Claude 回复 `Nothing to save.`

### 测试 2：应该保存的复杂任务

用 `TaskCreate` 发起：
> "我想扩展 hermes-agent2 的 gateway hook 系统，让它支持 `agent:interrupt` 事件。当用户在 agent 运行过程中按 `Ctrl+C` 打断时，触发这个 hook。请帮我找到相关代码、分析实现方案、然后直接修改。"

预期：任务完成后，Claude 经过 review，在 `.claude/skills/` 下创建类似 `hermes-add-hook-event` 的 Skill。

### 测试 3：Stop Hook 兜底

完成一次复杂对话后，按 `Ctrl+C` 退出 Claude Code。然后检查：

```bash
cat ~/.claude/.pending-reviews.md
cat ~/.claude/.skill_review_state.json
ls .claude/skills/
```

---

## 模式差异：与 Hermes Agent 的对比

| 维度 | Hermes Agent | 本系统 |
|------|-------------|--------|
| 触发时机 | 每 10 个 tool iteration | `TaskCompleted` + `Stop` |
| review 上下文 | 同一进程 fork `AIAgent`，完整 messages | `TaskCompleted` 用当前上下文；`Stop` 读 history.jsonl |
| 执行方式 | 后台 `threading.Thread` 调内部 API | `TaskCompleted` 零额外 API；`Stop` 用 `claude -p --continue` |
| skill 写入 | `skill_manage` 工具 | `skill_manager.py` CLI + Claude 直接写文件 |

差异主要来自 **Claude Code 的环境限制**（没有暴露 tool iteration 计数器、history 不完整、外部 API token 可能受限）。本系统是在这些约束下的最实用版本。

---

## 注意事项

1. **不要手动编辑 `.claude/skills/self-improving/SKILL.md` 中的判断标准**除非你清楚影响——标准太宽松会导致 skill 膨胀，太严格会漏掉有价值的经验。
2. **项目级配置优先**：`.claude/settings.json` 只在这个项目内生效，不会影响你其他项目的 Claude Code 配置。
3. **生成 skills 建议查看后提交**：自动生成的 skill 可能需要人工润色 frontmatter 的 `description`，以提升触发准确率。

---

## 维护

如需调整行为，可修改以下文件：

- `.claude/skills/self-improving/SKILL.md` — 修改判断标准和保存协议
- `.claude/scripts/review_skills.py` — 修改 Stop Hook 的 gating 规则
- `.claude/settings.json` — 修改 Hook 配置
