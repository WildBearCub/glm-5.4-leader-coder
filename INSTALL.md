# 安装与使用（不想读完整 README，看这一页就够了）

**获取**：GitHub 仓库 https://github.com/WildBearCub/glm-5.4-leader-coder ——点 Code 下载 ZIP，或 `git clone`。适用对象：z.ai GLM-5.4 + GLM-5.3-Flash（ZCode、Claude Code、Codex、Cursor 等能读 Markdown 定义的工具）。

## 使用顺序

1. 下载并解压分享包，记下解压后的具体位置。
2. 把位置和下面「一键安装提示词」整段发给你的 AI，让它帮你安装。
3. 安装完成后重启软件并新建会话。
4. 以后每次使用 GLM-5.4 的新会话，开头说「leader 模式」触发 Leader 技能，后面跟你的任务要求。

## 一键安装提示词

```text
你拿到的是一个「Leader + Coder 双模型分工」分享包，用户会告诉你它所在的目录。按下面的步骤安装：

1. 先读分享包目录里的三个文件：leader-SKILL.md（Leader 技能，英文）、coder.md（Coder 子智能体定义，中文）、AGENTS-discuss-before-diverging.md（要追加进项目说明文件的一节规则，英文）。读完后向用户说明你自己运行在什么平台上（ZCode、Claude Code、Codex、Cursor 等），后续步骤按平台执行。
2. 安装 Leader 技能：如果你运行在 ZCode，把 leader-SKILL.md 原样放到 ~/.agents/skills/leader/SKILL.md；如果运行在其他平台，把它换成你所在平台的技能格式再放，并向用户说明你改了什么。
3. 安装 Coder 子智能体：如果你运行在 ZCode，把 coder.md 原样放到 ~/.zcode/agents/coder.md；如果运行在其他平台，把它的正文作为系统提示词，换成你所在平台的子智能体格式再放，并向用户说明你改了什么。注意 ZCode 下如果项目级 .zcode/agents/coder.md 已存在，它会盖过用户级这份，两处只能留一份。
4. 把 AGENTS-discuss-before-diverging.md 里那一节规则追加到你项目的说明文件顶部、项目专属规则之前：ZCode、Codex、Cursor 通常是 AGENTS.md，Claude Code 通常是 CLAUDE.md。如果目标文件里已经有同名小节，跳过，不重复追加。
5. 覆盖任何已存在的目标文件之前，先把它原样复制一份 .bak 备份，并告诉用户备份文件在哪里。
6. coder.md 里的「安全规矩」和「测试配方」是按作者自己的项目写的（环境变量、路径、测试命令），需要按用户的项目替换。把这些需要替换的地方逐条列出来问用户，按用户的回答修改；用户没有回答的不要擅自改。
7. 全程不要 git commit、不要 git push。装完后输出一张清单：每个文件装到了哪里、改了什么、哪些在等用户确认；并提醒用户：ZCode 下智能体定义要到下一个会话才生效。
```

装完之后想了解原理、数据和适用范围，再回来看 [README.md](README.md)。
