# Claude Code Skill 系统

本目录包含一套为 **Claude Code** 设计的 Skill 管理系统。

核心目标：**将可复用的经验、调试流程和用户偏好保存为 Skill，实现能力的持续积累。**

---

## 目录结构

```
.claude/
├── README.md                      # 本文件
├── settings.json                  # 项目级 Claude Code 配置（当前为空）
└── skills/                        # Skill 目录
    └── example/                   # 示例 Skill
        └── SKILL.md
```

---

## 什么是 Skill

Skill 是一种可复用的指令集，存储在 `skills/<name>/SKILL.md` 中。当 Claude 遇到匹配的场景时，会自动加载相应的 Skill。

### Skill 文件格式

```yaml
---
name: example-skill
description: |
  触发条件：当用户询问...时
  作用：指导 Claude 如何...
---

# 具体指令内容

1. 步骤一
2. 步骤二
3. 步骤三
```

---

## 使用方式

### 使用 skill-creator 创建 Skill（推荐）

直接告诉 Claude：

> "帮我创建一个 skill，用于..."

Claude 会调用已安装的 `skill-creator` skill，帮你标准化地创建 Skill 文件。

### 手动创建 Skill

如需手动创建，直接编辑 `skills/<name>/SKILL.md` 文件：

```bash
mkdir -p .claude/skills/my-skill
cat > .claude/skills/my-skill/SKILL.md << 'EOF'
---
name: my-skill
description: 当用户需要做 XXX 时触发
---

具体指令内容...
EOF
```

---

## 注意事项

1. **项目级配置优先**：`.claude/settings.json` 只在这个项目内生效
2. **定期整理 skills**：建议定期 review skills 目录，删除过时或不再使用的 skill

---

## 维护

如需调整行为，直接修改对应 skill 的 `SKILL.md` 文件即可。
