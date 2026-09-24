#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_prime.py — 抓「Prime 专享折扣价」（前台实时 / BrowserSkill 通道），回填 monitor_data.json。

为什么要前台实时：
  SellerSprite getAsinDetail.primePrice 会**成批漏抓**——实测同一批 ASIN 在
  11:46 还返回真实值（18.99/19.99/29.99/29.99/32.99），11:50 同批同 updatedTime
  全部变回 -1；而 amazon.com 前台那几个 ASIN 当时都挂着 "Exclusive Prime price"，
  价格与接口一致。同一时段 coupon 字段也整列变空，属同一类问题。
  → Prime 专享是实时促销维度，按本技能「第一铁律」以前台实时为准。

口径：
  - 前台价格容器命中 primeNote（"Exclusive Prime price" 等文案）或 corePrime
    → 该 ASIN 在做 Prime 专享折扣，值 = 前台当前到手价。
  - 未命中 → prime_price = None（日报该格留空）。
  - 前台整体不可用（浏览器未开 / 扩展未连 / CLI 缺失）或某行抓取失败
    → 保留 fetch_sellersprite.py 写入的接口值，标注 prime_source 为兜底，
      并打印显式告警（绝不静默当成「没做折扣」）。

依赖：
  - bsk CLI（BrowserSkill），路径解析：环境变量 BSK_PATH → PATH → ~/.local/bin/bsk.exe
    → config.prime.bskPath
  - 浏览器里的 BrowserSkill 扩展处于已连接状态（bsk doctor 的 extension connected 为 ok）

用法：
  python fetch_prime.py [monitor_data.json] [prime_frontend.json]
"""
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time

import cfgpath

HERE = os.path.dirname(os.path.abspath(__file__))
PROBE = os.path.join(HERE, "frontend_prime_probe.js")

SESSION_DEAD = ("does not exist", "not registered", "already stopped", "no active session")


def find_bsk(cfg):
    p = (os.environ.get("BSK_PATH") or "").strip()
    if p and os.path.exists(p):
        return p
    for name in ("bsk", "bsk.exe"):
        w = shutil.which(name)
        if w:
            return w
    legacy = os.path.join(os.path.expanduser("~"), ".local", "bin", "bsk.exe")
    if os.path.exists(legacy):
        return legacy
    p = ((cfg.get("prime") or {}).get("bskPath") or "").strip()
    return p if p and os.path.exists(p) else None


def make_runner(bsk, timeout):
    def run(args, tmo=None):
        p = subprocess.run([bsk] + args, capture_output=True, encoding="utf-8",
                           errors="replace", timeout=tmo or timeout,
                           cwd=os.path.dirname(bsk) or None)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    return run


def first_session(out):
    for line in (out or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[0] != "SESSION":
            return parts[0]
    return None


def ensure_session(run, timeout, force_new=False):
    if not force_new:
        _rc, out, _err = run(["session", "list"], 60)
        sid = first_session(out)
        if sid:
            return sid
    _rc, out, _err = run(["session", "start", "--json"], timeout)
    m = re.search(r'"session_id"\s*:\s*"([^"]+)"', out or "")
    if m:
        return m.group(1)
    _rc, out, _err = run(["session", "list"], 60)
    return first_session(out)


def probe_once(asin, sid, run, domain):
    url = "https://%s/dp/%s?language=en_US" % (domain, asin)
    rc, out, err = run(["navigate", "--session", sid, url])
    if rc != 0:
        return {"err": "navigate rc=%s %s %s" % (rc, out[:100], err[:100])}
    try:
        js = open(PROBE, encoding="utf-8").read()
    except Exception as e:
        return {"err": "探针脚本读取失败 %s" % e}
    rc, out, err = run(["evaluate", "--session", sid, js], 120)
    if rc != 0 or not out:
        return {"err": "evaluate rc=%s %s" % (rc, err[:120])}
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                continue
    return {"err": "无法解析返回值：%s" % out[:140]}


def probe(asin, sid, run, timeout, domain):
    sid = sid or ensure_session(run, timeout)
    if not sid:
        return {"err": "无活跃会话：确认浏览器已装 BrowserSkill 扩展且图标为绿"}, None
    r = probe_once(asin, sid, run, domain)
    if r.get("err") and any(k in r["err"].lower() for k in SESSION_DEAD):
        time.sleep(1)
        sid2 = ensure_session(run, timeout, force_new=True)
        if sid2:
            r2 = probe_once(asin, sid2, run, domain)
            if not r2.get("err"):
                return r2, sid2
            sid = sid2
    return r, sid


def to_usd(v):
    if not v:
        return None
    try:
        return round(float(str(v).replace("$", "").replace(",", "").strip()), 2)
    except ValueError:
        return None


def judge_prime(fe):
    """前台读数 → (prime_value | None, reason)。"""
    price = to_usd(fe.get("price"))
    note = (fe.get("primeNote") or "").strip()
    core = bool(fe.get("corePrime"))
    if price is None:
        return None, "无前台价格"
    if note or core:
        why = []
        if note:
            why.append("文案=%s" % note)
        if core:
            why.append("corePrime")
        return price, "；".join(why)
    return None, "无 Prime 专享标记"


def main():
    cfg = cfgpath.load_config()
    prime_cfg = cfg.get("prime") or {}
    timeout = int(prime_cfg.get("timeoutSeconds", 150))
    domain = ((cfg.get("site") or {}).get("domain") or "www.amazon.com").strip()

    data_path = sys.argv[1] if len(sys.argv) > 1 else cfgpath.out_path("monitor_data.json", cfg=cfg)
    out_path = sys.argv[2] if len(sys.argv) > 2 else cfgpath.out_path("prime_frontend.json", cfg=cfg)

    with open(data_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    asins = [r["asin"] for r in payload.get("rows", [])]

    if prime_cfg.get("enabled") is False:
        print("config.prime.enabled=false → 跳过前台抓取，保留接口值。")
        return 0

    result = {
        "date": datetime.date.today().isoformat(),
        "fetched_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "BrowserSkill 前台实时（amazon.com 商品详情页）",
        "available": False, "items": {}, "errors": {},
    }

    bsk = find_bsk(cfg)
    if not bsk:
        print("⚠ 未找到 bsk CLI（BrowserSkill）→ Prime 专享列保留接口值，"
              "接口该字段会成批漏抓，请人工复核。")
    else:
        run = make_runner(bsk, timeout)
        sid = ensure_session(run, timeout, force_new=True)
        if not sid:
            print("⚠ 无活跃 bsk 会话（浏览器未开 / 扩展未连接）→ 保留接口值")
        else:
            print("bsk session 就绪 | 待抓 %d 个 ASIN" % len(asins))
            result["available"] = True
            try:
                for i, a in enumerate(asins, 1):
                    t0 = time.time()
                    r, sid = probe(a, sid, run, timeout, domain)
                    cost = round(time.time() - t0, 1)
                    if r.get("err"):
                        result["errors"][a] = r["err"][:160]
                        print("[%d/%d] %s ✗ %s (%ss)" % (i, len(asins), a, r["err"][:70], cost),
                              flush=True)
                        continue
                    val, why = judge_prime(r)
                    result["items"][a] = {
                        "prime": val, "price": r.get("price"), "basis": r.get("basis"),
                        "corePrime": bool(r.get("corePrime")),
                        "primeNote": r.get("primeNote") or "",
                        "reason": why, "dealBadge": r.get("dealBadge") or [],
                    }
                    print("[%d/%d] %s  前台=%s  Prime=%s  (%s) (%ss)"
                          % (i, len(asins), a, r.get("price") or "—",
                             ("$%.2f" % val) if val else "无", why, cost), flush=True)
            finally:
                try:
                    run(["session", "stop", sid], 60)
                except Exception:
                    pass

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    filled, cleared, kept = [], [], []
    for r in payload.get("rows", []):
        asin = r["asin"]
        fe = result["items"].get(asin)
        if fe is not None:
            if fe["prime"] is not None:
                r["prime_price"] = fe["prime"]
                r["prime_source"] = "前台实时（%s）" % fe["reason"]
                filled.append((asin, r.get("title", ""), fe["prime"]))
            else:
                r["prime_price"] = None
                r["prime_source"] = "前台实时（无 Prime 专享标记）"
                cleared.append(asin)
        elif r.get("prime_price"):
            r["prime_source"] = "接口兜底（前台未取到）"
            kept.append((asin, r.get("title", ""), r["prime_price"]))
        else:
            r["prime_source"] = "无（前台未取到）"

    payload["prime_fetch"] = {"available": result["available"],
                              "fetched_at": result["fetched_at"],
                              "ok": len(result["items"]), "err": len(result["errors"])}
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print()
    print("Prime 专享折扣 ASIN（前台实时确认 %d 个）：" % len(filled))
    for a, t, v in filled:
        print("  %s %s  Prime$%.2f" % (a, t, v))
    if not filled:
        print("  （无）")
    if cleared:
        print("前台确认无 Prime 专享标记 %d 个" % len(cleared))
    if kept:
        print("⚠ 前台未取到、沿用接口值 %d 个（该字段不稳，务必复核）：" % len(kept))
        for a, t, v in kept:
            print("  %s %s  接口值=%.2f" % (a, t, v))
    if result["errors"]:
        print("抓取失败 %d 个：" % len(result["errors"]))
        for a, e in result["errors"].items():
            print("  %s  %s" % (a, e[:90]))
    print("已写入 %s 与 %s" % (out_path, data_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
