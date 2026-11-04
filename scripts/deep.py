"""Deep checks (tier 2) for BSC mint precheck. Read-only, no signing.

Covers what the 3-second tier-1 filter cannot answer by itself:
  1. Sell path   - is there a real place to sell? (PancakeSwap V2/V3 pools for
                   the NFT contract itself if ERC20-like, and for every reward /
                   payment token the contract points at; quote via router;
                   NFT in-contract market functions)
  2. Lock/ignite - can the asset actually move? (NFT transferFrom simulated
                   from a real holder; ERC20 transfer simulated from its pool;
                   trading/launch/lock/vesting getters and owner toggles)
  3. Refund/deposit coverage - for deposit/refund style mints, does the
                   contract hold enough BNB/token to pay everyone back?

Each check returns a status of ok / warn / bad / unknown plus the evidence.
`unknown` always means "read the source": never treat it as ok.
"""
import json, re, time
from concurrent.futures import ThreadPoolExecutor

import precheck as P

WBNB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
USDT = "0x55d398326f99059ff775485246999027b3197955"
V2_FACTORY = "0xca143ce32fe78f1f7019d7d551a6402fc5350c73"
V2_ROUTER = "0x10ed43c718714eb63d5aa57b78b54704e256024e"
V3_FACTORY = "0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865"
PROBE_TO = "0x000000000000000000000000000000000000bEEF"
ZERO = "0x0000000000000000000000000000000000000000"
QUOTES = {WBNB: ("WBNB", 18), USDT: ("USDT", 18)}

LOCK_GET = re.compile(r"(?i)^(trading\w*|tradingOpen|tradingEnabled|isTrading\w*|launch\w*|isLaunched|"
                      r"ignit\w*|ignited|lock\w*|locked|unlock\w*|vest\w*|cliff\w*|release\w*|"
                      r"transferable|transfers?Enabled|isTransferable|soulbound|canTransfer|"
                      r"swapEnabled|limitsInEffect|maxTx\w*|maxWallet\w*|buyTax|sellTax|"
                      r"buyFee\w*|sellFee\w*|totalFees?|_?taxRate)\(\)$")
LOCK_SET = re.compile(r"(?i)^(enableTrading|openTrading|startTrading|setTrading\w*|launch|ignite|"
                      r"setLock\w*|lock|unlock|setTransferable|setSoulbound|blacklist\w*|"
                      r"setBlacklist\w*|addBot\w*|setBot\w*|setFee\w*|setTax\w*|setBuyTax|"
                      r"setSellTax|setMaxTx\w*|setMaxWallet\w*|setLimits?\w*|pauseTransfers?)$")
REFUND_FN = re.compile(r"(?i)^(refund\w*|claimRefund|withdrawDeposit|reclaim\w*|burnForRefund|"
                       r"redeem\w*|unstake\w*|deposit\w*)$")
REFUND_GET = re.compile(r"(?i)^(refund\w*|deposit\w*|collateral\w*|totalLocked\w*|escrow\w*|"
                        r"totalStaked|DEPOSIT\w*|REFUND\w*|reserve\w*)\(\)$")
UNIT_PRICE = re.compile(r"(?i)^(refundPrice|refundAmount|depositAmount|depositPrice|DEPOSIT|"
                        r"DEPOSIT_AMOUNT|REFUND_AMOUNT|refundPerToken|depositPerMint)\(\)$")
TOTAL_LIAB = re.compile(r"(?i)^(totalDeposit\w*|totalRefund\w*|totalLocked\w*|totalStaked|"
                        r"totalEscrow\w*|totalCollateral\w*)\(\)$")
MARKET_FN = re.compile(r"(?i)^(list\w*|buy\w*|cancelList\w*|delist\w*|makeOffer|acceptOffer|"
                       r"purchase|fillOrder|sell\w*)$")


def _c(to, data, frm=None):
    r, e = P.call(to, data, frm)
    return r, e


def _uint(to, sig, *args):
    data = "0x" + P.sel(sig) + "".join(args)
    r, _ = _c(to, data)
    return P.parse_uint(r)


def _addr(to, sig, *args):
    r, _ = _c(to, "0x" + P.sel(sig) + "".join(args))
    if r and len(r) >= 66:
        return "0x" + r[-40:]
    return None


def _decimals(tok):
    d = _uint(tok, "decimals()")
    return d if d is not None and d <= 36 else None


def _is_nft(ca):
    r, _ = _c(ca, "0x" + P.sel("supportsInterface(bytes4)") + "80ac58cd" + "0" * 56)
    if P.parse_uint(r) == 1:
        return "ERC721"
    r, _ = _c(ca, "0x" + P.sel("supportsInterface(bytes4)") + "d9b67a26" + "0" * 56)
    if P.parse_uint(r) == 1:
        return "ERC1155"
    return None


def _balance(addr):
    r, _ = P.rpc("eth_getBalance", [addr, "latest"])
    return P.parse_uint(r) or 0


def _erc20_bal(tok, who):
    return _uint(tok, "balanceOf(address)", P.pad_addr(who)) or 0


def _amounts_out(amount, path):
    # getAmountsOut(uint256,address[])
    head = P.pad_uint(amount) + P.pad_uint(64) + P.pad_uint(len(path))
    data = "0x" + P.sel("getAmountsOut(uint256,address[])") + head + "".join(P.pad_addr(a) for a in path)
    r, e = _c(V2_ROUTER, data)
    if not r or r == "0x":
        return None
    try:
        b = r[2:]
        n = int(b[64:128], 16)
        return int(b[128 + 64 * (n - 1):128 + 64 * n], 16)
    except Exception:
        return None


def token_candidates(r):
    """The mint contract itself when it is ERC20-like, plus every address a
    zero-arg view points at that answers decimals() (reward / payment token)."""
    ca = r["contract"].lower()
    out = {}
    g = r.get("global") or {}
    infra = set(P.INFRA.keys())
    owner = (g.get("owner()") or "").lower() if isinstance(g.get("owner()"), str) else ""
    addrs = []
    for k, v in g.items():
        if isinstance(v, str) and P.ADDR_RE.fullmatch(v or ""):
            a = v.lower()
            if a in infra or a == owner or a == ca or a == ZERO:
                continue
            addrs.append((k, a))
    nft = _is_nft(ca)
    if not nft:
        d = _decimals(ca)
        if d is not None:
            out[ca] = {"via": "mint contract (ERC20)", "decimals": d}
    def chk(item):
        k, a = item
        code, _ = P.rpc("eth_getCode", [a, "latest"])
        if not code or code == "0x":
            return None
        if _is_nft(a):
            return None
        d = _decimals(a)
        return (a, {"via": k, "decimals": d}) if d is not None else None
    with ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(chk, addrs):
            if res:
                out[res[0]] = res[1]
    return nft, out


def pools_for(tok):
    pools = []
    for q, (qname, qdec) in QUOTES.items():
        pair = _addr(V2_FACTORY, "getPair(address,address)", P.pad_addr(tok), P.pad_addr(q))
        if pair and pair != ZERO:
            qbal = _erc20_bal(q, pair)
            tbal = _erc20_bal(tok, pair)
            pools.append({"dex": "PancakeV2", "pool": pair, "quote": qname,
                          "quoteReserve": round(qbal / 10 ** qdec, 6), "tokenReserve": tbal})
        for fee in (100, 500, 2500, 10000):
            pool = _addr(V3_FACTORY, "getPool(address,address,uint24)",
                         P.pad_addr(tok), P.pad_addr(q), P.pad_uint(fee))
            if pool and pool != ZERO:
                qbal = _erc20_bal(q, pool)
                tbal = _erc20_bal(tok, pool)
                pools.append({"dex": "PancakeV3 %.2f%%" % (fee / 10000), "pool": pool, "quote": qname,
                              "quoteReserve": round(qbal / 10 ** qdec, 6), "tokenReserve": tbal})
    return pools


def sell_path(r, nft, toks, names_set):
    res = {"tokens": {}, "nftMarket": None}
    for tok, meta in toks.items():
        pools = pools_for(tok)
        dec = meta["decimals"]
        quote = None
        best = max(pools, key=lambda p: p["quoteReserve"], default=None)
        if best and best["dex"] == "PancakeV2" and best["quoteReserve"] > 0:
            amt = 10 ** dec
            q = WBNB if best["quote"] == "WBNB" else USDT
            out = _amounts_out(amt, [tok, q])
            if out is not None:
                quote = {"in": "1 token", "out": round(out / 1e18, 10), "unit": best["quote"]}
        transfer = transfer_test_erc20(tok, best["pool"]) if best else None
        res["tokens"][tok] = {"via": meta["via"], "decimals": dec, "pools": pools,
                              "quote1": quote, "transferFromPool": transfer}
    if nft:
        mk = sorted(n for n in names_set if MARKET_FN.match(n))
        g = r.get("global") or {}
        res["nftMarket"] = {"inContractMarketFns": mk,
                            "opened": g.get("opened()"), "isOpen": g.get("isOpen()")}
    # verdict
    liquid = [t for t, v in res["tokens"].items()
              if any(p["quoteReserve"] >= (1 if p["quote"] == "WBNB" else 500) for p in v["pools"])]
    thin = [t for t, v in res["tokens"].items() if v["pools"] and t not in liquid]
    if liquid:
        st, why = "ok", "有池子：" + "，".join(
            "%s %s %.3f %s" % (t[:10], max(v["pools"], key=lambda p: p["quoteReserve"])["dex"],
                               max(p["quoteReserve"] for p in v["pools"]),
                               max(v["pools"], key=lambda p: p["quoteReserve"])["quote"])
            for t, v in res["tokens"].items() if t in liquid)
    elif thin:
        st, why = "warn", "池子很浅（<1 BNB / <500 USDT），大额卖不出：" + ", ".join(t[:10] for t in thin)
    elif nft and res["nftMarket"]["inContractMarketFns"]:
        op = res["nftMarket"]["opened"] if res["nftMarket"]["opened"] is not None else res["nftMarket"]["isOpen"]
        st = "ok" if op == 1 else "warn"
        why = "合约内置市场（%s）%s" % (",".join(res["nftMarket"]["inContractMarketFns"][:5]),
                                  "已开" if op == 1 else ("未开" if op == 0 else "开放状态未读到"))
    elif nft:
        st, why = "unknown", "NFT 无内置市场、无代币池；只能靠第三方 NFT 市场，需人工看有没有挂单/成交"
    else:
        st, why = "bad", "没找到任何池子或市场：无卖出路径"
    for t, v in res["tokens"].items():
        tf = v.get("transferFromPool")
        if tf and tf["status"] == "bad":
            st, why = "bad", why + "；但代币从池子转出被拒（%s），疑似禁止交易/貔貅" % tf["reason"][:60]
    res["status"], res["why"] = st, why
    res["note"] = "卖出税未模拟（需真实持仓地址+授权，只读模式不做）；报价为 1 枚理论值，不含滑点与税"
    return res


def transfer_test_erc20(tok, holder):
    if not holder:
        return None
    amt = 1
    r, e = _c(tok, "0x" + P.sel("transfer(address,uint256)") + P.pad_addr(PROBE_TO) + P.pad_uint(amt), holder)
    if r is not None and P.parse_uint(r) in (1, None):
        return {"status": "ok", "holder": holder}
    return {"status": "bad", "holder": holder, "reason": P.decode_revert(e) if e else "returned false"}


def transfer_test_nft(ca):
    for tid in (1, 0, 2, 3, 10):
        owner = _addr(ca, "ownerOf(uint256)", P.pad_uint(tid))
        if owner and owner != ZERO:
            data = "0x" + P.sel("transferFrom(address,address,uint256)") + \
                P.pad_addr(owner) + P.pad_addr(PROBE_TO) + P.pad_uint(tid)
            r, e = _c(ca, data, owner)
            if e is None:
                return {"status": "ok", "tokenId": tid, "holder": owner}
            return {"status": "bad", "tokenId": tid, "holder": owner, "reason": P.decode_revert(e)}
    return {"status": "unknown", "reason": "前几个 tokenId 都没有持有人，无法模拟转账"}


def lock_check(r, nft, toks, names, names_set):
    getters = [(s, n) for s, n in names.items() if LOCK_GET.match(n)]
    vals = {}
    def rd(item):
        s, n = item
        out, _ = _c(r["contract"], "0x" + s)
        return n, P.parse_uint(out)
    with ThreadPoolExecutor(max_workers=6) as ex:
        for n, v in ex.map(rd, getters):
            vals[n] = v
    setters = sorted(n for n in names_set if LOCK_SET.match(n))
    now = int(time.time())
    timed = {}
    for n, v in vals.items():
        if isinstance(v, int) and 1.6e9 < v < 2.2e9:
            timed[n] = {"cst": time.strftime("%Y-%m-%d %H:%M", time.gmtime(v + 8 * 3600)), "past": v < now}
    nft_tf = transfer_test_nft(r["contract"]) if nft == "ERC721" else None
    st, why = "ok", []
    if nft_tf:
        if nft_tf["status"] == "bad":
            st = "bad"; why.append("NFT 持有人转账被拒（%s）：疑似灵魂绑定/锁仓" % (nft_tf["reason"] or "")[:60])
        elif nft_tf["status"] == "ok":
            why.append("NFT 持有人转账模拟通过（#%s）" % nft_tf["tokenId"])
        else:
            st = "unknown"; why.append(nft_tf["reason"])
    for n, v in vals.items():
        base = n.split("(")[0].lower()
        if v == 0 and re.search(r"trading|launch|ignit|transferable|transfersenabled|cantransfer|swapenabled", base):
            st = "bad" if st != "bad" else st; why.append("%s=0（未点火/未开交易）" % n)
        if v == 1 and re.search(r"^(locked|soulbound|limitsineffect)", base):
            st = "warn" if st == "ok" else st; why.append("%s=1" % n)
    for n, t in timed.items():
        if not t["past"] and re.search(r"(?i)unlock|release|vest|cliff|launch|trading", n):
            st = "warn" if st == "ok" else st; why.append("%s 在 %s CST 之后才解锁" % (n, t["cst"]))
    taxes = {n: v for n, v in vals.items() if re.search(r"(?i)tax|fee", n) and isinstance(v, int)}
    if any(v and v > 1000 for v in taxes.values()):
        st = "warn" if st == "ok" else st; why.append("税率读数偏高：%s（单位以源码为准）" % taxes)
    if setters:
        if st == "ok":
            st = "warn"
        why.append("owner 可切换：" + ", ".join(setters[:8]))
    if not why:
        why.append("没发现锁仓/点火/转账限制函数")
    return {"status": st, "why": "；".join(why), "getters": vals, "times": timed,
            "ownerToggles": setters, "nftTransfer": nft_tf}


def refund_check(r, names, names_set):
    fns = sorted(n for n in names_set if REFUND_FN.match(n))
    if not fns:
        return {"status": "n/a", "why": "不是押金/退款型铸造"}
    getters = [(s, n) for s, n in names.items() if REFUND_GET.match(n) or TOTAL_LIAB.match(n) or UNIT_PRICE.match(n)]
    vals = {}
    for s, n in getters:
        out, _ = _c(r["contract"], "0x" + s)
        vals[n] = P.parse_uint(out)
    g = r.get("global") or {}
    pt = g.get("paymentToken()")
    erc = isinstance(pt, str) and pt.lower() not in (ZERO,)
    if erc:
        dec = _decimals(pt) or 18
        bal = _erc20_bal(pt, r["contract"]); unit = "token %s" % pt[:10]
    else:
        dec = 18; bal = _balance(r["contract"]); unit = "BNB"
    minted = ((r.get("verdict") or {}).get("supply") or {}).get("minted")
    liab, how = None, None
    tot = [v for n, v in vals.items() if TOTAL_LIAB.match(n) and v]
    per = [v for n, v in vals.items() if UNIT_PRICE.match(n) and v]
    if tot:
        liab, how = max(tot), "合约记录的总押金/总退款"
    elif per and minted:
        liab, how = per[0] * minted, "单份退款额 × 已铸 %s（含免费档时会偏高）" % minted
    res = {"refundFns": fns, "getters": vals, "balance": round(bal / 10 ** dec, 6), "unit": unit}
    if liab:
        cov = bal / liab if liab else None
        res.update({"liability": round(liab / 10 ** dec, 6), "coverage": round(cov, 3), "method": how})
        if cov >= 1:
            res["status"], res["why"] = "ok", "押金覆盖率 %.0f%%（余额 %s / 应退 %s %s）" % (cov * 100, res["balance"], res["liability"], unit)
        elif cov >= 0.8:
            res["status"], res["why"] = "warn", "押金覆盖率只有 %.0f%%，挤兑会有人退不到" % (cov * 100)
        else:
            res["status"], res["why"] = "bad", "押金覆盖率 %.0f%%，不足额" % (cov * 100)
        owner_drain = sorted(n for n in names_set if re.match(r"(?i)^(withdraw\w*|emergency\w*|rescue\w*|sweep\w*)$", n))
        if owner_drain:
            res["why"] += "；owner 有提款函数 %s，需读源码确认能否动押金" % ",".join(owner_drain)
            if res["status"] == "ok":
                res["status"] = "warn"
    else:
        res["status"] = "unknown"
        res["why"] = "有退款/押金函数（%s），但读不到应退总额，合约余额 %s %s；需读源码算" % (",".join(fns[:4]), res["balance"], unit)
    return res


def deep_check(r):
    t0 = time.time()
    names = r.get("_names") or {}
    names_set = {n.split("(")[0] for n in names.values()}
    nft, toks = token_candidates(r)
    out = {"assetType": nft or ("ERC20" if r["contract"].lower() in toks else "unknown"),
           "tokensFound": {t: m["via"] for t, m in toks.items()}}
    with ThreadPoolExecutor(max_workers=3) as ex:
        f1 = ex.submit(sell_path, r, nft, toks, names_set)
        f2 = ex.submit(lock_check, r, nft, toks, names, names_set)
        f3 = ex.submit(refund_check, r, names, names_set)
        out["sellPath"], out["lock"], out["refund"] = f1.result(), f2.result(), f3.result()
    sts = [out["sellPath"]["status"], out["lock"]["status"], out["refund"]["status"]]
    if "bad" in sts:
        overall = "bad"
    elif "unknown" in sts:
        overall = "unknown"
    elif "warn" in sts:
        overall = "warn"
    else:
        overall = "ok"
    out["overall"] = overall
    out["deep_s"] = round(time.time() - t0, 2)
    return out


ZH = {"ok": "✅", "warn": "⚠️", "bad": "❌", "unknown": "❓需读源码", "n/a": "—"}


def deep_summary(d):
    return "\n".join([
        "深查（第二层）：总评 %s · 耗时 %ss" % (ZH[d["overall"]], d["deep_s"]),
        "  卖出路径 %s %s" % (ZH[d["sellPath"]["status"]], d["sellPath"]["why"]),
        "  锁仓/点火 %s %s" % (ZH[d["lock"]["status"]], d["lock"]["why"]),
        "  押金退款 %s %s" % (ZH.get(d["refund"]["status"], d["refund"]["status"]), d["refund"]["why"]),
        "  注：%s" % d["sellPath"]["note"],
    ])
