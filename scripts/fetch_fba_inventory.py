#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_fba_inventory.py — 从积加 ERP 拉「平台仓库存 → 可用量」，生成 erp_stock.json。

数据源：gerp-inventory MCP 的 post_purchase_store_fbaInventory_page_V2（查询FBA库存列表V2）
        url / 鉴权头自 mcp.json 读取（见 mcpclient.py）。

口径（回答「还能卖多少天」）：
  分子 = availableQty —— ERP「平台仓库存」页面的**可用量**
  分母 = avgUnitsOrdered30Days —— 30 天日均销量
  仓库 = config.erp.warehouseName（同一 ASIN 还会返回其它站点仓的行，必须按仓库名过滤；
        仓库名以 ERP 里显示的名称为准，形如 "<品牌>:<站点>_FBA"）

两个必须避开的坑：
  1) 同一 ASIN 在同一仓库下可能有多条 MSKU（老 SKU 常年 0 库存、state 显示异常）。
     必须取「有库存的那条」——可用量 > 0 中最大者；不能取最后一行，也不能简单求和。
  2) 接口会返回所有站点仓库（US/UK/EU/CA…），不过滤会串站。

用法：
  python fetch_fba_inventory.py [输出JSON路径]
默认输出：<outputs.dir>/erp_stock.json

输出结构：
{
  "date": "2026-01-01",
  "source": "积加ERP ... @ <warehouseName>",
  "stock": {"B0XXXXXXXX": {"avail_qty": 276, "avg30": 19.44, "erp_days": 15.0,
                           "on_hand": 276, "in_transit": 300, "msku": "...", "mskus": [...]}}
}
"""
import datetime
import json
import sys

import cfgpath
from mcpclient import McpClient, load_server_config


def fetch_all(client, asins):
    rows, page = [], 1
    while page <= 10:
        body = client.call_tool("post_purchase_store_fbaInventory_page_V2", {
            "page": page, "pagesize": 100, "asinList": asins,
        })
        data = body.get("data", {})
        inner = data.get("data", data)
        batch = inner.get("rows") or []
        rows.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return rows


def main():
    cfg = cfgpath.load_config()
    asins = cfgpath.asin_keys(cfg)
    if not asins:
        print("✗ config.json 的 asinList 为空，无可查询对象。")
        return 1
    erp_cfg = cfg.get("erp") or {}
    warehouse = (erp_cfg.get("warehouseName") or "").strip()
    if not warehouse:
        print("✗ config.json 缺少 erp.warehouseName（ERP 平台仓名称，形如 <品牌>:US_FBA）。")
        return 1

    out_path = sys.argv[1] if len(sys.argv) > 1 else cfgpath.out_path("erp_stock.json", cfg=cfg)

    url, headers = load_server_config(erp_cfg.get("inventoryServer", "gerp-inventory"), cfg)
    client = McpClient(url, headers)
    client.initialize()
    rows = fetch_all(client, asins)

    grouped = {}
    for r in rows:
        if r.get("warehouseName") != warehouse:
            continue
        asin = r.get("asin")
        if asin:
            grouped.setdefault(asin, []).append(r)

    stock = {}
    for asin, items in grouped.items():
        ranked = sorted(items, key=lambda x: -(x.get("availableQty") or 0))
        with_stock = [it for it in ranked if (it.get("availableQty") or 0) > 0]
        main_row = with_stock[0] if with_stock else ranked[0]
        stock[asin] = {
            "avail_qty": main_row.get("availableQty") or 0,
            "avg30": round(main_row.get("avgUnitsOrdered30Days") or 0, 2),
            "erp_days": main_row.get("availableTurnoverDays"),
            "on_hand": main_row.get("onHandQty") or 0,
            "in_transit": main_row.get("inTransitQty") or 0,
            "msku": main_row.get("msku"),
            "stock_msku_count": len(with_stock),
            "mskus": [{"msku": it.get("msku"), "avail": it.get("availableQty"),
                       "avg30": it.get("avgUnitsOrdered30Days"),
                       "state": it.get("stateName")} for it in ranked],
        }

    payload = {"date": datetime.date.today().isoformat(),
               "source": "积加ERP gerp-inventory post_purchase_store_fbaInventory_page_V2 @ %s" % warehouse,
               "stock": stock}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    missing = [a for a in asins if a not in stock]
    print("拉到 %d 行，其中 %s 命中 %d/%d 个 ASIN" % (len(rows), warehouse, len(stock), len(asins)))
    if missing:
        print("缺数据 ASIN:", ", ".join(missing))
    print("已写入 %s" % out_path)
    for a in asins:
        info = stock.get(a)
        if info:
            print("  %s  可用量=%s  日均30=%s  ERP可用天数=%s  MSKU=%s（该仓库共%d条）"
                  % (a, info["avail_qty"], info["avg30"], info["erp_days"], info["msku"],
                     len(info["mskus"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
