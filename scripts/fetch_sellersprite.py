#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_sellersprite.py — 拉取监控清单的日报字段，写入 monitor_data.json。

数据源：sellersprite-universal MCP（url/鉴权自 mcp.json 读取，见 mcpclient.py）
  今日：getAsinDetail       → price / discount / coupon / primePrice / fulfillment /
                              sellerId / sellerName / ratings / rating / bsrRank / subcategories
  昨日：getAsinDetailHistory → 昨日那条的 couponPrice（判「昨日有无券」）

为什么要脚本化：字段靠人工回填极易漏（历史上发生过券列整列漏填），脚本化后
取数 / 格式化 / 环比全部确定性执行，LLM 不再经手任何数值。

用法：
  python fetch_sellersprite.py [monitor_data.json] [昨日日期 YYYY-MM-DD]
默认：
  monitor_data.json = <outputs.dir>/monitor_data.json（见 config.json）
  昨日日期          = 今天 - 1 天

产出：
  1) 覆盖写 monitor_data.json 的 rows，保留其它脚本注入的行内字段（fba_* 等），
     并更新 snapshot_time。
  2) 滚动三份环比基线：ratings_baseline.json / price_baseline.json / rank_baseline.json
  3) 写 coupons.json 备查（含每个 ASIN 今日券值、昨日有无券、updatedTime）。

口径必读：
  - 价格环比用 getAsinDetail.price（到手价）+ 自建 price_baseline.json。
    绝不可用 getAsinDetailHistory.price —— 那是标价（本例中某 ASIN 标价长期 59.99
    而到手价 41.99），混用会让整列误报「异常」。
  - coupon 展示用 getAsinDetail.coupon 原始值（"2.00" / "5%"）；「昨日有无券」用
    history.couponPrice>0 判断（该值是券面额，百分比券会被折成金额，不能用于展示）。
  - 该接口的 coupon 与 primePrice 都会**成批漏抓**：同一批数据可能在几分钟内
    从有值变成全空/全 -1。因此 coupon 有 ERP 兜底（apply_erp_coupons.py）、
    Prime 专享有前台实时兜底（fetch_prime.py），本脚本的 primePrice 仅作最后兜底。
  - 排名：大类 = bsrRank（一级类目），小类 = subcategories[0].rank（末级类目）。
    波动 = 与上次监控的绝对值差；阈值见 config.thresholds。
    只有基线里的类目名与今日一致时才环比（类目被平台调整过则不可比，跳过并告警）。
"""
import datetime
import json
import sys

import cfgpath
from mcpclient import McpClient, load_server_config


def norm_coupon(v):
    """券值归一化：空/0/-1 -> ""；数值 -> "2.00"；百分比 -> "5%"。"""
    if v is None:
        return ""
    s = str(v).strip()
    if s in ("", "0", "0.0", "0.00", "-1", "-1.0", "None", "null"):
        return ""
    if s.endswith("%"):
        return s
    try:
        f = float(s)
        return "" if f <= 0 else "%.2f" % f
    except ValueError:
        return s


def norm_prime(v):
    """Prime 专享折扣价：-1/0/空/非数字 -> None（未做折扣，日报留空）；>0 -> float。"""
    if v is None:
        return None
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def norm_rank(v):
    if isinstance(v, int) and v > 0:
        return v
    return None


def get_price(d):
    p = d.get("price")
    if p is None or (isinstance(p, (int, float)) and p <= 0):
        p = d.get("primePrice")
    if p is None or (isinstance(p, (int, float)) and p <= 0):
        return None
    return "$%.2f" % float(p)


def pick_subcategory(d):
    """小类排名：subcategories 里第一个有效 rank（= 平台主类目）。
    个别 ASIN 会同时挂多个小类（如灯具类 ASIN 同时挂 Jewelry Making Kits / Light Bulbs），
    固定取第一个，与 bsrLabel 的主类口径一致。返回 (rank|None, label)。"""
    for s in (d.get("subcategories") or []):
        r = s.get("rank")
        if isinstance(r, int) and r > 0:
            return r, (s.get("label") or "")
    return None, ""


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def main():
    cfg = cfgpath.load_config()
    rows_cfg = cfgpath.asin_list(cfg)
    if not rows_cfg:
        print("✗ config.json 的 asinList 为空，无可监控对象。")
        return 1
    site = cfg.get("site") or {}
    market = (site.get("marketplace") or "US").upper()
    brand_ids = cfgpath.seller_ids(cfg)
    th = cfgpath.thresholds(cfg)

    data_path = sys.argv[1] if len(sys.argv) > 1 else cfgpath.out_path("monitor_data.json", cfg=cfg)
    today = datetime.date.today()
    prev_date = (sys.argv[2] if len(sys.argv) > 2
                 else (today - datetime.timedelta(days=1)).isoformat())

    ratings_path = cfgpath.out_path("ratings_baseline.json", cfg=cfg)
    price_path = cfgpath.out_path("price_baseline.json", cfg=cfg)
    rank_path = cfgpath.out_path("rank_baseline.json", cfg=cfg)
    coupon_dump_path = cfgpath.out_path("coupons.json", cfg=cfg)

    base_ratings = (load_json(ratings_path, {}) or {}).get("ratings", {})
    base_prices = (load_json(price_path, {}) or {}).get("prices", {})
    base_ranks = (load_json(rank_path, {}) or {}).get("ranks", {})
    old = load_json(data_path, {}) or {}
    old_map = {r.get("asin"): r for r in old.get("rows", [])}

    url, headers = load_server_config(cfg.get("sellersprite", {}).get("mcpServer", "sellersprite-universal"), cfg)
    client = McpClient(url, headers)
    client.initialize()

    new_rows, coupon_dump, today_ratings, today_prices, today_ranks = [], {}, {}, {}, {}
    warnings = []

    for asin, name in rows_cfg:
        d = client.call_tool("getAsinDetail", {
            "asin": asin, "marketplace": market,
            "specifyResponseFields": [
                "data.asin", "data.title", "data.price", "data.primePrice", "data.coupon",
                "data.fulfillment", "data.sellerId", "data.sellerName",
                "data.ratings", "data.reviews", "data.rating", "data.updatedTime",
                "data.bsrRank", "data.bsrLabel", "data.subcategories",
            ],
        })
        d = (d.get("data", {}) or {}).get("data") or {}

        if not d or not d.get("asin"):
            warnings.append("%s 今日接口无数据（按不可售处理）" % asin)
            row = {"asin": asin, "title": name, "status": "不可售", "price": "$-",
                   "prev_price": "$-", "rating": "-", "prev_rating": 0, "rating_delta": 0,
                   "buybox": "异常", "coupon": "", "prev_coupon": "", "prime_price": None,
                   "stars": "", "gross_rank": None, "gross_label": "",
                   "gross_rank_delta": None, "gross_rank_alert": False,
                   "sub_rank": None, "sub_label": "", "sub_rank_delta": None,
                   "sub_rank_alert": False}
            for k in ("fba_qty", "fba_days", "fba_alert"):
                if k in old_map.get(asin, {}):
                    row[k] = old_map[asin][k]
            new_rows.append(row)
            continue

        price = get_price(d) or "$-"
        coupon = norm_coupon(d.get("coupon"))
        prime_price = norm_prime(d.get("primePrice"))
        seller_id = d.get("sellerId")
        seller_name = d.get("sellerName")
        fulfillment = d.get("fulfillment")
        buybox = "是" if (seller_id in brand_ids and fulfillment == "FBA") else "异常"
        if buybox == "异常":
            # 三种情况要分清，处理动作完全不同：
            #   ① sellerId 为空 → 抓取缺数，不是真异常，别急着改价/改库存
            #   ② 卖家是平台自营（sellerName 含 Amazon / fulfillment=AMZ）→ 真被抢，需运营干预
            #   ③ 卖家是品牌方但 fulfillment 不是 FBA（如 'NA'）→ 配送方式异常/未同步
            seller_l = (seller_name or "").lower()
            if not seller_id:
                why = "卖家字段为空（疑抓取缺数，非真异常）"
            elif seller_l.startswith("amazon") or fulfillment == "AMZ":
                why = "Buy Box 被平台自营抢占（需运营干预）"
            elif seller_id in brand_ids:
                why = "品牌方卖家但配送方式非 FBA（fulfillment=%r）" % fulfillment
            else:
                why = "Buy Box 由非品牌方卖家持有"
            warnings.append("%s Buy Box 异常：%s（sellerId=%r, seller=%r, fulfillment=%r）"
                            % (asin, why, seller_id, seller_name, fulfillment))

        ratings = d.get("ratings")
        stars = d.get("rating")

        hist = (client.call_tool("getAsinDetailHistory", {
            "asin": asin, "marketplace": market,
            "specifyResponseFields": ["data.history"],
        }).get("data", {}) or {}).get("data") or {}
        prev_coupon, found_prev = "", False
        for h in (hist.get("history") or []):
            if h.get("date") == prev_date:
                found_prev = True
                prev_coupon = norm_coupon(h.get("couponPrice"))
                break
        if not found_prev:
            warnings.append("%s 历史接口无 %s 数据（不参与券消失判定）" % (asin, prev_date))

        prev_p = base_prices.get(asin)
        prev_price = ("$%.2f" % float(prev_p)) if isinstance(prev_p, (int, float)) and prev_p > 0 else price

        gross_rank = norm_rank(d.get("bsrRank"))
        gross_label = d.get("bsrLabel") or ""
        sub_rank, sub_label = pick_subcategory(d)
        prev_rank = base_ranks.get(asin) or {}
        gross_delta = sub_delta = None

        if gross_rank is None:
            warnings.append("%s 无大类排名数据" % asin)
        elif norm_rank(prev_rank.get("gross")):
            if prev_rank.get("gross_label") == gross_label:
                gross_delta = gross_rank - prev_rank["gross"]
            else:
                warnings.append("%s 大类类目变更（%s → %s），大类排名不环比"
                                % (asin, prev_rank.get("gross_label"), gross_label))
        if sub_rank is None:
            warnings.append("%s 无小类排名数据" % asin)
        elif norm_rank(prev_rank.get("sub")):
            if prev_rank.get("sub_label") == sub_label:
                sub_delta = sub_rank - prev_rank["sub"]
            else:
                warnings.append("%s 小类类目变更（%s → %s），小类排名不环比"
                                % (asin, prev_rank.get("sub_label"), sub_label))

        prev_r = base_ratings.get(asin, ratings)
        delta = (ratings - prev_r) if isinstance(ratings, int) else 0

        row = {
            "asin": asin, "title": name, "status": "可售",
            "price": price, "prev_price": prev_price,
            "rating": ("%s" % format(ratings, ",")) if isinstance(ratings, int) else str(ratings or "-"),
            "prev_rating": prev_r if isinstance(prev_r, int) else 0,
            "rating_delta": delta,
            "buybox": buybox, "buybox_seller": seller_name or "",
            "coupon": coupon, "prev_coupon": prev_coupon,
            "prime_price": prime_price,
            "stars": str(stars) if stars is not None else "",
            "gross_rank": gross_rank, "gross_label": gross_label,
            "gross_rank_delta": gross_delta,
            "gross_rank_alert": gross_delta is not None and abs(gross_delta) >= th["gross_rank"],
            "sub_rank": sub_rank, "sub_label": sub_label,
            "sub_rank_delta": sub_delta,
            "sub_rank_alert": sub_delta is not None and abs(sub_delta) >= th["sub_rank"],
        }
        for k in ("fba_qty", "fba_days", "fba_alert"):
            if k in old_map.get(asin, {}):
                row[k] = old_map[asin][k]
        new_rows.append(row)

        coupon_dump[asin] = {"today": coupon, "prev": prev_coupon, "prev_date": prev_date,
                             "prev_found": found_prev, "updated_time": d.get("updatedTime")}
        if isinstance(ratings, int):
            today_ratings[asin] = ratings
        today_ranks[asin] = {"gross": gross_rank, "gross_label": gross_label,
                             "sub": sub_rank, "sub_label": sub_label}
        try:
            today_prices[asin] = float(price.lstrip("$"))
        except ValueError:
            pass

    # notes 必须每次由当日数据重算，不能沿用旧文件（旧值会让「价格变动」永久滞留）
    notes = []
    for r in new_rows:
        pp, cp = r.get("prev_price", ""), r.get("price", "")
        if pp and cp and pp != cp and pp != "$-" and cp != "$-":
            notes.append("价格变动 %s %s: %s -> %s" % (r["asin"], r["title"], pp, cp))
        gd = r.get("gross_rank_delta")
        if gd is not None and abs(gd) >= th["gross_rank"]:
            notes.append("大类排名异常 %s %s: %s（%+d）"
                         % (r["asin"], r["title"], format(r.get("gross_rank") or 0, ","), gd))
        sd = r.get("sub_rank_delta")
        if sd is not None and abs(sd) >= th["sub_rank"]:
            notes.append("小类排名异常 %s %s: %s（%+d）"
                         % (r["asin"], r["title"], format(r.get("sub_rank") or 0, ","), sd))

    payload = {
        "snapshot_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M") + " GMT+8",
        "site": market,
        "rows": new_rows,
        "notes": notes,
    }
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    with open(coupon_dump_path, "w", encoding="utf-8") as f:
        json.dump({"date": today.isoformat(), "prev_date": prev_date, "coupons": coupon_dump},
                  f, ensure_ascii=False, indent=2)

    with open(ratings_path, "w", encoding="utf-8") as f:
        json.dump({"snapshot_date": today.isoformat(), "ratings": today_ratings},
                  f, ensure_ascii=False, indent=2)

    with open(price_path, "w", encoding="utf-8") as f:
        json.dump({"snapshot_date": today.isoformat(), "snapshot_time": payload["snapshot_time"],
                   "field": "getAsinDetail.price（到手价）", "prices": today_prices},
                  f, ensure_ascii=False, indent=2)

    with open(rank_path, "w", encoding="utf-8") as f:
        json.dump({"snapshot_date": today.isoformat(), "snapshot_time": payload["snapshot_time"],
                   "field": "getAsinDetail.bsrRank / subcategories[0].rank",
                   "thresholds": {"gross": th["gross_rank"], "sub": th["sub_rank"]},
                   "ranks": today_ranks}, f, ensure_ascii=False, indent=2)

    print("取数完成：%d 行，昨日=%s，快照=%s" % (len(new_rows), prev_date, payload["snapshot_time"]))
    print("在券 ASIN：")
    hit = [(a, v["today"], v["prev"]) for a, v in coupon_dump.items() if v["today"]]
    for a, t, p in hit:
        print("  %s  今日=%s  昨日=%s" % (a, t, p or "无"))
    if not hit:
        print("  （无；注意该字段会成批漏抓，交由 apply_erp_coupons.py 核对 ERP）")
    missing = [a for a, v in coupon_dump.items() if v["today"] and not v["prev_found"]]
    if missing:
        print("注意：以下 ASIN 无昨日历史，未参与券消失判定：", ", ".join(missing))

    print("Prime 专享折扣 ASIN（接口原值；成批漏抓时交由 fetch_prime.py 前台复核）：")
    prime_hit = [(r["asin"], r["title"], r["prime_price"]) for r in new_rows if r.get("prime_price")]
    for a, t, p in prime_hit:
        print("  %s %s  Prime$%.2f" % (a, t, float(p)))
    if not prime_hit:
        print("  （无）")

    print("排名异常（大类阈值 ±%d，小类阈值 ±%d）：" % (th["gross_rank"], th["sub_rank"]))
    rank_hits = 0
    for r in new_rows:
        gd, sd = r.get("gross_rank_delta"), r.get("sub_rank_delta")
        g_hit = gd is not None and abs(gd) >= th["gross_rank"]
        s_hit = sd is not None and abs(sd) >= th["sub_rank"]
        if not (g_hit or s_hit):
            continue
        rank_hits += 1
        parts = []
        if g_hit:
            parts.append("大类 %s（%+d）" % (format(r["gross_rank"], ","), gd))
        if s_hit:
            parts.append("小类 %s（%+d）" % (format(r["sub_rank"], ","), sd))
        print("  %s %s: %s" % (r["asin"], r["title"], "；".join(parts)))
    if not rank_hits:
        print("  （无）")

    if warnings:
        print("告警：")
        for w in warnings:
            print("  !", w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
