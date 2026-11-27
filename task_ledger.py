# ZCode 钩子：任务台账注入 + 总目标行 + 上下文护栏。
# 登记在 ~/.zcode/cli/config.json 的 hooks.events.UserPromptSubmit 下（写法见 INSTALL.md）。
# 每条用户消息把三样带进主会话的上下文，不靠模型记得去看（压缩之后照样在眼前）：
#   1) 台账第一行 <!-- 目标: … --> 里的总目标——接手的会话每轮都先看见「为什么干」，不会只顾着清剩余任务
#   2) docs/任务台账-*.md 里没关闭的行（状态不以 已提交/已作废/已撤销 开头），封顶 15 行
#   3) 队长自己的上下文有多大：取用量库里本会话主会话最近一次请求的输入 token
#      到提醒线（SOFT_TOKENS）提示先写恢复要点；到强制交接线（HARD_TOKENS）提示立刻交接（2026-11-27 用户令：交接线放在 55 万到 60 万之间）
# 只读、不写任何文件；任何异常都静默退出，绝不拖累提示。
import datetime
import glob
import json
import os
import re
import sqlite3
import sys

CLOSED = ("已提交", "已作废", "已撤销")
MAX_ROWS = 15
SOFT_TOKENS = 400_000   # 提醒线：派下一单之前先把恢复要点写好
HARD_TOKENS = 550_000   # 强制交接线：不再派单，写交接，换新会话


def _arg(flag, default=""):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def parse_ledger(path):
    """返回 (meta, rows)。meta 取正文里的 `<!-- key: value -->`；rows 是表格行，每行是「表头→单元格」的字典。"""
    meta, rows, header = {}, [], None
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            m = re.match(r"<!--\s*([^:：]+?)\s*[:：]\s*(.+?)\s*-->", s)
            if m:
                meta[m.group(1).strip()] = m.group(2).strip()
                continue
            if not s.startswith("|"):
                continue
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue  # 表头的分隔行
            if header is None:
                if cells and cells[0] == "单号":
                    header = cells
                continue
            if len(cells) < 4:
                continue
            rows.append(dict(zip(header, cells + [""] * (len(header) - len(cells)))))
    return meta, rows


def context_line():
    """队长当前上下文：用量库里本会话主会话最近一次请求的输入 token（含缓存重读）。取不到返回空串。"""
    try:
        sid = _arg("--session") or os.environ.get("CLAUDE_SESSION_ID") or ""
        if not sid:
            return ""
        db = os.path.join(os.path.expanduser("~"), ".zcode", "cli", "db", "db.sqlite")
        if not os.path.isfile(db):
            return ""
        con = sqlite3.connect("file:" + db.replace("\\", "/") + "?mode=ro", uri=True)
        try:
            row = con.execute(
                "SELECT input_tokens FROM model_usage WHERE session_id=? AND query_source='main_turn' "
                "AND input_tokens IS NOT NULL ORDER BY rowid DESC LIMIT 1", (sid,)).fetchone()
        finally:
            con.close()
        if not row or not row[0]:
            return ""
        n, k = int(row[0]), int(row[0]) // 1000
        if n >= HARD_TOKENS:
            return ("【上下文超线】队长当前约 " + str(k) + "k，已到强制交接线 " + str(HARD_TOKENS // 1000)
                    + "k：本轮不要再派新单、不要读大段输出；立刻写好恢复要点与换任提示词，然后告诉用户换新会话。")
        if n >= SOFT_TOKENS:
            return ("【上下文提醒】队长当前约 " + str(k) + "k（提醒线 " + str(SOFT_TOKENS // 1000) + "k，交接线 "
                    + str(HARD_TOKENS // 1000) + "k）：派下一单之前先把恢复要点写好；读输出只看尾部。")
        return ""
    except Exception:
        return ""


def main():
    project = _arg("--cwd") or os.environ.get("ZCODE_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    project = os.path.normpath(project)
    files = sorted(glob.glob(os.path.join(project, "docs", "任务台账-*.md")))
    if not files:
        return
    lines, goal = [], ""
    for path in files:
        meta, rows = parse_ledger(path)
        goal = goal or meta.get("目标", "")
        name = os.path.splitext(os.path.basename(path))[0].replace("任务台账-", "", 1)
        opened = [r for r in rows if not str(r.get("状态", "")).startswith(CLOSED)]
        if not opened:
            lines.append("「" + name + "」没有未关闭的单子")
            continue
        lines.append("「" + name + "」未关闭 " + str(len(opened)) + " 单：")
        for r in opened[:MAX_ROWS]:
            g = str(r.get("目标", ""))
            g = g if len(g) <= 28 else g[:27] + "…"
            lines.append("  · #" + str(r.get("单号", "?")) + " " + g + "｜" + str(r.get("施工方", "")) + "｜"
                         + str(r.get("状态", "")) + "｜打回 " + str(r.get("打回", "0")))
        if len(opened) > MAX_ROWS:
            lines.append("  · …另有 " + str(len(opened) - MAX_ROWS) + " 单，看台账文件")
    head = []
    if goal:
        head.append("【总目标】" + goal)
    else:
        head.append("【总目标】台账第一行还没写目标：在 docs/任务台账-<战役>.md 最上面加一行 "
                    "<!-- 目标: 一两句话说要做到什么，加做到什么算完 -->，写完再派单。剩余的单子都是为它服务的。")
    ctx = context_line()
    if ctx:
        head.append(ctx)
    print(json.dumps({"additionalContext": "\n".join(head + ["【任务台账】只有主会话写台账；动手前以它为准。"] + lines)},
                     ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
