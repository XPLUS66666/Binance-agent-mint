# 更新说明 / Changelog

## 2026-11-05 — v1.1.0

- 新增 `skills/agent-drop-patrol-tracking/` 技能 v1.1.0：Agent 掉落巡查与追踪（发现、验证、追踪、收尾，含断腿替补路、固定档阈值、持有物卖出路径）。
- 新增“检查注册与凭证窗口”一节：凭证制的免费项目，真正的截止时间是后端注册/凭证窗口，不是链上开盘时间；新项目一发现当轮就查注册、名额和凭证状态，注册窗口当正式截止时间记；凭证制免费单独排队优先报；加一条盯新 Agent ID 铸造站的腿；报免费剩余以后端凭证口径为准，不只报链上供应。
- 背景：2026-11-05 AGI（agentideth.com）免费 500 份开盘前就被后端凭证预占完，巡查 16:41 只报了 @AgentIdETH 名字、没深挖注册和凭证，23:00 开盘时已无免费可打。
- 版本号：本技能首次在仓库发布为 v1.1.0（v1.0.0 为 2026-11-02 本地合并版，未单独发版）。

## 2026-11-05 — v1.1.0 (English)

- Added the `skills/agent-drop-patrol-tracking/` skill v1.1.0: agent drop patrol and tracking (discover, verify, track and close, including broken-leg alternative routes, fixed tier thresholds and held item sell paths).
- Added the “Check the registration and voucher window” section: for a voucher-gated free mint, the real deadline is the backend registration/voucher window, not the on-chain open time. On discovery, check registration, allocation and voucher status in the same round and record the registration window as the real deadline; queue voucher-gated free projects separately and report them first; add a leg for new Agent ID mint sites; report remaining free allocation from the backend, not just on-chain supply.
- Background: on 2026-11-05 the 500 free AGI shares (agentideth.com) were pre-claimed through backend vouchers before the 23:00 on-chain open. The patrol reported only the @AgentIdETH name at 16:41 without a same-round deep dive into registration and vouchers, so no free share was left at open.
- Version: this skill is first published in this repo as v1.1.0 (v1.0.0 was the 2026-11-02 local merged version, never released separately).


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
