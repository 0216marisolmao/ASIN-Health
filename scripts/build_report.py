#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_report.py — 把 monitor_data.json 渲染成自包含的 HTML 报告。

结构（顺序固定：数据在前、总结在后）：
  一、监控数据   12 列完整表格（与 PNG 日报同口径）
  二、日报总结   KPI 卡 + 库存预警明细 + 在券清单 + Prime 专享 + 排名异常 + 评论增长
  三、口径与判定规则   逐列的数据来源与判定规则

模板内置在技能包里（templates/report_template.html），本脚本只负责把当日数字算出来
填进占位符——所以换品牌 / 换站点 / 换阈值都不用改 HTML，只改 config.json。

用法：
  python build_report.py [monitor_data.json] [输出HTML路径]
默认输出：<outputs.dir>/report_<日期>.html（文件名模板见 config.outputs.report）

输出是单文件 HTML（CSS 内联、无外部依赖），浏览器直接打开，Ctrl+P 可导出 PDF。
"""
import datetime
import html
import json
import os
import re
import sys

import cfgpath

CURRENCY_FALLBACK = "USD"


def esc(v):
    return html.escape(str(v if v is not None else ""), quote=False)


def fmt_coupon(raw):
    """券值展示："2.00"→"$2"；"5%"→"5%"；空→""。"""
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


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def label_summary(rows, key):
    """把若干类目名汇总成一句可读文本，如 "Arts, Crafts & Sewing"。"""
    seen = []
    for r in rows:
        v = (r.get(key) or "").strip()
        if v and v not in seen:
            seen.append(v)
    if not seen:
        return "—"
    if len(seen) <= 2:
        return " / ".join(seen)
    return "%s 等 %d 个类目" % (seen[0], len(seen))


def data_row_html(r):
    """12 列数据表 <tr>。"""
    asin = esc(r.get("asin"))
    title = esc(r.get("title"))

    status = r.get("status", "可售")
    status_html = ('<span class="badge bg-green">可售</span>' if status == "可售"
                   else '<span class="badge bg-red">%s</span>' % esc(status))

    if r.get("buybox", "是") == "是":
        bb_html = '<span class="badge bg-green">是</span>'
    else:
        seller = (r.get("buybox_seller") or "").strip()
        bb_html = '<span class="badge bg-red">异常</span>'
        if seller:
            bb_html += ' <span class="muted">%s</span>' % esc(seller)

    price = esc(r.get("price", ""))
    if r.get("prev_price") and r.get("prev_price") != r.get("price"):
        price_html = '<span class="red">%s</span> <span class="badge bg-red">异常</span>' % price
    else:
        price_html = price

    c = fmt_coupon(r.get("coupon", ""))
    pc = fmt_coupon(r.get("prev_coupon", ""))
    if c:
        coupon_html = '<span class="badge bg-purple">%s coupon</span>' % esc(c)
    elif pc:
        coupon_html = '<span class="badge bg-red">coupon消失</span>'
    else:
        coupon_html = '<span class="muted">无</span>'

    prime = r.get("prime_price")
    if prime is not None and prime != "":
        try:
            prime_html = '<span class="prime">Prime$%.2f</span>' % float(prime)
        except (TypeError, ValueError):
            prime_html = ""
    else:
        prime_html = ""

    rating = esc(r.get("rating", ""))
    d = r.get("rating_delta")
    rating_html = rating
    if isinstance(d, int) and d != 0:
        cls = "bg-green" if d > 0 else "bg-red"
        rating_html += ' <span class="badge %s">%+d</span>' % (cls, d)

    stars = r.get("stars") or ""
    stars_html = '<span class="stars">★ %s</span>' % esc(stars) if stars else ""
    if not stars:
        stars_html = '<span class="muted">—</span>'

    def rank_cell(v, hit):
        if not isinstance(v, int):
            return '<td class="num muted">—</td>'
        if hit:
            return '<td class="num"><span class="red">%s</span> <span class="badge bg-red">异常</span></td>' % format(v, ",")
        return '<td class="num">%s</td>' % format(v, ",")

    alert, days = r.get("fba_alert", ""), r.get("fba_days")
    if alert == "异常":
        fba_html = '<span class="badge bg-red">异常 · %s天</span>' % days
    elif alert == "正常":
        fba_html = '<span class="badge bg-green">正常 · %s天</span>' % days
    else:
        fba_html = '<span class="muted">—</span>'

    return ("        <tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td>%s</td><td>%s</td><td>%s</td>%s%s<td>%s</td></tr>"
            % (asin, title, status_html, bb_html, price_html, coupon_html, prime_html,
               rating_html, stars_html,
               rank_cell(r.get("gross_rank"), bool(r.get("gross_rank_alert"))),
               rank_cell(r.get("sub_rank"), bool(r.get("sub_rank_alert"))),
               fba_html))


def kpi(n, label, cls=""):
    return ('    <div class="kpi"><div class="n%s">%s</div><div class="l">%s</div></div>'
            % ((" " + cls) if cls else "", n, label))


def build_cards(rows, th):
    total = len(rows)
    sellable = [r for r in rows if r.get("status") == "可售"]
    bb_ok = [r for r in rows if r.get("buybox") == "是"]
    price_bad = [r for r in rows if r.get("prev_price") and r.get("prev_price") != r.get("price")
                 and r.get("price") != "$-"]
    coupon_on = [r for r in rows if (r.get("coupon") or "").strip()]
    coupon_gone = [r for r in rows if not (r.get("coupon") or "").strip() and (r.get("prev_coupon") or "").strip()]
    prime_on = [r for r in rows if r.get("prime_price")]
    fba_bad = [r for r in rows if r.get("fba_alert") == "异常"]
    rank_bad = [r for r in rows if r.get("gross_rank_alert") or r.get("sub_rank_alert")]

    cards = [
        kpi("%d / %d" % (len(sellable), total),
            "可售；Buy Box 品牌方持有 %d 个" % len(bb_ok),
            "n-green" if len(bb_ok) == total else "n-red"),
        kpi(str(len(price_bad)), "价格异常（与上一交易日基线不一致）",
            "n-red" if price_bad else ""),
        kpi(str(len(coupon_on)), "在券 ASIN" + ("；券消失 %d 个" % len(coupon_gone) if coupon_gone else "，无券消失"),
            "n-purple"),
        kpi(str(len(prime_on)), "Prime 专享折扣", "n-purple" if prime_on else ""),
        kpi(str(len(rank_bad)), "排名异常（大类 ±%d / 小类 ±%d）" % (th["gross_rank"], th["sub_rank"]),
            "n-red" if rank_bad else ""),
        kpi(str(len(fba_bad)), "库存预警（可售天数 < %d 天）" % th["fba_days"],
            "n-red" if fba_bad else ""),
    ]
    return "\n".join(cards), {"price_bad": price_bad, "coupon_on": coupon_on,
                              "coupon_gone": coupon_gone, "prime_on": prime_on,
                              "rank_bad": rank_bad, "fba_bad": fba_bad, "total": total}


def build_blocks(rows, info, erp_coupons, stock, notes, rank_baseline_date, today, th):
    blocks = []

    # 库存预警明细
    if info["fba_bad"]:
        items = sorted(info["fba_bad"], key=lambda r: (r.get("fba_days") is None, r.get("fba_days") or 0))
        trs = []
        for r in items:
            avg30 = (stock.get(r["asin"], {}) or {}).get("avg30")
            trs.append('<tr><td>%s</td><td>%s</td><td class="num">%s</td><td class="num">%s</td>'
                       '<td class="num red">%s</td><td><span class="badge bg-red">异常</span></td></tr>'
                       % (esc(r["asin"]), esc(r.get("title")), r.get("fba_qty"),
                          avg30 if avg30 is not None else "—", r.get("fba_days")))
        urgent = [r for r in items if isinstance(r.get("fba_days"), int) and r["fba_days"] <= 10]
        extra = ""
        if urgent:
            extra = ('<p class="note">其中 %s 已进入补货决策窗口，建议优先处理。</p>'
                     % "、".join("<strong>%s（%s 天）</strong>" % (esc(r.get("title")), r.get("fba_days"))
                                 for r in urgent))
        blocks.append("""  <div class="card">
    <p style="margin:0 0 10px"><strong class="red">库存预警（%d 条，按紧急程度排序）</strong>——分子为 ERP 平台仓可用量，分母为 30 天日均销量：</p>
    <div class="tbl-wrap">
      <table>
        <thead><tr><th>ASIN</th><th>产品</th><th class="num">可用量</th><th class="num">30 天日均</th><th class="num">可售天数</th><th>状态</th></tr></thead>
        <tbody>
%s
        </tbody>
      </table>
    </div>
    %s
  </div>""" % (len(items), "\n".join("          " + t for t in trs), extra))
    else:
        blocks.append('  <div class="card"><p style="margin:0"><span class="badge bg-green">库存正常</span> '
                      '本轮无 ASIN 可售天数低于 %d 天。</p></div>' % th["fba_days"])

    # 在券
    if info["coupon_on"]:
        lis = []
        for r in info["coupon_on"]:
            end = ((erp_coupons.get(r["asin"], {}) or {}).get("end_date") or "")
            suffix = "，有效期至 %s" % end if end else ""
            lis.append("      <li><strong>%s</strong>（%s）— %s%s</li>"
                       % (esc(r.get("title")), esc(r["asin"]), esc(fmt_coupon(r.get("coupon"))), suffix))
        tail = ('<p class="note">本轮无「coupon 消失」告警。</p>' if not info["coupon_gone"]
                else '<p class="note red">本轮出现 %d 个「coupon 消失」：%s</p>'
                     % (len(info["coupon_gone"]),
                        "、".join(esc(r.get("title")) for r in info["coupon_gone"])))
        blocks.append("""  <div class="card">
    <p style="margin:0 0 10px"><strong class="n-purple">在券 %d 个</strong>（Coupon 列显示为紫色徽章）：</p>
    <ul>
%s
    </ul>
    %s
  </div>""" % (len(info["coupon_on"]), "\n".join(lis), tail))

    # Prime 专享
    if info["prime_on"]:
        lis = "".join("      <li><strong>%s</strong>（%s）— Prime$%.2f</li>"
                      % (esc(r.get("title")), esc(r["asin"]), float(r["prime_price"]))
                      for r in info["prime_on"])
        blocks.append("""  <div class="card">
    <p style="margin:0 0 10px"><strong class="prime">Prime 专享 %d 个</strong>（前台实时确认）：</p>
    <ul>
%s
    </ul>
  </div>""" % (len(info["prime_on"]), lis))

    # 排名异常
    if info["rank_bad"]:
        lis = []
        for r in info["rank_bad"]:
            parts = []
            if r.get("gross_rank_alert"):
                parts.append("大类 %s（%+d）" % (format(r["gross_rank"], ","), r["gross_rank_delta"]))
            if r.get("sub_rank_alert"):
                parts.append("小类 %s（%+d）" % (format(r["sub_rank"], ","), r["sub_rank_delta"]))
            lis.append("      <li><strong>%s</strong>（%s）— %s</li>"
                       % (esc(r.get("title")), esc(r["asin"]), "；".join(parts)))
        blocks.append("""  <div class="card">
    <p style="margin:0 0 10px"><strong class="red">排名异常 %d 个</strong>：</p>
    <ul>
%s
    </ul>
  </div>""" % (len(info["rank_bad"]), "\n".join(lis)))

    # 评论增长
    grew = sorted([r for r in rows if isinstance(r.get("rating_delta"), int) and r["rating_delta"] > 0],
                  key=lambda r: -r["rating_delta"])
    if grew:
        lis = "".join("      <li><strong>%s</strong>（%s）— +%d，累计 %s</li>"
                      % (esc(r.get("title")), esc(r["asin"]), r["rating_delta"], esc(r.get("rating")))
                      for r in grew)
        blocks.append("""  <div class="card">
    <p style="margin:0 0 10px"><strong class="n-blue">评论增长 %d 个</strong>：</p>
    <ul>
%s
    </ul>
  </div>""" % (len(grew), lis))

    # 基线说明 + 当日 notes
    foot = []
    if rank_baseline_date and rank_baseline_date == today:
        foot.append("排名维度：今日建立/刷新了环比基线，本轮排名列只显示绝对值；"
                    "自下一交易日起输出排名波动预警（大类 ≥%d、小类 ≥%d 判异常）。"
                    % (th["gross_rank"], th["sub_rank"]))
    if notes:
        foot.append("当日异动记录：" + "；".join(esc(n) for n in notes))
    if foot:
        blocks.append('  <div class="warn"><strong>说明：</strong>' + "<br>".join(foot) + "</div>")

    return "\n".join(blocks)


def main():
    cfg = cfgpath.load_config()
    th = cfgpath.thresholds(cfg)
    site = cfg.get("site") or {}
    currency = site.get("currency") or CURRENCY_FALLBACK
    brand = cfg.get("brand") or ""
    site_label = site.get("label") or site.get("marketplace") or ""
    prime_cfg = cfg.get("prime") or {}
    domain = (site.get("domain") or "www.amazon.com")

    data_path = sys.argv[1] if len(sys.argv) > 1 else cfgpath.out_path("monitor_data.json", cfg=cfg)
    today = datetime.date.today().isoformat()
    default_name = ((cfg.get("outputs") or {}).get("report") or "report_{date}.html").replace("{date}", today)
    out_path = sys.argv[2] if len(sys.argv) > 2 else cfgpath.out_path(default_name, cfg=cfg)

    payload = load_json(data_path, None)
    if not payload:
        print("✗ 读不到 %s，先跑 fetch_sellersprite.py" % data_path)
        return 1
    rows = payload.get("rows", [])
    snapshot = payload.get("snapshot_time", "")

    erp_dump = load_json(cfgpath.out_path("coupons_erp.json", cfg=cfg), {}) or {}
    erp_coupons = erp_dump.get("erp_running_coupons", {}) or {}
    stock = (load_json(cfgpath.out_path("erp_stock.json", cfg=cfg), {}) or {}).get("stock", {}) or {}
    rank_baseline = load_json(cfgpath.out_path("rank_baseline.json", cfg=cfg), {}) or {}

    cards, info = build_cards(rows, th)
    blocks = build_blocks(rows, info, erp_coupons, stock, payload.get("notes") or [],
                          rank_baseline.get("snapshot_date", ""), today, th)

    tpl_path = cfgpath.template_path("report_template.html")
    if not os.path.exists(tpl_path):
        print("✗ 找不到报告模板 %s" % tpl_path)
        return 1
    with open(tpl_path, "r", encoding="utf-8") as f:
        tpl = f.read()

    gross_label = label_summary(rows, "gross_label")
    sub_label = label_summary(rows, "sub_label")
    prime_source = ("后台抓取（BrowserSkill 前台实时）" if prime_cfg.get("enabled", True)
                    else "卖家精灵 primePrice")

    header_bits = []
    if snapshot:
        header_bits.append("数据快照：%s" % snapshot)
    header_bits.append("监控范围：%s %d 个 ASIN" % (brand or site_label, len(rows)))
    header_bits.append("数据源：卖家精灵 + 前台实时（Prime 专享）+ 积加 ERP（库存 / 券兜底）")
    if gross_label != "—":
        header_bits.append("类目：%s" % gross_label)
    header_meta = "&nbsp;·&nbsp; ".join(esc(b) for b in header_bits)

    footer_bits = ["报告生成时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                   "数据快照：%s" % (snapshot or today),
                   ("监控范围：%s %s %d 个 ASIN" % (brand, site_label, len(rows))).strip()]
    if cfg.get("footerNote"):
        footer_bits.append(str(cfg["footerNote"]))

    mapping = {
        "DOC_TITLE": "%s · 监控数据与总结（%s）" % ((brand + " " + site_label).strip() or "ASIN 监控", today),
        "H1": "%s · 监控数据与总结" % ((brand + " " + site_label).strip() or "ASIN 监控"),
        "HEADER_META": header_meta,
        "SNAPSHOT": esc(snapshot or today),
        "CURRENCY": esc(currency),
        "DATA_TABLE_ROWS": "\n".join(data_row_html(r) for r in rows),
        "DATA_NOTE": "Rating 列绿色徽章为与上一交易日的环比增量；未标注即为持平。"
                     "「—」表示该项本轮无数据。",
        "SUMMARY_CARDS": cards,
        "SUMMARY_BLOCKS": blocks,
        "ASIN_COUNT": str(len(rows)),
        "BRAND_SELLER_IDS": ", ".join(sorted(cfgpath.seller_ids(cfg))) or "（未配置 brandSellerIds）",
        "PRIME_SOURCE": prime_source + ("（%s）" % domain if prime_cfg.get("enabled", True) else ""),
        "GROSS_LABEL": esc(gross_label),
        "SUB_LABEL": esc(sub_label),
        "THRESHOLD_GROSS": str(th["gross_rank"]),
        "THRESHOLD_SUB": str(th["sub_rank"]),
        "THRESHOLD_FBA": str(th["fba_days"]),
        "WAREHOUSE": esc((cfg.get("erp") or {}).get("warehouseName") or "—"),
        "FOOTER_META": "&nbsp;·&nbsp; ".join(esc(b) for b in footer_bits),
    }

    out = tpl
    for k, v in mapping.items():
        out = out.replace("{{%s}}" % k, v)
    left = re.findall(r"\{\{([A-Z_]+)\}\}", out)
    if left:
        print("⚠ 模板里有未替换的占位符：%s" % ", ".join(sorted(set(left))))

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(out)

    print("报告已生成：%s" % out_path)
    print("  %d 行 · 快照 %s · KPI 卡 %d 张" % (len(rows), snapshot or today, 6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
