#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""generate_monitor_image.py — 把监控数据渲染成 PNG 日报（企业微信内联显示）。

用法：
  python generate_monitor_image.py [输入JSON] [输出PNG]
默认：<outputs.dir>/monitor_data.json → <outputs.dir>/asin_monitor_daily.png

输入 JSON 结构（由 fetch_sellersprite + apply_erp_coupons + fetch_prime + apply_fba_stock 生成）：
{
  "snapshot_time": "2026-01-01 08:30 GMT+8",
  "rows": [{
      "asin": "...", "title": "产品简称", "status": "可售", "buybox": "是",
      "price": "$41.99", "prev_price": "$41.99", "coupon": "2.00", "prev_coupon": "",
      "prime_price": 29.99, "rating": "1,390", "rating_delta": 1, "stars": "4.6",
      "gross_rank": 4558, "gross_rank_alert": false,
      "sub_rank": 41, "sub_rank_alert": false,
      "fba_qty": 675, "fba_days": 29, "fba_alert": "正常"
  }],
  "notes": []
}

列顺序（12 列）：
  ASIN | 产品标题（简）| 可售状态 | Buy Box | 价格（USD）| Coupon | Prime专享 |
  Rating | Review | 大类排名 | 小类排名 | FBA库存预警

渲染规则：
  Coupon      今日有券 → 紫底 "X coupon"；昨日有券今日无 → 红底 "coupon消失"；否则灰字"无"
  Prime专享   prime_price 有值 → 紫字 "Prime$29.99"；没做折扣 → 整格留空
  大类/小类排名  与上次监控波动超阈值 → 红字 + "异常"（阈值见 config.thresholds）
  FBA库存预警   <阈值天 → 红底"异常 · N天"；>=阈值 → 绿底"正常 · N天"；无数据 → "—"
  价格        与上一交易日基线不一致 → 红字 + "异常"
  Buy Box     非品牌方或不走 FBA → 红字"异常"

图片高度随行数自适应；字体用系统微软雅黑/黑体。
"""
import json
import os
import sys

from PIL import Image, ImageDraw, ImageFont

import cfgpath


def load_fonts():
    for c in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
              "C:/Windows/Fonts/simsun.ttc",
              "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
              "/System/Library/Fonts/PingFang.ttc"):
        if os.path.exists(c):
            return c
    return None


def format_coupon(raw):
    """券值展示文本（不含 ' coupon' 后缀）："2.00"→"$2"；"5%"→"5%"。"""
    if not raw:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    if s.endswith("%"):
        return s
    try:
        f = float(s.lstrip("$"))
        return "$%d" % int(f) if f == int(f) else "$%.2f" % f
    except ValueError:
        return s


def load_data(cfg):
    if len(sys.argv) == 3:
        json_path, output_path = sys.argv[1], sys.argv[2]
    elif len(sys.argv) == 2:
        json_path, output_path = cfgpath.out_path("monitor_data.json", cfg=cfg), sys.argv[1]
    else:
        json_path = cfgpath.out_path("monitor_data.json", cfg=cfg)
        output_path = cfgpath.out_path("asin_monitor_daily.png", cfg=cfg)
    with open(json_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload.get("rows", []), payload.get("snapshot_time", ""), output_path


def make_image(rows, snapshot_time, output_path, cfg):
    font_path = load_fonts()
    if font_path is None:
        raise RuntimeError("未找到中文字体（Windows 需 msyh/simhei，macOS 需 PingFang）")

    site = cfg.get("site") or {}
    title = cfg.get("title") or "ASIN 监控日报"
    site_label = site.get("label") or site.get("marketplace") or ""

    n = len(rows)
    headers = ["ASIN", "产品标题（简）", "可售状态", "Buy Box", "价格（%s）" % (site.get("currency") or "USD"),
               "Coupon", "Prime专享", "Rating", "Review", "大类排名", "小类排名", "FBA库存预警"]
    col_x = [50, 200, 442, 547, 639, 775, 925, 1075, 1188, 1283, 1433, 1588]
    row_h = 70 if n <= 8 else (60 if n <= 14 else 54)

    W, RIGHT, top_block = 1850, 1800, 150
    H = top_block + row_h * (n + 1) + 30

    img = Image.new("RGB", (W, H), "#f6f8fb")
    draw = ImageDraw.Draw(img)

    WHITE = "#ffffff"
    TEXT_DARK, TEXT_MED = "#111827", "#6b7280"
    GREEN_BG, GREEN_TEXT = "#d1fae5", "#065f46"
    RED_TEXT, RED_BG = "#dc2626", "#fee2e2"
    BLUE_TEXT = "#2563eb"
    PURPLE_BG, PURPLE_TEXT = "#ede9fe", "#5b21b6"
    PRIME_TEXT = "#6d28d9"
    GRAY_TEXT, BORDER, HEADER_BG = "#9ca3af", "#eef0f4", "#1f2937"

    scale = 1.0 if n <= 8 else (0.92 if n <= 14 else 0.86)
    f_title = ImageFont.truetype(font_path, int(34 * scale))
    f_sub = ImageFont.truetype(font_path, int(19 * scale))
    f_th = ImageFont.truetype(font_path, int(21 * scale))
    f_td = ImageFont.truetype(font_path, int(23 * scale))
    f_asin = ImageFont.truetype(font_path, int(21 * scale))
    f_badge = ImageFont.truetype(font_path, int(17 * scale))

    draw.text((60, 34), title, fill=TEXT_DARK, font=f_title)
    sub = ("站点：%s" % site_label) if site_label else ""
    if snapshot_time:
        sub = (sub + " · " if sub else "") + "快照时间：%s" % snapshot_time
    draw.text((60, 92), sub, fill=TEXT_MED, font=f_sub)

    table_top = top_block
    draw.rectangle([50, table_top, RIGHT, table_top + row_h], fill=HEADER_BG)
    for i, h in enumerate(headers):
        draw.text((col_x[i] + 16, table_top + (row_h - int(21 * scale)) // 2), h, fill=WHITE, font=f_th)

    badge_h = max(22, int(30 * scale))
    badge_dy = (row_h - badge_h) // 2
    badge_text_dy = (badge_h - int(17 * scale)) // 2

    def text_dy(font):
        bbox = draw.textbbox((0, 0), "汉Ag", font=font)
        return (row_h - (bbox[3] - bbox[1])) // 2 - bbox[1]

    def draw_badge(x, y, label, bg, fg):
        w = int(draw.textlength(label, font=f_badge)) + 28
        draw.rounded_rectangle([x, y, x + w, y + badge_h], radius=badge_h // 2, fill=bg)
        draw.text((x + 14, y + badge_text_dy), label, fill=fg, font=f_badge)

    for idx, row in enumerate(rows):
        y_row = table_top + row_h * (idx + 1)
        draw.rectangle([50, y_row, RIGHT, y_row + row_h], fill=WHITE if idx % 2 == 0 else "#fafbfd")
        draw.line([(50, y_row + row_h), (RIGHT, y_row + row_h)], fill=BORDER, width=1)

        dy_td, dy_asin = text_dy(f_td), text_dy(f_asin)
        by = y_row + badge_dy

        draw.text((col_x[0] + 16, y_row + dy_asin), row.get("asin", ""), fill=TEXT_DARK, font=f_asin)
        draw.text((col_x[1] + 16, y_row + dy_td), row.get("title", ""), fill=TEXT_DARK, font=f_td)

        status = row.get("status", "可售")
        draw_badge(col_x[2] + 16, by, status, GREEN_BG if status == "可售" else RED_BG,
                   GREEN_TEXT if status == "可售" else RED_TEXT)

        if row.get("buybox", "是") == "是":
            draw_badge(col_x[3] + 16, by, "是", GREEN_BG, GREEN_TEXT)
        else:
            draw_badge(col_x[3] + 16, by, "异常", RED_BG, RED_TEXT)

        price, prev_price = row.get("price", ""), row.get("prev_price", "")
        draw.text((col_x[4] + 16, y_row + dy_td), price, fill=RED_TEXT, font=f_td)
        if prev_price and prev_price != price:
            pw = int(draw.textlength(price, font=f_td))
            draw.text((col_x[4] + 16 + pw + 6, y_row + dy_td + 2), "异常", fill=RED_TEXT, font=f_badge)

        coupon_txt = format_coupon(row.get("coupon", ""))
        prev_coupon_txt = format_coupon(row.get("prev_coupon", ""))
        if coupon_txt:
            draw_badge(col_x[5] + 16, by, "%s coupon" % coupon_txt, PURPLE_BG, PURPLE_TEXT)
        elif prev_coupon_txt:
            draw_badge(col_x[5] + 16, by, "coupon消失", RED_BG, RED_TEXT)
        else:
            draw.text((col_x[5] + 16, y_row + dy_td), "无", fill=GRAY_TEXT, font=f_td)

        prime = row.get("prime_price")
        if prime is not None:
            try:
                draw.text((col_x[6] + 16, y_row + dy_td), "Prime$%.2f" % float(prime),
                          fill=PRIME_TEXT, font=f_td)
            except (TypeError, ValueError):
                pass

        rating_text = str(row.get("rating", ""))
        draw.text((col_x[7] + 16, y_row + dy_td), rating_text, fill=BLUE_TEXT, font=f_td)
        delta = row.get("rating_delta")
        if delta is not None and delta != 0:
            rw = int(draw.textlength(rating_text, font=f_td))
            draw.text((col_x[7] + 16 + rw + 4, y_row + dy_td + 2),
                      ("+" if delta > 0 else "") + str(delta),
                      fill=GREEN_TEXT if delta > 0 else RED_TEXT, font=f_badge)

        stars = row.get("stars", "") or ""
        if stars:
            draw.text((col_x[8] + 16, y_row + dy_td), "★ %s" % stars, fill="#f59e0b", font=f_td)

        for ci, val_key, alert_key in ((9, "gross_rank", "gross_rank_alert"),
                                       (10, "sub_rank", "sub_rank_alert")):
            rv = row.get(val_key)
            if not isinstance(rv, int):
                draw.text((col_x[ci] + 16, y_row + dy_td), "—", fill=GRAY_TEXT, font=f_td)
                continue
            rtxt = format(rv, ",")
            hit = bool(row.get(alert_key))
            draw.text((col_x[ci] + 16, y_row + dy_td), rtxt,
                      fill=RED_TEXT if hit else TEXT_DARK, font=f_td)
            if hit:
                rw = int(draw.textlength(rtxt, font=f_td))
                draw.text((col_x[ci] + 16 + rw + 4, y_row + dy_td + 2), "异常",
                          fill=RED_TEXT, font=f_badge)

        alert, days = row.get("fba_alert", ""), row.get("fba_days")
        if alert == "异常":
            draw_badge(col_x[11] + 16, by,
                       ("异常 · %s天" % days) if days is not None else "异常", RED_BG, RED_TEXT)
        elif alert == "正常":
            draw_badge(col_x[11] + 16, by,
                       ("正常 · %s天" % days) if days is not None else "正常", GREEN_BG, GREEN_TEXT)
        else:
            draw.text((col_x[11] + 16, y_row + dy_td), "—", fill=GRAY_TEXT, font=f_td)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    img.save(output_path, "PNG")
    print("Saved: %s  (%dx%d, %d rows)" % (output_path, W, H, n))


if __name__ == "__main__":
    _cfg = cfgpath.load_config()
    _rows, _snap, _out = load_data(_cfg)
    if not _rows:
        print("✗ 无数据行，先跑 fetch_sellersprite.py")
        sys.exit(1)
    make_image(_rows, _snap, _out, _cfg)
