# Binance Agent Mint 🤖🛡️

> 币安 Agent ID 铸造预检 · 打之前 3 秒看清免费/付费/风险

![banner](assets/banner.png)

打币安 Agent（BSC）铸造之前，先花 3 秒验一下合约。丢进一个**网址**或**合约地址（CA）**，自动完成只读链上预检：是免费还是付费、开没开、还剩多少、你的钱包能不能打、有什么风险。

**全程只读：不签名、不授权、不付款、不发交易。** 打不打，你自己决定。

![flow](assets/flow.png)

## 功能
- 只给网址也行：从页面、同站 JS、`/agents.md`、`/llms.txt` 里自动找合约，过滤 WBNB/USDT/Router 等基础设施地址
- 代理识别（EIP-1967 / Beacon / EIP-1167），追到真实实现，标出可升级风险
- ABI：Sourcify 已验证源码优先，否则用 openchain 签名库（本地缓存）
- 并行读：名称、总量、已铸、价格、免费余量、每钱包上限、开关、起止时间、owner 权限
- 模拟铸造：eth_call + estimateGas（余额覆盖，不受真实余额影响），提前知道会不会 revert
- **币安 Agent ID 门槛检测**（注册表 `0x60b9…022f`），按你的 Agent ID tokenId 查免费额度与已领状态
- 10 档结论：OPEN_FREE / OPEN_PAID / SOLD_OUT / ENDED / CLOSED / NOT_STARTED / WALLET_LIMIT / NOT_MINTABLE_NOW / NO_MINT_FN / UNKNOWN
- 批量并行，单个约 2–3 秒
- 经验库：记录打过的合约，相似合约直接复用识别方法（地址、价格每次都重新读）

## 安装
```bash
git clone https://github.com/XPLUS66666/binance-agent-mint.git
cd binance-agent-mint
pip install requests pycryptodome
```
Python 3.9+。用公共 BSC RPC，无需 API key。

## 用法
```bash
# 单个 CA 或网址，附中文摘要
python3 scripts/precheck.py --input 0x合约地址 --human
python3 scripts/precheck.py --input https://项目官网 --human

# 批量并行
python3 scripts/precheck.py --input 0xA... --input 0xB... --human

# 查自己钱包的资格（可选，带 Agent ID tokenId 更准）
python3 scripts/precheck.py --input 0x合约 --wallets "main=0x你的地址:0x你的AgentID,alt=0x另一个地址" --human
# 或用环境变量
export BSC_PRECHECK_WALLETS="main=0x你的地址"

# 指定数量 / 完整报告
python3 scripts/precheck.py --input 0x合约 --qty 2 --full

# 记录真实铸造结果、查看经验库
python3 scripts/precheck.py record 0x合约 success "免费首枚" --tx 0x...
python3 scripts/precheck.py lib
```
stdout 输出 JSON（给程序/AI Agent 用），`--human` 时中文摘要打在 stderr。

## 给 AI Agent 用
仓库里的 `SKILL.md` 可以直接作为 Agent 的 skill：用户发来铸造网址或 CA 时先跑预检，再把结论交给用户确认。

## 已知限制
- Merkle 证明 / 后端签名型铸造：空证明模拟会 revert，需从项目方 API 拿证明
- ERC20 计价的铸造需要授权：脚本只标出来，绝不授权
- 很新的未验证合约只能靠签名库猜函数名

## 免责声明
本工具只做只读分析，不构成投资建议。链上项目可能归零，合约 owner 可能随时改价、暂停或提款。永远不要把助记词、私钥交给任何网站或工具。

MIT License
