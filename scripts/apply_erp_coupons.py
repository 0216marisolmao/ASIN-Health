#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""apply_erp_coupons.py — 用积加 ERP「优惠券」报表给 coupon 列兜底。

为什么需要：
  SellerSprite 的 coupon 字段会**成批返回空值**（实测曾连续两天 18/18 全空），
  而 ERP 优惠券报表显示这些券 status=Running、预算未耗尽、仍在投放。
  若照接口的空值渲染，日报会出现「coupon消失」红色误报（每天数条）。

口径：
  - 只在接口 coupon 为空时才用 ERP 兜底（ERP 不覆盖接口已返回的真实券值）。
  - ERP discountType=percent → "5%"；money → "2.00"，与 norm_coupon 展示格式一致。
  - 只认 status == "Running" 且 endDate 未过期的券；同 ASIN 多张券取 endDate 最晚的。

数据源：gerp-ads MCP post_operation_ads_coupon_query，marketId 见 config.erp.marketId。

用法：
  python apply_erp_coupons.py [monitor_data.json] [审计JSON路径]
"""
import datetime
import json
import sys

import cfgpath
from mcpclient import McpClient, load_server_config

LOOKBACK_DAYS = 90


def parse_dt(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(str(s), fmt)
        except ValueError:
            continue
    return None


def coupon_display(c):
    """ERP 券 → 与 norm_coupon 一致的展示串。

    注意：discountAmount.currencyAmount 对百分比券不可信（5% 券也显示 $5.00），
    判券值只能用 discountType + discount。
    """
    dtype = (c.get("discountType") or "").lower()
    d = c.get("discount")
    if dtype == "percent" and d is not None:
        f = float(d)
        return ("%d%%" % int(f)) if f == int(f) else ("%s%%" % f)
    if dtype == "money" and d is not None:
        return "%.2f" % float(d)
    amt = (c.get("discountAmount") or {}).get("currencyAmount")
    if amt:
        return "%.2f" % float(amt)
    return ""


def fetch_running_coupons(client, market_id):
    """返回 {asin: {display, end_date, desc, coupon_id, discount_type, discount, msku}}。

    接口返回是**券（campaign）维度**：一张券下挂 adsOpenCouponGoodsVOList[]，
    每个元素含 asin/msku，所以需要自己反转成「ASIN → 券」。
    """
    today = datetime.date.today()
    body = client.call_tool("post_operation_ads_coupon_query", {
        "marketId": market_id, "count": 100,
        "startDataDate": (today - datetime.timedelta(days=LOOKBACK_DAYS)).isoformat(),
        "endDataDate": today.isoformat(),
    })
    data = body.get("data", {})
    coupons = data.get("data", data) or []
    if isinstance(coupons, dict):
        coupons = coupons.get("data") or []

    best = {}
    for c in coupons:
        if str(c.get("status") or "").lower() != "running":
            continue
        end = parse_dt(c.get("endDate"))
        if end and end.date() < today:
            continue
        disp = coupon_display(c)
        if not disp:
            continue
        for g in (c.get("adsOpenCouponGoodsVOList") or []):
            asin = g.get("asin")
            if not asin:
                continue
            cur = best.get(asin)
            if cur is None or (end and cur["_end"] and end > cur["_end"]) or (end and not cur["_end"]):
                best[asin] = {
                    "_end": end, "display": disp,
                    "end_date": end.strftime("%Y-%m-%d") if end else None,
                    "desc": c.get("internalDescription"),
                    "coupon_id": c.get("couponId"),
                    "discount_type": c.get("discountType"),
                    "discount": c.get("discount"),
                    "msku": g.get("msku"),
                }
    for v in best.values():
        v.pop("_end", None)
    return best


def main():
    cfg = cfgpath.load_config()
    erp_cfg = cfg.get("erp") or {}
    market_id = erp_cfg.get("marketId")
    if market_id is None:
        print("✗ config.json 缺少 erp.marketId（积加 ERP 的店铺/站点 ID）。")
        return 1

    data_path = sys.argv[1] if len(sys.argv) > 1 else cfgpath.out_path("monitor_data.json", cfg=cfg)
    audit_path = sys.argv[2] if len(sys.argv) > 2 else cfgpath.out_path("coupons_erp.json", cfg=cfg)

    url, headers = load_server_config(erp_cfg.get("adsServer", "gerp-ads"), cfg)
    client = McpClient(url, headers)
    client.initialize()
    erp = fetch_running_coupons(client, market_id)

    with open(data_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    applied, kept, no_coupon = [], [], []
    for r in payload.get("rows", []):
        asin = r.get("asin")
        ss_val = (r.get("coupon") or "").strip()
        hit = erp.get(asin)
        if ss_val:
            kept.append((asin, ss_val))
            continue
        if hit:
            r["coupon"] = hit["display"]
            r["coupon_source"] = "ERP gerp-ads（接口字段为空，ERP Running 兜底）"
            applied.append((asin, hit["display"], hit["end_date"], hit["desc"]))
        else:
            no_coupon.append(asin)

    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    with open(audit_path, "w", encoding="utf-8") as f:
        json.dump({"date": datetime.date.today().isoformat(), "marketId": market_id,
                   "source": "积加ERP gerp-ads post_operation_ads_coupon_query（status=Running）",
                   "erp_running_coupons": erp,
                   "applied": [{"asin": a, "coupon": c, "end_date": e, "desc": d}
                               for a, c, e, d in applied],
                   "ss_kept": [{"asin": a, "coupon": c} for a, c in kept],
                   "no_coupon": no_coupon}, f, ensure_ascii=False, indent=2)

    print("ERP Running 券覆盖 ASIN %d 个；monitor_data 共 %d 行"
          % (len(erp), len(payload.get("rows", []))))
    print("兜底写入 %d 行：" % len(applied))
    for a, c, e, d in applied:
        print("  %s  coupon=%s  结束=%s  (%s)" % (a, c, e, d))
    if kept:
        print("接口自身已返回券值、未覆盖：", ", ".join("%s=%s" % (a, c) for a, c in kept))
    print("仍无券 %d 行" % len(no_coupon))
    print("已写入 %s 与 %s" % (data_path, audit_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
