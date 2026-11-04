#!/usr/bin/env python3
"""BSC Mint precheck v2 - read-only. Never signs, never approves, never pays.

Usage:
  python3 precheck.py --input <CA|URL> [--input <CA|URL> ...] [--human] [--qty N]
                      [--wallets 0xAAA,0xBBB] [--full] [--no-record]
  python3 precheck.py record <CA> success|fail "reason" [--tx 0x...]

Single target: stdout is one JSON object (v1-compatible keys plus new fields).
Multiple targets: stdout is {"results": [...]}.
--human prints a simple Simplified Chinese summary to stderr, stdout stays JSON.
--full adds the complete report (raw reads, all simulations) to stdout JSON.

Pipeline (each stage timed):
  1 resolve  URL -> page + same-site JS + /agents.md + /llms.txt -> scored
             addresses -> keep contracts (eth_getCode), infra addresses excluded
  2 code     EIP-1967 / Beacon / OZ-legacy / EIP-1167 proxy -> implementation,
             code hash + PUSH4 selector set (works even when source unverified)
  3 abi      Sourcify verified ABI (implementation first, then proxy); unknown
             selectors named via openchain signature DB (cached); history match
             only judges method reuse, never supplies addresses/prices/params
  4 reads    generic discovery: zero-arg views, address-arg views, uint-price
             views (regex) + known-template probes (priceAt / quoteFor / costOf /
             tokenId eligibility / Agent ID registry / balances)
  5 simulate generic mint candidates: build args by type, try price candidates,
             retry with amounts found in revert data, eth_call with funded-state
             override (fallback: plain call), estimateGas, gas<45k = getter guard
  6 risk     proxy / unverified / tiny code / paid / ERC20 payment / approval
             functions / owner powers / paused / not open
  7 verdict  9-way status + 3-way Chinese conclusion + needsAI flag

Policy (from the user's standing rules, not from any project doc):
  - precheck only; mint or not, how many, always waits for the user's word
    (except the standing free-auto-mint rule: total <=309 or free quota <=300,
    safety lines all passing - see SKILL.md).
  - ERC20-priced mints only flag the need for approval; never auto-approve.
  - history.json is updated only after human review; `record` only appends
    raw outcomes to records.jsonl, it never writes history.json.
"""
import argparse, fcntl, json, os, re, sys, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from Crypto.Hash import keccak as _keccak
    def _keccak_hex(b):
        k = _keccak.new(digest_bits=256); k.update(b); return k.hexdigest()
except Exception:
    try:
        from eth_hash.auto import keccak as _eth_keccak
        def _keccak_hex(b):
            return _eth_keccak(b).hex()
    except Exception:
        _keccak_hex = None

KNOWN_SELECTORS = {
    "claimFree()": "f366afc9", "freeMint()": "5b70ea9f",
    "totalMinted()": "a2309ff8", "freeMinted()": "d10a1a2b",
    "paused()": "5c975abb", "mintOpen()": "8f2a1c4e",
}

def sel(sig):
    if _keccak_hex:
        return _keccak_hex(sig.encode())[:8]
    s = KNOWN_SELECTORS.get(sig)
    if not s:
        raise RuntimeError(f"no selector for {sig}: keccak unavailable")
    return s

def khash(b):
    if _keccak_hex:
        return "0x" + _keccak_hex(b)
    import hashlib
    return "0x" + hashlib.sha256(b).hexdigest()

RPCS = [
    "https://bsc-dataseed1.bnbchain.org",
    "https://bsc-dataseed.binance.org",
    "https://rpc-bsc.48.club",
    "https://bsc.publicnode.com",
]
REG = "0x60b9262456de011f7d15a54cdd8c48baaa8b022f"   # Binance Agent ID registry
def _load_wallets():
    """Wallets to check, from env BSC_PRECHECK_WALLETS or --wallets.
    Format: name=0xADDRESS[:0xAGENT_ID_TOKENID], comma separated.
    e.g. BSC_PRECHECK_WALLETS="main=0xAbc...:0x4fbc...,alt=0xDef..."
    """
    raw = os.environ.get("BSC_PRECHECK_WALLETS", "")
    return _parse_wallets(raw) if raw else {}


def _parse_wallets(raw):
    out = {}
    for i, item in enumerate(x.strip() for x in raw.split(",") if x.strip()):
        name, _, rest = item.rpartition("=") if "=" in item else ("w%d" % i, "", item)
        addr, _, aid = rest.partition(":")
        out[name or "w%d" % i] = {"address": addr.strip(), "agent_id": (aid.strip() or "0x0")}
    return out


WALLETS = None  # filled at runtime by _load_wallets()
INFRA = {  # never a mint target
    "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c": "WBNB",
    "0x55d398326f99059ff775485246999027b3197955": "USDT",
    "0x10ed43c718714eb63d5aa57b78b54704e256024e": "PancakeV2Router",
    "0x13f4ea83d0bd40e75c8222255bc855a974568dd4": "PancakeV3Router",
    "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": "USDC",
    "0xe9e7cea3dedca5984780bafc599bd69add087d56": "BUSD",
    REG: "AgentIDRegistry",
    "0x000000000000000000000000000000000000dead": "dead",
    "0x0000000000000000000000000000000000000000": "zero",
}
UA = {"User-Agent": "Mozilla/5.0 (BSC-Mint-Precheck)"}
ADDR_RE = re.compile(r"0x[0-9a-fA-F]{40}")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIGC = os.path.join(BASE, "sigcache.json")

# ---------------- rpc with rotation + revert/ratelimit distinction ----------------

def rpc(method, params, tries=3, timeout=8):
    """Sticky-RPC with failover. A real revert (code 3 / revert text) returns
    immediately without retrying; transport/rate-limit errors fail over to
    the next RPC. The first RPC that answers becomes sticky for the run,
    so one slow/broken node is not re-paid on every retry."""
    order = []
    if _CURRENT["rpc"]:
        order.append(_CURRENT["rpc"])
    order += [r for r in RPCS if r != _CURRENT["rpc"]]
    err = None
    for i in range(tries):
        url = order[i % len(order)]
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        try:
            req = urllib.request.Request(url, data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.load(r)
            if "result" in out:
                _CURRENT["rpc"] = url
                return out["result"], None
            err = out.get("error") or {}
            m = json.dumps(err).lower()
            is_revert = (isinstance(err, dict) and err.get("code") == 3) or \
                any(k in m for k in ("revert", "insufficient funds", "out of gas",
                                     "invalid argument", "cannot unmarshal"))
            if method in ("eth_call", "eth_estimateGas") and is_revert:
                return None, err
            time.sleep(0.3 * (i + 1))
        except Exception as e:
            err = {"message": str(e)[:200]}
            time.sleep(0.5)
    return None, err

_CURRENT = {"rpc": None}

def pick_rpc():
    for rpc_url in RPCS:
        try:
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}).encode()
            req = urllib.request.Request(rpc_url, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=8) as r:
                if "result" in json.load(r):
                    _CURRENT["rpc"] = rpc_url
                    return rpc_url
        except Exception:
            continue
    return None

def call(to, data, frm=None, value=0, fund=False):
    """eth_call. fund=True adds a state override (balance 10^21) so simulation
    is not blocked by a low real balance; falls back to a plain call if the
    node rejects the override parameter. Read-only either way."""
    tx = {"to": to, "data": data}
    if frm:
        tx["from"] = frm
    if value:
        tx["value"] = hex(value)
    params = [tx, "latest"]
    if fund and frm:
        params.append({frm: {"balance": hex(10 ** 21)}})
    res, err = rpc("eth_call", params, 3)
    if err and fund:
        m = json.dumps(err).lower()
        if any(k in m for k in ("override", "invalid param", "unsupported", "argument")):
            res, err = rpc("eth_call", [tx, "latest"], 3)
    return res, err

def estimate_gas(tx, frm):
    params = [tx, "latest"]
    if frm:
        params.append({frm: {"balance": hex(10 ** 21)}})
    res, err = rpc("eth_estimateGas", params, 2)
    if err and frm:
        res, err = rpc("eth_estimateGas", [tx, "latest"], 2)
    if res:
        try:
            return int(res, 16), None
        except Exception:
            return None, {"message": "bad gas value"}
    return None, err

def pad_uint(v):
    return f"{int(v):064x}"

def pad_addr(a):
    return "0" * 24 + a[2:].lower()

def parse_uint(h):
    if not h or h == "0x":
        return None
    try:
        return int(h, 16)
    except Exception:
        return None

def decode_string(h):
    try:
        b = bytes.fromhex(h[2:])
        ln = int.from_bytes(b[32:64], "big")
        return b[64:64 + ln].decode("utf-8", "ignore")
    except Exception:
        return None

def http_get(url, timeout=15, max_bytes=3_000_000):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(max_bytes).decode("utf-8", "ignore")

# ---------------- signature DB (cached, read-only) ----------------

def sigcache_load():
    try:
        return json.load(open(SIGC))
    except Exception:
        return {}

def sig_names(sels):
    """Name unknown 4-byte selectors via openchain, cached in sigcache.json."""
    cache = sigcache_load()
    need = [s for s in sels if s not in cache]
    for i in range(0, len(need), 40):
        chunk = need[i:i + 40]
        try:
            u = "https://api.openchain.xyz/signature-database/v1/lookup?filter=true&function=" + ",".join(chunk)
            j = json.load(urllib.request.urlopen(
                urllib.request.Request(u, headers=UA), timeout=20))
            for s in chunk:
                a = j.get("result", {}).get("function", {}).get(s) or []
                cache[s] = a[0]["name"] if a else None
        except Exception:
            pass
    try:
        with open(SIGC + ".lock", "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            cur = sigcache_load()
            cur.update(cache)
            tmp = SIGC + ".tmp%d" % os.getpid()
            json.dump(cur, open(tmp, "w"))
            os.replace(tmp, SIGC)
    except Exception:
        pass
    return {s: cache.get(s) for s in sels}

# ---------------- stage 1: resolve ----------------

def resolve_input(inp):
    """Returns (candidates, note). CA -> itself. URL -> scored contract list."""
    inp = inp.strip()
    if ADDR_RE.fullmatch(inp):
        return [inp], {"mode": "ca"}
    url = inp if inp.startswith("http") else "https://" + inp
    texts = {}
    tried = []
    try:
        texts["page"] = http_get(url)
        tried.append(url)
    except Exception as e:
        tried.append(f"{url} (fail: {str(e)[:100]})")
    base = re.match(r"https?://[^/]+", url).group(0)
    if "page" in texts:
        for p in re.findall(r'src="([^"]+\.js[^"]*)"', texts["page"])[:10]:
            u = p if p.startswith("http") else base + "/" + p.lstrip("./").lstrip("/")
            if not u.startswith(base):
                continue
            try:
                texts[u] = http_get(u)
            except Exception:
                pass
    for extra in ("/agents.md", "/llms.txt", "/contracts", "/mint"):
        try:
            t = http_get(base + extra, max_bytes=200_000)
            if "<html" not in t[:200].lower():
                texts[base + extra] = t
        except Exception:
            pass
    score = {}
    for src, t in texts.items():
        for mm in ADDR_RE.finditer(t):
            a = mm.group(0).lower()
            if a in INFRA:
                continue
            ctx = t[max(0, mm.start() - 120): mm.end() + 60].lower()
            score[a] = score.get(a, 0) + 1 + 3 * any(
                k in ctx for k in ("mint", "nft", "contract", "launch", "sale", "claim"))
    cands = sorted(score, key=lambda a: -score[a])[:12]
    with ThreadPoolExecutor(max_workers=8) as ex:
        codes = dict(zip(cands, ex.map(
            lambda a: rpc("eth_getCode", [a, "latest"])[0] or "0x", cands)))
    contracts = [a for a in cands if len(codes[a]) > 2]
    return contracts, {"mode": "url", "tried": tried,
                       "addrScores": {a: score[a] for a in cands},
                       "sources": list(texts)}

# ---------------- stage 2: code / proxy ----------------

SLOTS = {"eip1967": "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc",
         "beacon": "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50",
         "ozLegacy": "0x7050c9e0f4ca769c69bd3a8ef740bc37934f8e2c036e5a723fd8ee048ed3f8c3"}

def selectors_of(code):
    try:
        b = bytes.fromhex(code[2:])
    except Exception:
        return []
    out = []
    i = 0
    while i < len(b):
        op = b[i]
        if op == 0x63 and i + 4 < len(b):
            out.append("0x" + b[i + 1:i + 5].hex())
            i += 5
            continue
        i += 1 + (op - 0x5f if 0x60 <= op <= 0x7f else 0)
    return sorted(set(s for s in out if s not in ("0xffffffff", "0x00000000")))

def code_info(ca):
    code = rpc("eth_getCode", [ca, "latest"])[0] or "0x"
    info = {"codeLen": (len(code) - 2) // 2 if code.startswith("0x") else 0,
            "proxy": None, "impl": None}
    impl = None
    m = re.fullmatch(r"0x363d3d373d3d3d363d73([0-9a-f]{40})5af43d82803e903d91602b57fd5bf3", code or "")
    if m:
        impl, info["proxy"] = "0x" + m.group(1), "eip1167"
    else:
        with ThreadPoolExecutor(max_workers=3) as ex:
            vals = list(ex.map(
                lambda kv: (kv[0], rpc("eth_getStorageAt", [ca, kv[1], "latest"])[0]),
                SLOTS.items()))
        for k, v in vals:
            if v and int(v, 16):
                a = "0x" + v[-40:]
                if k == "beacon":
                    r, _ = call(a, "0x" + sel("implementation()"))
                    a = "0x" + r[-40:] if r and len(r) >= 42 else a
                impl, info["proxy"] = a, k
                break
    icode = rpc("eth_getCode", [impl, "latest"])[0] if impl else code
    info.update({"impl": impl,
                 "codeHash": khash(bytes.fromhex(icode[2:])) if icode and len(icode) > 2 else None,
                 "implLen": (len(icode) - 2) // 2 if icode and icode.startswith("0x") else 0,
                 "selectors": selectors_of(icode) if icode else [],
                 "aidInCode": (REG[2:] in (icode or "").lower()) or (REG[2:] in (code or "").lower())})
    return info

# ---------------- stage 3: abi ----------------

def sourcify_abi(addr):
    try:
        d = json.loads(http_get(
            f"https://sourcify.dev/server/v2/contract/56/{addr}?fields=abi",
            timeout=20, max_bytes=2_000_000))
        return {"match": d.get("match"), "creationMatch": d.get("creationMatch"),
                "runtimeMatch": d.get("runtimeMatch"), "abi": d.get("abi") or []}
    except Exception as e:
        return {"match": None, "creationMatch": None, "runtimeMatch": None,
                "abi": [], "error": str(e)[:200]}

def abi_sig(f):
    def tp(i):
        t = i["type"]
        if t.startswith("tuple"):
            return "(" + ",".join(tp(c) for c in i["components"]) + ")" + t[5:]
        return t
    return f["name"] + "(" + ",".join(tp(i) for i in f.get("inputs", [])) + ")"

def abi_functions(abi):
    out = []
    for e in abi or []:
        if e.get("type") == "function":
            out.append({"name": e.get("name"),
                        "inputs": [(i.get("type", ""), i.get("name", "")) for i in e.get("inputs", [])],
                        "stateMutability": e.get("stateMutability")})
    return out

def abi_for(ca, info):
    """Names + mutability for the contract. Sourcify first (implementation,
    then proxy); unknown selectors via the signature DB."""
    soc = sourcify_abi(info["impl"] or ca)
    if not soc.get("abi") and info["impl"]:
        soc = sourcify_abi(ca)
    names, stm = {}, {}
    src = None
    if soc.get("abi"):
        src = "sourcify"
        for f in soc["abi"]:
            if f.get("type") == "function":
                s = abi_sig(f)
                names[sel(s)] = s
                stm[s] = f.get("stateMutability")
    unknown = [s for s in info["selectors"] if s[2:] not in names]
    if unknown:
        got = sig_names(unknown)
        # sigdb keys carry a 0x prefix while Sourcify keys are bare selectors;
        # normalize to bare so the same function is not stored twice.
        names.update({(k[2:] if k.startswith("0x") else k): v
                      for k, v in got.items() if v})
        src = (src + "+sigdb") if src else "sigdb"
    pnames = {}
    if soc.get("abi"):
        for f in soc["abi"]:
            if f.get("type") == "function":
                pnames[sel(abi_sig(f))] = [i.get("name", "") for i in f.get("inputs", [])]
    return names, stm, soc, {"source": src, "verified": soc.get("match") == "exact_match",
                             "pnames": pnames}

# ---------------- stage 4: reads ----------------

ZERO_ARG = re.compile(r"^(name|symbol|totalSupply|totalMinted|totalSold|minted|maxSupply|MAX_SUPPLY|maxMint\w*|MAX_\w+|cap|supply\w*|price|mintPrice|cost|PRICE|MINT_PRICE|publicPrice|\w*[Pp]rice|\w*[Qq]uote\w*|\w*[Cc]ost|paused|saleActive|isSaleActive|publicSaleActive|mintActive|mintOpen|isMintOpen|isOpen|opened|saleStart\w*|saleEnd\w*|startTime|endTime|start\w*Time|end\w*Time|mintStart\w*|mintEnd\w*|deadline\w*|owner|remaining\w*|available\w*|freeMint\w*|max\w*PerWallet|maxPerTx|MAX_PER_\w+|perWallet\w*|limitPerWallet|phase|currentPhase|stage|registry|agentRegistry|REGISTRY|paymentToken|token|feeRecipient|treasury)\(\)$")
ADDR_ARG = re.compile(r"^(\w*[Pp]rice\w*|priceFor|cost\w*|remaining\w*|minted\w*|numberMinted|mintCount\w*|balanceOf|canMint|eligible\w*|isEligible|hasMinted|claimed|isWhitelisted|whitelist\w*|allowlist\w*|quota\w*|allowance\w*|mintable\w*|freeMint\w*|agentOf|isAgent)\(address\)$")
UINT_PRICE = re.compile(r"^\w*([Qq]uote|[Pp]rice|[Cc]ost)\w*\(uint256\)$")
ADDR_RESULT = {"owner()", "registry()", "agentRegistry()", "REGISTRY()",
               "paymentToken()", "token()", "feeRecipient()", "treasury()"}

def reads(ca, names, wallets, qty):
    """Generic discovery + known-template probes. Returns (global, perWallet, bals)."""
    zero = [(s, n) for s, n in names.items() if ZERO_ARG.match(n)]
    uintp = [(s, n) for s, n in names.items() if UINT_PRICE.match(n)]
    addr = [(s, n) for s, n in names.items() if ADDR_ARG.match(n)]
    jobs = [("g", n, s, None) for s, n in zero]
    jobs += [("w", n, s, w["address"]) for s, n in addr for w in wallets.values()]
    jobs += [("w", "AgentID.balanceOf(address)", None, w["address"]) for w in wallets.values()]
    jobs += [("q", n, s, qty) for s, n in uintp]
    by_name = {}
    for s, n in names.items():
        by_name.setdefault(n.split("(")[0], (s, n))

    def run(j):
        k, n, s, w = j
        if n.startswith("AgentID"):
            r, _ = call(REG, "0x" + sel("balanceOf(address)") + pad_addr(w))
        elif k == "q":
            r, _ = call(ca, "0x" + s + pad_uint(w))
        else:
            r, _ = call(ca, "0x" + s + (pad_addr(w) if w else ""))
        return r

    g, per = {}, {w["address"]: {} for w in wallets.values()}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for (k, n, s, w), r in zip(jobs, ex.map(run, jobs)):
            if r is None:
                continue
            if n in ("name()", "symbol()"):
                v = decode_string(r)
            elif n in ADDR_RESULT:
                v = "0x" + r[-40:] if r and len(r) >= 42 else None
            else:
                v = parse_uint(r)
            if v is None:
                continue
            if k == "g":
                g[n] = v
            elif k == "q":
                g["%s[qty=%d]" % (n, w)] = v
            else:
                per[w][n] = v
    # phase 2: everything that only needs phase-1 totals, run in parallel
    total = g.get("totalMinted()") if isinstance(g.get("totalMinted()"), int) else 0
    free = g.get("freeMinted()") if isinstance(g.get("freeMinted()"), int) else 0
    fmap = {}
    for s, n in names.items():
        m = re.match(r"^(\w+)\((\w+)\)$", n)
        if m:
            fmap.setdefault(m.group(1), m.group(2))
    jobs2 = []
    if "priceAt" in by_name:
        for mark in (total, free, 0):
            jobs2.append(("priceAt", f"priceAt({mark})", None,
                          "0x" + by_name["priceAt"][0] + pad_uint(mark)))
    for lbl, w in wallets.items():
        if "quoteFor" in by_name:
            jobs2.append(("quoteFor", lbl, w["address"],
                          "0x" + by_name["quoteFor"][0] + pad_uint(int(w["agent_id"], 16)) + pad_uint(1)))
        if "costOf" in by_name:
            jobs2.append(("costOf", lbl, w["address"],
                          "0x" + by_name["costOf"][0] + pad_uint(int(w["agent_id"], 16))))
        for fname in ("freeClaimed", "mintedById", "quotaOf", "inviteBonus"):
            if fname in fmap:
                arg = pad_addr(w["address"]) if fmap[fname] == "address" else pad_uint(int(w["agent_id"], 16))
                jobs2.append(("elig", (lbl, fname), w["address"],
                              "0x" + sel(f"{fname}({fmap[fname]})") + arg))
        jobs2.append(("balOf", lbl, w["address"],
                      "0x" + sel("balanceOf(address)") + pad_addr(w["address"])))
        jobs2.append(("ethbal", lbl, w["address"], None))

    def run2(j):
        kind, key, waddr, data = j
        if kind == "ethbal":
            r, _ = rpc("eth_getBalance", [waddr, "latest"])
            return r
        r, _ = call(ca, data)
        return r

    bals = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for j, r in zip(jobs2, ex.map(run2, jobs2)):
            kind, key, waddr, data = j
            if kind == "priceAt":
                v = parse_uint(r)
                if v is not None:
                    g[key] = v
            elif kind == "quoteFor":
                words = None
                if r and len(r) >= 2 + 256:
                    try:
                        words = [int(r[2 + i * 64:2 + (i + 1) * 64], 16) for i in range(4)]
                    except Exception:
                        words = None
                per[waddr]["quoteFor(agentId,1)"] = {
                    "raw_words": words, "first_word_wei": words[0] if words else None}
            elif kind == "costOf":
                v = parse_uint(r)
                if v is not None:
                    per[waddr]["costOf(agentId)"] = v
            elif kind == "elig":
                v = parse_uint(r)
                if v is not None:
                    per[waddr][key[1]] = v
            elif kind == "balOf":
                v = parse_uint(r)
                if v is not None:
                    per[waddr]["balanceOf"] = v
            elif kind == "ethbal":
                bals[key] = int(r, 16) if r else 0
    return g, per, bals

# ---------------- stage 5: simulate ----------------

ACTION = re.compile(r"^((mint|claim|buy|purchase|redeem|adopt|summon|hatch)(Public|Free|Agent|AgentId|With[A-Z]\w*|For|To|Batch|Multiple|Many|One|NFT|Nft|Card|Cards|Token|Tokens|Pass|Box|Item|Hero|Character|Pet|Land|Plot|Genesis|Shares?|Reward|Rewards|Airdrop)?|(public|free|safe|batch|whitelist|allowlist|presale|wl|og|agent|holder|early)(Mint|Claim))\($")
MIN_MINT_GAS = 45000   # a pure getter estimates ~22-30k; real mints write storage
PAYERR = re.compile(r"(?i)(underpaid|wrongpayment|insufficient ?payment|wrong ?(bnb|value|amount|price)|incorrect ?(value|payment|price)|invalidpayment|invalid ?value|not ?enough|msg\.value|value ?mismatch|payment)")

def build_args(sig, wallet, qty, pnames=None):
    m = re.search(r"\((.*)\)$", sig)
    types = [t for t in re.split(r",(?![^()]*\))", m.group(1)) if t] if m else []
    head = []
    first_uint = True
    for idx, t in enumerate(types):
        pn = (pnames[idx] if pnames and idx < len(pnames) else "") or ""
        if t.startswith("uint"):
            if re.search(r"(?i)deadline|expir|validuntil|validthrough|endtime", pn):
                head.append(int(time.time()) + 3600)
            elif first_uint:
                head.append(qty)
                first_uint = False
            else:
                head.append(0)
        elif t == "address":
            head.append(int(wallet, 16))
        elif t == "bool":
            head.append(0)
        elif t.startswith("bytes") and t[5:].isdigit():
            head.append(0)
        elif t in ("bytes", "string") or t.endswith("[]"):
            head.append(("dyn", t))
        else:
            return None   # tuples etc -> needs AI
    data, dyn = "", ""
    n = len(types)
    off = 32 * n
    for v in head:
        if isinstance(v, tuple):
            data += hex(off + len(dyn) // 2)[2:].rjust(64, "0")
            dyn += "0" * 64   # empty bytes/array/string
        else:
            data += hex(v)[2:].rjust(64, "0")
    return data + dyn

def decode_revert(err):
    if not err:
        return None
    d = err.get("data") if isinstance(err, dict) else None
    msg = (err.get("message") if isinstance(err, dict) else str(err)) or ""
    if isinstance(d, dict):
        d = d.get("data")
    if isinstance(d, str) and d.startswith("0x08c379a0"):
        return "Error: " + (decode_string("0x" + d[10:]) or "")
    if isinstance(d, str) and d.startswith("0x4e487b71"):
        try:
            return "Panic 0x%x" % int(d[10:74], 16)
        except Exception:
            return "Panic"
    if isinstance(d, str) and len(d) >= 10:
        nm = sig_names([d[:10]]).get(d[:10])
        return "CustomError " + (nm or d[:10]) + (" args=" + d[10:138] if len(d) > 10 else "")
    return msg[:160]

def price_cands(g, per, waddr):
    out = []
    def add(v, k, total=False):
        if isinstance(v, int) and 0 <= v < 10 ** 20 and v not in [x[0] for x in out]:
            out.append((v, k, total))
    per_w = per.get(waddr, {})
    add(per_w.get("priceFor(address)"), "priceFor(address)")
    for k, v in per_w.items():
        if re.search(r"(?i)(price|cost|quote)", k) and isinstance(v, int):
            add(v, k)
    for k, v in g.items():
        if "[qty=" in k:
            add(v, k, True)
    for k in ("mintPrice()", "price()", "cost()", "PRICE()", "MINT_PRICE()", "publicPrice()"):
        add(g.get(k), k)
    for k, v in g.items():
        if "[qty=" not in k and re.search(r"(?i)(price|cost|quote)", k) and \
                not re.search(r"(?i)(max|min|floor|token|usd|oracle)", k):
            add(v, k)
    add(0, "zero")
    return out[:6]

def simulate(ca, names, stm, g, per, wallets, qty, gas_price, pnames=None):
    cands = [(s, n) for s, n in names.items() if ACTION.match(n.split("(")[0] + "(")]
    if stm:
        cands = [(s, n) for s, n in cands if stm.get(n) not in ("view", "pure")]

    def run_one(s, n, lbl, w):
        args = build_args(n, w, qty, (pnames or {}).get(s))
        if args is None:
            return {"fn": n, "wallet": lbl, "ok": False,
                    "revert": "complex args (tuple/bytes) -> needs AI", "valueWei": 0}
        data = "0x" + s + args
        uses_qty = bool(re.search(r"\(.*uint", n))
        tried = []
        queue = [(v * (qty if uses_qty else 1) if not tot else v, k)
                 for v, k, tot in price_cands(g, per, w)]
        last = None
        while queue and len(tried) < 7:
            val, pk = queue.pop(0)
            if val in [t[0] for t in tried]:
                continue
            r, e = call(ca, data, w, val, fund=True)
            tried.append((val, pk))
            if e is None and r is not None:
                tx = {"from": w, "to": ca, "data": data, "value": hex(val)}
                gas, _ = estimate_gas(tx, w)
                if gas and gas < MIN_MINT_GAS:
                    return {"fn": n, "wallet": lbl, "valueWei": 0, "ok": False,
                            "revert": "succeeds with %d gas -> looks like a getter, not a mint" % gas}
                return {"fn": n, "wallet": lbl, "valueWei": val, "valueBNB": val / 1e18,
                        "priceFrom": pk, "ok": True, "gas": gas, "revert": None,
                        "tried": len(tried),
                        "calldata": data[:10] + "…(%d bytes)" % ((len(data) - 2) // 2)}
            last = decode_revert(e)
            if last and re.search(r"(?i)remote end|connection|timed? ?out|429|403|http error|eof|reset by peer|temporarily|unreachable", last):
                last = "TRANSPORT: " + last
            d = e.get("data") if isinstance(e, dict) else None
            if isinstance(d, dict):
                d = d.get("data")
            if last and PAYERR.search(last) and isinstance(d, str) and len(d) > 10:
                for i in range(10, len(d), 64):
                    try:
                        v = int(d[i:i + 64] or "0", 16)
                    except Exception:
                        continue
                    if 0 < v < 10 ** 20:
                        queue.insert(0, (v, "revertHint"))
        return {"fn": n, "wallet": lbl, "valueWei": tried[0][0] if tried else 0,
                "valueBNB": (tried[0][0] / 1e18) if tried else 0,
                "priceFrom": tried[0][1] if tried else None, "ok": False,
                "gas": None, "revert": last, "tried": len(tried)}

    jobs = [(s, n, lbl, w["address"]) for s, n in cands for lbl, w in wallets.items()]
    with ThreadPoolExecutor(max_workers=8) as ex:
        sims = list(ex.map(lambda j: run_one(*j), jobs))
    bals = {}
    for lbl, w in wallets.items():
        r, _ = rpc("eth_getBalance", [w["address"], "latest"])
        bals[lbl] = int(r, 16) if r else 0
    for sm in sims:
        if sm["ok"]:
            cost = sm["valueWei"] + (sm["gas"] or 300000) * gas_price
            if cost > bals.get(sm["wallet"], 0):
                sm["balanceShort"] = True
    return sims, cands, bals

# ---------------- stage 6: risk ----------------

DANGER = re.compile(r"(?i)^(approve|setApprovalForAll|transferFrom|permit|increaseAllowance|setOperator|delegate)\(")
OWNER_FUNCS = {"setPrice", "setMintPrice", "pause", "setPaused", "withdraw",
               "setMintOpen", "setBaseURI", "transferOwnership", "setFeeRecipient",
               "setMaxSupply", "setURI", "setRoyalty"}

def risk_flags(info, names, abi_meta, sims, g):
    flags = []
    if info["proxy"]:
        flags.append("可升级代理（%s），逻辑可变" % info["proxy"])
    if not abi_meta["verified"]:
        flags.append("Sourcify 不是 exact_match，函数名来自签名库或未核实")
    if info["implLen"] and info["implLen"] < 1500:
        flags.append("代码很小（%d 字节），疑似异常小合约" % info["implLen"])
    ok = [s for s in sims if s["ok"]]
    if any(s["valueWei"] for s in ok):
        flags.append("付费铸造：需付 BNB")
    pt = g.get("paymentToken()")
    if isinstance(pt, str) and pt != "0x0000000000000000000000000000000000000000":
        flags.append("ERC20 计价（paymentToken=%s），需授权，绝不自动授权" % pt)
    if any(DANGER.match(s["fn"].split("(")[0] + "(") for s in sims):
        flags.append("候选里有授权类函数，不可当 mint 打")
    if any("allowance" in (s["revert"] or "").lower() or
           "transferfrom" in (s["revert"] or "").lower() for s in sims):
        flags.append("回滚提到 allowance/transferFrom，疑似 ERC20 付款，绝不自动授权")
    names_set = {n.split("(")[0] for n in names.values()}
    risky = sorted(names_set & OWNER_FUNCS)
    if risky:
        flags.append("owner 保留权限函数：" + ", ".join(risky) + "（可改价/开关/提款/改 URI，见源码再定）")
    if g.get("paused()") == 1:
        flags.append("paused=1，已暂停")
    if g.get("mintOpen()") == 0:
        flags.append("mintOpen=0，未开放")
    if g.get("opened()") == 0:
        flags.append("opened=0，池子未开（不等于不能铸，只说明无卖出路径）")
    return flags

def time_flags(g):
    now = int(time.time())
    out = {}
    for k, v in g.items():
        if isinstance(v, int) and 1.6e9 < v < 2.2e9 and \
                re.search(r"(?i)(start|end|deadline|time|open|close)", k):
            out[k] = {"cst": time.strftime("%Y-%m-%d %H:%M", time.gmtime(v + 8 * 3600)),
                      "past": v < now}
    return out

# ---------------- history (v1, human-reviewed only) ----------------

def history_match(funcs, hist_path):
    try:
        hist = json.load(open(hist_path))
    except Exception:
        return []
    new = {f["name"] for f in funcs}
    new_mints = {f["name"] for f in funcs
                 if "mint" in f["name"].lower() or "claim" in f["name"].lower()}
    scored = []
    for h in hist:
        old = set(h.get("abi_functions") or [])
        if not old and not new:
            continue
        inter = len(new & old)
        union = len(new | old) or 1
        overlap = round(inter / min(len(new), len(old)), 3) if min(len(new), len(old)) else 0
        reusable = bool(h.get("mint_function") and
                        h["mint_function"].split("(")[0] in new_mints and overlap >= 0.8)
        scored.append({"project": h.get("project"), "contract": h.get("contract"),
                       "similarity": round(inter / union, 3), "overlap": overlap,
                       "reusable_method": reusable, "mint_function": h.get("mint_function"),
                       "outcome": h.get("outcome"), "failure_reason": h.get("failure_reason"),
                       "notes": h.get("notes")})
    scored.sort(key=lambda x: (-x["overlap"], -x["similarity"]))
    return scored[:3]

# ---------------- verdict ----------------

def decide(ca, info, abi_meta, g, sims, cands, tf, hist_free_total=None):
    sold = next((g[k] for k in ("totalMinted()", "totalSold()", "minted()",
                                "totalMintedShares()")
                 if isinstance(g.get(k), int)), None)
    if sold is None and isinstance(g.get("totalSupply()"), int) and g["totalSupply()"] < 10 ** 7:
        sold = g["totalSupply()"]
    cap = next((v for k, v in g.items()
                if re.match(r"(?i)(max_?supply|cap|MAX_SUPPLY|MAX_SHARES)\(\)", k) and isinstance(v, int)), None)
    free_total = next((v for k, v in g.items()
                       if re.match(r"(?i)(FREE_SUPPLY|FREE_SHARES|MAX_FREE_CLAIMS|MAX_FREE_SHARES|maxFreeClaims)\(\)", k) and isinstance(v, int)), None)
    if free_total is None:
        free_total = hist_free_total
    free_minted = next((g[k] for k in ("freeMinted()", "freeMintedShares()", "freeClaimedShares()")
                        if isinstance(g.get(k), int)), None)
    remaining = (cap - sold) if (cap is not None and sold is not None) else None
    free_ok = [s for s in sims if s["ok"] and not s["valueWei"]]
    paid_ok = [s for s in sims if s["ok"] and s["valueWei"]]
    okw = sorted({s["wallet"] for s in sims if s["ok"]})
    best = (free_ok or paid_ok or [None])[0]
    reverts = sorted({s["revert"] for s in sims if s["revert"]})[:4]

    status, conclusion, needs_ai, hint = None, None, False, None
    if info["codeLen"] == 0:
        status, conclusion = "NO_CONTRACT", "现在打不了：合约无代码"
    elif free_total is not None and free_minted is not None and free_minted >= free_total:
        status, conclusion = "SOLD_OUT", "现在打不了：免费档已耗尽，只剩付费档（付费需用户另定）"
    elif g.get("paused()") == 1:
        status, conclusion = "CLOSED", "现在打不了：已暂停"
    elif g.get("mintOpen()") == 0:
        status, conclusion = "CLOSED", "现在打不了：未开放"
    elif not cands:
        status, conclusion, needs_ai = "NO_MINT_FN", "需人工分析：ABI 里没找到 mint 类函数", True
        hint = "读 Sourcify 源码或网站 JS，找真正的 mint 入口"
    elif free_ok or paid_ok:
        status = "OPEN_FREE" if free_ok else "OPEN_PAID"
        if abi_meta["verified"]:
            conclusion = "可打（等确认）：预检通过，仍需用户确认数量后再用自己的钱包预览/签名"
            if status == "OPEN_PAID":
                conclusion += "（付费档，需用户另定打几份）"
        else:
            conclusion = "需人工分析：源码未验证，模拟通过，仅供人工确认后走 preview（不直接打）"
            needs_ai = True
            hint = "人工读 Sourcify/源码确认 mint 逻辑与 owner 权限后再定"
    else:
        ends = [v for k, v in tf.items() if re.search(r"(?i)(end|deadline|close)", k)]
        starts = [v for k, v in tf.items() if re.search(r"(?i)start", k)]
        real_revs = [s["revert"] for s in sims
                     if s["revert"] and not s["revert"].startswith("TRANSPORT:")]
        all_rev = " ".join(real_revs)
        if sims and not real_revs:
            status, conclusion = "UNKNOWN", "链路不稳：模拟都没跑通，先重跑一次再定"
        elif ends and all(e["past"] for e in ends):
            status, conclusion = "ENDED", "现在打不了：已结束"
        elif starts and any(not x["past"] for x in starts):
            status, conclusion = "NOT_STARTED", "现在打不了：未开始"
        elif remaining == 0 or re.search(r"(?i)sold ?out|max ?supply|exceeds? ?supply|supply ?exceeded", all_rev):
            status, conclusion = "SOLD_OUT", "现在打不了：已售罄"
        elif sims and real_revs and all(re.search(r"(?i)already|claimed|minted|limit|quota|exceed", r)
                                        for r in real_revs):
            status, conclusion = "WALLET_LIMIT", "现在打不了：两钱包额度已用完"
        elif re.search(r"(?i)not ?(active|open|started|live)|closed|paused|inactive", all_rev):
            status, conclusion = "CLOSED", "现在打不了：未开放/已关闭"
        elif any(re.search(r"bytes(?!\d)|bytes32\[\]", (x.get("fn") or "").split("(",1)[-1])
                 and re.search(r"(?i)mint|claim|buy", x.get("fn") or "") for x in sims):
            status = "SIGNED_MINT"
            conclusion, needs_ai = "现在不能直接链上打：mint 需要项目方后端签名或白名单证明（bytes 参数），要走官网/项目 API 拿签名", True
            hint = "找项目方签名接口（官网 JS / API），或用专用脚本（如 Agent Cards 的 kart）"
        else:
            status = "NOT_MINTABLE_NOW"
            complex_args = any("needs AI" in (s["revert"] or "") for s in sims)
            unknown_price = any(PAYERR.search(s["revert"] or "") for s in sims)
            if complex_args or unknown_price or not sims:
                conclusion, needs_ai = "需人工分析：模拟失败，价格或参数未识别", True
                hint = "读源码/网站 JS 找价格函数、签名或证明参数，再重跑"
            else:
                conclusion = "现在打不了：模拟失败（%s）" % (reverts[0][:90] if reverts else "未知回滚")
    return {"status": status, "walletsOK": okw, "bestCall": best, "needsAI": needs_ai,
            "aiHint": hint, "supply": {"minted": sold, "cap": cap, "remaining": remaining,
                                       "freeMinted": free_minted, "freeTotal": free_total},
            "reverts": reverts, "conclusion": conclusion}

# ---------------- summary ----------------

ZH = {"OPEN_FREE": "可免费铸造", "OPEN_PAID": "开放·付费", "SOLD_OUT": "已售罄",
      "ENDED": "已结束", "CLOSED": "未开放/已关闭", "NOT_STARTED": "未开始",
      "WALLET_LIMIT": "钱包额度已用完", "NOT_MINTABLE_NOW": "当前模拟失败",
      "NO_MINT_FN": "未识别到 Mint 函数", "NO_CONTRACT": "未找到合约"}

def human_summary(r):
    v = r["verdict"]
    b = v.get("bestCall") or {}
    sup = v.get("supply") or {}
    sims = r.get("simulations") or []
    lines = [f"预检：{r.get('name') or r['contract']}【{ZH.get(v['status'], v['status'])}】"]
    lines.append(f"合约：{r['contract']}，Sourcify：{r['sourcify'].get('match')}，"
                 f"代码 {r.get('code_bytes')} 字节"
                 + (f"，代理 {r.get('proxy')} → 实现 {r.get('impl')}" if r.get("proxy") else ""))
    if sup.get("minted") is not None:
        lines.append(f"总量/已铸：{sup['minted']} / {sup.get('cap') or '未提供'}"
                     + (f" · 剩 {sup['remaining']}" if sup.get("remaining") is not None else "")
                     + (f"；免费已铸 {sup.get('freeMinted')} / {sup.get('freeTotal')}" if sup.get("freeTotal") is not None else ""))
    if b.get("fn"):
        lines.append(f"函数 {b['fn']} · 每钱包本次需付 {b.get('valueBNB', 0) or 0} BNB · gas≈{b.get('gas')}")
    lines.append(f"可铸钱包 {','.join(v.get('walletsOK') or []) or '无'} · Agent ID 门槛 {'是' if r.get('aidGated') else '否'}"
                 + (f" · 余额不足：{','.join(sorted({s['wallet'] for s in sims if s.get('balanceShort')}))}"
                    if any(s.get("balanceShort") for s in sims) else ""))
    if r.get("history_match"):
        top = r["history_match"][0]
        lines.append(f"最像历史项目：{top['project']}，相似度 {top['similarity']}，overlap {top['overlap']}，"
                     f"mint 方法 {top['mint_function']}（方法可复用，价格与参数已本轮重核）")
    if r.get("times"):
        lines.append("时间 " + "；".join("%s=%s%s" % (k, x["cst"], "(已过)" if x["past"] else "")
                                        for k, x in r["times"].items()))
    if r.get("risk_flags"):
        lines.append("风险：" + "；".join(r["risk_flags"]))
    if v.get("reverts"):
        lines.append("回滚 " + "；".join(x[:90] for x in v["reverts"]))
    if v.get("needsAI"):
        lines.append("⚠ 需要 AI 深挖：" + (v.get("aiHint") or "价格/参数未识别"))
    lines.append(f"结论：{v['conclusion']}")
    lines.append(f"耗时 {r.get('total_s')}s（{json.dumps(r.get('timings', {}), ensure_ascii=False)}）· 未签名 未授权 未付款")
    return "\n".join(lines)

# ---------------- per-target pipeline ----------------

def run_target(inp, qty, wallets, hist_path):
    t = {"_start": time.time()}

    def stage(name):
        print(f"[precheck] {inp[:20]} {name} {time.time() - t['_start']:.1f}s",
              file=sys.stderr, flush=True)

    cands, note = resolve_input(inp)
    t["resolve"] = round(time.time() - t["_start"], 3)
    stage("resolve")
    if not cands:
        return {"input": inp, "contract": None, "candidates": [],
                "verdict": {"status": "NO_CONTRACT", "conclusion": "需人工分析：网址里没找到可信的铸造合约",
                            "needsAI": True, "aiHint": "直接给 CA 再跑"},
                "resolve_note": note,
                "timings": {k: v for k, v in t.items() if k != "_start"}}
    rpc_url = pick_rpc()
    t["rpc_pick"] = round(time.time() - t["_start"], 3)
    if not rpc_url:
        return {"input": inp, "contract": cands[0],
                "verdict": {"status": "NO_CONTRACT", "conclusion": "需人工分析：BSC RPC 全断",
                            "needsAI": True, "aiHint": None},
                "timings": {k: v for k, v in t.items() if k != "_start"}}
    gas_price = 10 ** 9
    gp, _ = rpc("eth_gasPrice", [])
    if gp:
        try:
            gas_price = int(gp, 16)
        except Exception:
            pass
    results = []
    for ca in cands[:3]:
        info = code_info(ca)
        t["code"] = round(time.time() - t["_start"], 3)
        stage("code")
        names, stm, soc, abi_meta = abi_for(ca, info)
        t["abi"] = round(time.time() - t["_start"], 3)
        stage("abi")
        g, per, bals = reads(ca, names, wallets, qty)
        t["reads"] = round(time.time() - t["_start"], 3)
        stage("reads")
        sims, sim_cands, bals2 = simulate(ca, names, stm, g, per, wallets, qty, gas_price,
                                          abi_meta.get("pnames"))
        t["simulate"] = round(time.time() - t["_start"], 3)
        stage("simulate")
        flags = risk_flags(info, names, abi_meta, sims, g)
        tf = time_flags(g)
        hist = history_match([{"name": n.split("(")[0]} for n in names.values()], hist_path)
        hist_free_total = None
        for h in hist:
            if (h.get("contract") or "").lower() == ca.lower() and \
                    h.get("outcome") == "failed_free_sold_out":
                m = re.search(r"freeMinted\s*(\d+)\s*/\s*(\d+)", h.get("failure_reason") or "")
                if m:
                    hist_free_total = int(m.group(2))
                break
        verdict = decide(ca, info, abi_meta, g, sims, sim_cands, tf, hist_free_total)
        r = {"input": inp, "contract": ca, "rpc": rpc_url, "resolve_note": note,
             "candidates": cands, "code_bytes": info["codeLen"],
             "proxy": info["proxy"], "impl": info["impl"], "codeHash": info["codeHash"],
             "selectorCount": len(info["selectors"]), "aidGated": info["aidInCode"],
             "sourcify": {k: soc.get(k) for k in ("match", "creationMatch", "runtimeMatch")},
             "abi_source": abi_meta["source"], "abi_verified": abi_meta["verified"],
             "name": g.get("name()"), "symbol": g.get("symbol()"),
             "global": g, "perWallet": {lbl: per[w["address"]] for lbl, w in wallets.items()},
             "balances": {k: round(v / 1e18, 6) for k, v in bals2.items()},
             "mintCandidates": [n for _, n in sim_cands],
             "simulations": sims, "risk_flags": flags, "times": tf,
             "history_match": hist, "verdict": verdict,
             "timings": {k: v for k, v in t.items() if k != "_start"},
             "total_s": round(time.time() - t["_start"], 2)}
        results.append(r)
        if verdict["status"].startswith("OPEN") or sim_cands:
            break   # found the mint contract; other candidates are backups
    if results:
        best = next((r for r in results
                     if r["mintCandidates"] or r["verdict"]["status"].startswith("OPEN")),
                    results[0])
        return best
    return {
        "input": inp, "contract": None, "candidates": cands,
        "verdict": {"status": "NO_CONTRACT", "conclusion": "需人工分析：候选合约均未跑出结果",
                    "needsAI": True, "aiHint": None},
        "timings": {k: v for k, v in t.items() if k != "_start"}}

def lock_append(path, line):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        with open(path, "a") as f:
            f.write(line + "\n")

def record_outcome(ca, outcome, reason, tx):
    rec = {"at": time.strftime("%Y-%m-%d %H:%M CST", time.gmtime(time.time() + 8 * 3600)),
           "ca": ca.lower(), "outcome": outcome, "reason": reason, "tx": tx}
    lock_append(os.path.join(BASE, "records.jsonl"), json.dumps(rec, ensure_ascii=False))
    print(json.dumps(rec, ensure_ascii=False))
    print("已写入 records.jsonl（原始记录）；入库 history.json 仍需人工复核。", file=sys.stderr)
    return 0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", action="append", default=[])
    ap.add_argument("--human", action="store_true")
    ap.add_argument("--no-record", action="store_true")
    ap.add_argument("--qty", type=int, default=1)
    ap.add_argument("--wallets", default=None)
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--tx", default=None)
    ap.add_argument("cmd", nargs="?", default=None)
    ap.add_argument("rest", nargs="*")
    args = ap.parse_args()

    if args.cmd == "record":
        if len(args.rest) < 2:
            print("用法：precheck.py record <CA> success|fail \"原因\" [--tx 0x...]", file=sys.stderr)
            return 1
        tx = args.tx
        return record_outcome(args.rest[0], args.rest[1],
                              args.rest[2] if len(args.rest) > 2 else "", tx)
    if args.cmd == "lib":
        try:
            hist = json.load(open(os.path.join(BASE, "history.json")))
            print(json.dumps({h.get("contract"): {"project": h.get("project"),
                             "mint_function": h.get("mint_function"),
                             "outcome": h.get("outcome")} for h in hist},
                             ensure_ascii=False, indent=1))
        except Exception as e:
            print(json.dumps({"error": str(e)[:200]}, ensure_ascii=False))
        return 0

    wallets = _parse_wallets(args.wallets) if args.wallets else _load_wallets()
    if not wallets:
        print("提示：未配置钱包（--wallets 或 BSC_PRECHECK_WALLETS），只做合约级预检，不查钱包资格。", file=sys.stderr)
    inputs = args.input or []
    if args.cmd and not inputs and (args.cmd.startswith("0x") or args.cmd.startswith("http")):
        inputs = [args.cmd]
    if not inputs:
        print("用法：precheck.py --input <网址或CA> [--human] [--qty N] [--wallets 0x..,0x..] [--full]",
              file=sys.stderr)
        return 1
    hist_path = os.path.join(BASE, "history.json")
    if len(inputs) == 1:
        reports = [run_target(inputs[0], args.qty, wallets, hist_path)]
    else:
        with ThreadPoolExecutor(max_workers=min(4, len(inputs))) as ex:
            reports = list(ex.map(lambda i: run_target(i, args.qty, wallets, hist_path), inputs))
    out = reports[0] if len(reports) == 1 else {"results": reports}
    if not args.full and len(reports) == 1:
        r = reports[0]
        out = {k: r.get(k) for k in (
            "input", "contract", "rpc", "resolve_note", "candidates", "code_bytes",
            "proxy", "impl", "codeHash", "selectorCount", "aidGated", "sourcify",
            "abi_source", "abi_verified", "name", "symbol", "balances",
            "mintCandidates", "simulations", "risk_flags", "times", "history_match",
            "verdict", "timings", "total_s")}
        out["supply"] = r.get("verdict", {}).get("supply")
        out["global_views"] = {k: v for k, v in (r.get("global") or {}).items()
                               if not isinstance(v, dict)}
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    if args.human:
        print("\n--- 摘要 ---", file=sys.stderr)
        for r in reports:
            print(human_summary(r), file=sys.stderr)
            if len(reports) > 1:
                print("---", file=sys.stderr)
    if not args.no_record:
        try:
            for r in reports:
                lock_append(os.path.join(BASE, "runs", "runs.jsonl"),
                            json.dumps({"ts": time.time(), "input": r.get("input"),
                                        "contract": r.get("contract"),
                                        "status": (r.get("verdict") or {}).get("status"),
                                        "conclusion": (r.get("verdict") or {}).get("conclusion")},
                                       ensure_ascii=False))
                lock_append(os.path.join(BASE, "timings.jsonl"),
                            json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S CST",
                                        time.gmtime(time.time() + 8 * 3600)),
                                        "input": r.get("input"),
                                        "status": (r.get("verdict") or {}).get("status"),
                                        "total_s": r.get("total_s"),
                                        "stages": r.get("timings")}, ensure_ascii=False))
        except Exception:
            pass
    return 0

if __name__ == "__main__":
    sys.exit(main())
