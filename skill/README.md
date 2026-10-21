# 安装为 AI Skill

`SKILL.md` 是一份**模型可读的操作手册**，可直接放入支持 Skill 机制的 AI 工具中，让 AI 遇到同类任务时自动按这套流程执行。

## 什么是 Skill

Skill 是给 AI 的结构化上下文：一段带 frontmatter 的 Markdown，AI 在匹配到相关任务时会自动加载它。

```markdown
---
name: job-automation-pipeline
description: Use when building or debugging browser-automation job hunting pipelines...
---

# 正文：何时用、怎么做、坑在哪
```

关键点：
- **`description` 决定何时触发**：AI 会读这段判断当前任务是否匹配
- **正文决定怎么做**：浓缩的决策依据、代码骨架、排查表

## 安装方式

### DSH（DeepSeek Harness）

```bash
# 1. 创建 skill 目录
mkdir -p ~/.dsh/skills/job-automation-pipeline

# 2. 复制 SKILL.md
cp skill/SKILL.md ~/.dsh/skills/job-automation-pipeline/SKILL.md
```

重启会话后，`job-automation-pipeline` 会出现在可用 skill 列表中。

### Claude Code / 其他支持 Skill 的工具

放到工具约定的 skills 目录即可。常见路径：

| 工具 | 路径 |
|---|---|
| DSH | `~/.dsh/skills/<name>/SKILL.md` |
| Claude Code | `~/.claude/skills/<name>/SKILL.md` |
| 项目级 | `<project>/.skills/<name>/SKILL.md` |

### 作为普通提示词使用

不支持 Skill 机制的工具，也可以直接把 `SKILL.md` 正文贴进对话作为上下文——它本身就是一份完整的操作手册。

## Skill 与文档的分工

| 载体 | 面向 | 内容 | 体量 |
|---|---|---|---|
| `skill/SKILL.md` | AI | 决策依据 + 代码骨架 + 排查表 | ~300 行 |
| `docs/*.md` | 人 | 完整设计说明 + 细节推导 | ~1000 行 |
| `src/*` | 机器 | 可运行实现 | ~1500 行 |

**为什么分三层**：AI 的上下文预算有限，SKILL.md 只保留「不看就会做错」的部分；需要细节时，它知道去哪找（仓库里的 docs 和 src）。

## SKILL.md 的内容取舍原则

写 Skill 时遵循以下原则（来自实际踩坑）：

1. **只写反直觉的部分**
   - ✅ 写：「合成 click 无效，必须用 CDP 派发真实鼠标事件」
   - ❌ 不写：「用 querySelector 选择元素」（这是常识）

2. **写清「为什么」而不只是「做什么」**
   - ✅ 写：「坐标必须用中心点，用左上角会点到相邻元素」
   - ❌ 不写：「使用 getBoundingClientRect」

3. **约束放在动作之前**
   - 风控限制、合规提醒要出现在对应操作之前，不能放最后

4. **给可执行的骨架，不给完整实现**
   - 代码片段控制在 10-30 行，能表达关键逻辑即可
   - 完整实现指向仓库

5. **排查表比教程有用**
   - 「症状 → 原因 → 解决」三列表格，比长篇讲解更容易被 AI 用上

## 自定义

`SKILL.md` 里的规则表是按通用场景写的。实际使用时需要按自己的情况改：

| 位置 | 改什么 |
|---|---|
| 规则引擎表格 | 你的硬性排除条件 |
| `SALARY_FLOOR` | 你的薪资底线 |
| 风控参数 | 你的平台账号承受度 |
| 消息处理策略 | 你的求职方向与话术 |

改完后建议先小规模验证，再批量跑。

## 验证 Skill 是否生效

1. 开一个新会话
2. 说「帮我从招聘网站采集岗位并筛选」
3. 观察 AI 是否主动加载 `job-automation-pipeline` skill
4. 检查它的第一步是否是「先确认页面结构」而非直接写代码
