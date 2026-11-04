# Binance Agent Mint 🤖🛡️

![banner](assets/banner.png)

**English** | [中文](#中文说明)

> Read-only precheck for Binance Agent ID (BSC) mints. Paste a URL or contract address, get free / paid / supply / eligibility / risk in ~3 seconds.

**Fully read-only: no signing, no approvals, no payments, no transactions.** You decide whether to mint.

![flow](assets/flow.png)

## Features
- **URL or CA input** – finds the mint contract from the page, same-site JS, `/agents.md`, `/llms.txt`; filters out infra addresses (WBNB, USDT, routers…)
- **Proxy detection** – EIP-1967 / Beacon / EIP-1167, follows to the implementation and flags upgradeability
- **ABI** – Sourcify verified source first, openchain signature DB as fallback (cached locally)
- **Parallel reads** – name, total supply, minted, price, free remaining, per-wallet cap, open/paused, start/end time, owner powers
- **Mint simulation** – `eth_call` + `estimateGas` with balance override, so you know if it will revert before you sign anything
- **Binance Agent ID gate check** – registry `0x60b9…022f`; checks free quota / claimed status by your Agent ID tokenId
- **10 verdicts** – OPEN_FREE / OPEN_PAID / SOLD_OUT / ENDED / CLOSED / NOT_STARTED / WALLET_LIMIT / NOT_MINTABLE_NOW / NO_MINT_FN / UNKNOWN
- **Risk flags** – unverified source, upgradeable proxy, ERC20 pricing (needs approval), owner can change price / pause / withdraw
- **Batch & fast** – multiple targets in parallel, ~2–3 s each, public RPCs, no API key
- **Agent-ready** – ships a `SKILL.md` you can drop into any AI agent as a skill

## Install
```bash
git clone https://github.com/XPLUS66666/binance-agent-mint.git
cd binance-agent-mint
pip install requests pycryptodome
```
Python 3.9+.

## Usage
```bash
# single contract or URL, with a human summary (Chinese) on stderr
python3 scripts/precheck.py --input 0xCONTRACT --human
python3 scripts/precheck.py --input https://project.site --human

# batch in parallel
python3 scripts/precheck.py --input 0xA... --input 0xB... --human

# check your own wallets (optional; add Agent ID tokenId for accuracy)
python3 scripts/precheck.py --input 0xCONTRACT --wallets "main=0xYOUR_ADDR:0xYOUR_AGENT_ID,alt=0xOTHER" --human
export BSC_PRECHECK_WALLETS="main=0xYOUR_ADDR"   # or via env

# quantity / full report
python3 scripts/precheck.py --input 0xCONTRACT --qty 2 --full

# log a real mint result, view the library
python3 scripts/precheck.py record 0xCONTRACT success "free first mint" --tx 0x...
python3 scripts/precheck.py lib
```
stdout is JSON (for scripts / agents).

## Known limits
- Merkle-proof or backend-signature mints revert with empty proofs; you need the project's API for the proof
- ERC20-priced mints need an approval – the tool only flags it, never approves
- Brand-new unverified contracts rely on signature-DB guesses

## ⚠️ Disclaimer
This tool performs read-only on-chain analysis and is provided "as is" for educational and research purposes. It is **not financial advice** and not affiliated with or endorsed by Binance. A "mintable" verdict is a snapshot, not a guarantee: contract owners can change prices, pause, or withdraw at any time, and projects can go to zero. Always do your own research. **Never share your seed phrase or private key with any website, script, or tool.** The authors accept no liability for any loss arising from use of this software.

---

## 中文说明

![banner_zh](assets/banner_zh.png)

> 币安 Agent ID（BSC）铸造只读预检。丢一个网址或合约地址（CA），约 3 秒看清免费/付费、余量、钱包资格和风险。

**全程只读：不签名、不授权、不付款、不发交易。** 打不打，你自己决定。

![flow_zh](assets/flow_zh.png)

### 功能
- **网址或 CA 都行**：从页面、同站 JS、`/agents.md`、`/llms.txt` 自动找铸造合约，过滤 WBNB/USDT/Router 等基础设施地址
- **代理识别**：EIP-1967 / Beacon / EIP-1167，追到真实实现，标出可升级风险
- **ABI**：Sourcify 已验证源码优先，否则用 openchain 签名库（本地缓存）
- **并行读链**：名称、总量、已铸、价格、免费余量、每钱包上限、开关/暂停、起止时间、owner 权限
- **模拟铸造**：eth_call + estimateGas（余额覆盖），签名前就知道会不会 revert
- **币安 Agent ID 门槛检测**：注册表 `0x60b9…022f`，按你的 Agent ID tokenId 查免费额度与已领状态
- **10 档结论**：OPEN_FREE / OPEN_PAID / SOLD_OUT / ENDED / CLOSED / NOT_STARTED / WALLET_LIMIT / NOT_MINTABLE_NOW / NO_MINT_FN / UNKNOWN
- **风险标红**：源码未验证、可升级代理、ERC20 计价（需授权）、owner 可改价/暂停/提款
- **批量快速**：多目标并行，单个约 2–3 秒，公共 RPC，无需 API key
- **AI Agent 可用**：自带 `SKILL.md`，可直接作为 Agent 技能

### 安装与用法
同上方 Install / Usage。`--human` 输出中文摘要到 stderr，stdout 为 JSON。钱包用 `--wallets` 或环境变量 `BSC_PRECHECK_WALLETS` 配置（可选）。

### 已知限制
- Merkle 证明 / 后端签名型铸造：空证明模拟会 revert，需从项目方 API 拿证明
- ERC20 计价的铸造需要授权：脚本只标出，绝不授权
- 很新的未验证合约只能靠签名库猜函数名

### ⚠️ 免责声明
本工具仅做只读链上分析，按"现状"提供，用于学习与研究，**不构成任何投资建议**，与币安（Binance）无关联、未获其背书。"可打"只是当时的快照，不代表保证：合约 owner 可能随时改价、暂停或提款，项目可能归零。请自行研究判断。**永远不要把助记词或私钥交给任何网站、脚本或工具。** 因使用本软件造成的任何损失，作者概不负责。

## License
MIT
