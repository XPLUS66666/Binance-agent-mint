# 更新说明 / Changelog

## 2026-11-04

- SKILL.md 新增“卡死时间项目：预检就是开盘前预热”一节：已知开盘时间的项目，预检要在开盘前至少提前 1 小时跑完并单独确认预热完成；开盘后轮询瘦到每 2-5 秒只做最关键的一个模拟调用；预热没完成就明说没预热好，不把“任务已排定”说成“已准备好”。
- 背景：2026-11-04 某免费项目 14:30 开盘，定时任务 14:29 触发、执行脚本 14:30:26 才启动、第一轮模拟 14:30:41 已回滚免费档耗尽，窗口错过。本节就是为此而加。
- 其余不变：本工具仍全程只读，不签名、不授权、不付款、不发交易；打不打由用户决定。

此前版本见 Git commit 历史。

## 2026-11-04 (English)

- SKILL.md: added the “Prewarm for time-locked mints” section. For a mint with a known opening time, run the full precheck at least 1 hour before it opens and confirm prewarm completion separately; after the opening, poll lean every 2–5 seconds with only the single essential simulation call; if prewarm has not completed, say plainly that it is not prewarmed — a scheduled task is not prewarm.
- README.md: added the same prewarm explanation in both the English and Chinese sections.
- Background: on 2026-11-04 a free mint opened at 14:30, its scheduled task fired at 14:29, the execution script only started at 14:30:26, and the first simulation at 14:30:41 already reverted as sold out. This section was added because of that miss.
- Everything else unchanged: the tool is still fully read-only — no signing, no approvals, no payments, no transactions; whether to mint is the user's decision.

Earlier versions: see Git commit history.
