#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""apply_fba_stock.py — 把 ERP 平台仓可用量注入 monitor_data.json 并算库存预警。

用法：
  python apply_fba_stock.py [erp_stock.json] [monitor_data.json]
默认两份文件都在 <outputs.dir>/ 下（见 config.json）。

口径（分子/分母不能换）：
  分子 = avail_qty —— ERP「平台仓库存」页面的**可用量**（availableQty）
  分母 = avg30      —— 30 天日均销量（avgUnitsOrdered30Days）

计算规则（阈值 = config.thresholds.fbaDays，默认 20 天）：
  fba_days = round(avail_qty / avg30)
  < 阈值  → fba_alert = "异常"（日报红字「异常 · N天」）
  >= 阈值 → fba_alert = "正常"（日报绿字「正常 · N天」）
  无销量数据（avg30<=0）→ 不会断货，fba_alert = "正常"
  avail_qty 缺失        → fba_alert = ""（日报显示 —）

⚠ 不要用商品列表里的「可履约数量 / fbaQuantity」当分子——那是亚马逊可履约数，
  口径更小，会系统性低估可售天数、造成误报。本技能只认平台仓可用量。
"""
import json
import sys

import cfgpath


def compute(avail_qty, avg30, threshold):
    """返回 (fba_days|None, alert)。"""
    if avail_qty is None:
        return None, ""
    if avg30 is None or avg30 <= 0:
        return None, "正常"
    days = int(round(avail_qty / avg30))
    return days, ("异常" if days < threshold else "正常")


def main():
    cfg = cfgpath.load_config()
    threshold = cfgpath.thresholds(cfg)["fba_days"]

    erp_path = sys.argv[1] if len(sys.argv) > 1 else cfgpath.out_path("erp_stock.json", cfg=cfg)
    data_path = sys.argv[2] if len(sys.argv) > 2 else cfgpath.out_path("monitor_data.json", cfg=cfg)

    with open(erp_path, "r", encoding="utf-8") as f:
        erp = json.load(f)
    stock = erp.get("stock", {})
    erp_date = erp.get("date", "")

    with open(data_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    alerts = []
    for row in payload.get("rows", []):
        asin = row["asin"]
        info = stock.get(asin)
        if not info:
            row["fba_qty"] = None
            row["fba_days"] = None
            row["fba_alert"] = ""
            alerts.append((asin, row.get("title", ""), "无数据"))
            continue
        qty = info.get("avail_qty")
        avg30 = info.get("avg30")
        days, alert = compute(qty, avg30, threshold)
        row["fba_qty"] = qty
        row["fba_days"] = days
        row["fba_alert"] = alert
        if alert == "异常":
            alerts.append((asin, row.get("title", ""), "可用量%s / 日均%s → %s天" % (qty, avg30, days)))

    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    total = len(payload.get("rows", []))
    bad = [a for a in alerts if a[2] != "无数据"]
    print("ERP 库存日期: %s  已注入 %d 行（口径：平台仓可用量 / 30天日均，阈值 %d 天）"
          % (erp_date, total, threshold))
    print("库存预警异常 %d 条：" % len(bad))
    for asin, title, detail in alerts:
        flag = "!!" if detail != "无数据" else "--"
        print("  %s %s  %s  %s" % (flag, asin, title, detail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
