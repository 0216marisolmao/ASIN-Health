#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_pipeline.py — 一键跑完整条日报流水线。

步骤与依赖关系（顺序不能乱）：
  1. fetch_sellersprite.py   取数（价格/券/评分/排名/BuyBox）→ 滚动三份基线。失败=致命。
  2. fetch_prime.py          Prime 专享：前台实时，覆盖接口值（接口该字段会成批漏抓）。
                             失败不阻塞（回落接口值 + 显式告警）。
  3. fetch_fba_inventory.py  ERP 平台仓可用量 → erp_stock.json。失败不阻塞（库存列显示「—」）。
  4. apply_erp_coupons.py    ERP Running 券兜底修正 coupon 列。
  5. apply_fba_stock.py      注入库存 + 算可售天数预警。
  6. generate_monitor_image.py  出 PNG 日报。
  7. build_report.py         出 HTML 报告（+ render_report_png.py 出报告长图，可关）。

用法：
  python run_pipeline.py                 # 全跑
  python run_pipeline.py --skip-prime    # 不抓前台 Prime（无浏览器时）
  python run_pipeline.py --no-report     # 只出日报图，不出 HTML 报告
  python run_pipeline.py --with-report-png   # 额外把报告渲染成长图（需 bsk）

设计原则：任何一步失败都要说清楚「哪一步、什么错、后续受影响的是哪一列」，
绝不静默吞掉——列错值比缺值危险。
"""
import os
import subprocess
import sys
import time

import cfgpath

HERE = cfgpath.script_dir()
PY = sys.executable


def step(name, script, args=None, fatal=False):
    t0 = time.time()
    cmd = [PY, os.path.join(HERE, script)] + (args or [])
    print("\n" + "=" * 68)
    print("▶ %s  (%s)" % (name, script))
    print("=" * 68)
    p = subprocess.run(cmd, cwd=HERE)
    cost = round(time.time() - t0, 1)
    if p.returncode != 0:
        print("✗ %s 失败（退出码 %d，耗时 %ss）" % (name, p.returncode, cost))
        if fatal:
            raise SystemExit("%s 是必需步骤，流水线终止。" % name)
        print("  → 该步失败不阻塞后续；对应列会缺失或退回兜底值，请在报告中说明。")
    else:
        print("✓ %s 完成（%ss）" % (name, cost))
    return p.returncode == 0


def main():
    argv = sys.argv[1:]
    skip_prime = "--skip-prime" in argv
    no_report = "--no-report" in argv
    with_report_png = "--with-report-png" in argv

    cfg = cfgpath.load_config()
    print("工作目录：%s" % cfgpath.out_path("", cfg=cfg).rstrip("\\/"))
    print("监控清单：%d 个 ASIN" % len(cfgpath.asin_list(cfg)))

    step("① 取数（卖家精灵）", "fetch_sellersprite.py", fatal=True)
    if skip_prime:
        print("\n（按 --skip-prime，跳过前台 Prime 抓取）")
    else:
        step("② Prime 专享（前台实时）", "fetch_prime.py")
    step("③ ERP 平台仓库存", "fetch_fba_inventory.py")
    step("④ ERP 券兜底", "apply_erp_coupons.py")
    step("⑤ 库存预警计算", "apply_fba_stock.py")
    step("⑥ 生成日报 PNG", "generate_monitor_image.py")

    if not no_report:
        if step("⑦ 生成 HTML 报告", "build_report.py") and with_report_png:
            step("⑧ 报告渲染长图", "render_report_png.py")

    print("\n" + "=" * 68)
    print("流水线结束。产物目录：%s" % cfgpath.outputs_dir(cfg=cfg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
