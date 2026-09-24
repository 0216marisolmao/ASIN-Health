#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render_report_png.py — 把 HTML 报告整页渲染成 PNG（长图，可直接发企业微信）。

实现方式：起一个本地静态服务把报告喂给浏览器 → BrowserSkill 打开 → 整页截图。
不用无头内核自己造轮子，好处是长图内容与浏览器里看到的**完全一致**（表格、徽章、
配色都不走二次绘制，不会出现两边对不上的情况）。

依赖：
  - bsk CLI（BrowserSkill）与已连接的浏览器扩展
  - Python 内置 http.server

用法：
  python render_report_png.py [报告HTML] [输出PNG]
默认：<outputs.dir>/report_<日期>.html → <outputs.dir>/report_<日期>.png

已知坑（都已在脚本里处理）：
  - 报告名含中文时 URL 编码易出错 → 先复制成 ASCII 临时名再喂给浏览器，截完删掉
  - 视口宽度必须 ≥ 页面 max-width + 边距，否则右侧列被容器裁掉（默认给 1600）
  - 文件用 file:// 打开时扩展可能无权限 → 一律走 http://127.0.0.1
  - Windows 上 bsk daemon 常掉，`session start` 会报 "ensure daemon is running"；
    此时先在后台跑 `bsk daemon start --foreground`（本脚本检测到会给出提示）
"""
import datetime
import functools
import http.server
import os
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time

import cfgpath

ASCII_VIEW = "_report_view.html"


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


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve(directory, port):
    handler = functools.partial(QuietHandler, directory=directory)
    httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


def run(bsk, args, timeout=180):
    p = subprocess.run([bsk] + args, capture_output=True, encoding="utf-8",
                       errors="replace", timeout=timeout, cwd=os.path.dirname(bsk) or None)
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


def main():
    cfg = cfgpath.load_config()
    out_cfg = cfg.get("outputs") or {}
    today = datetime.date.today().isoformat()
    default_html = (out_cfg.get("report") or "report_{date}.html").replace("{date}", today)
    html_path = sys.argv[1] if len(sys.argv) > 1 else cfgpath.out_path(default_html, cfg=cfg)
    png_path = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(html_path)[0] + ".png"
    width = int(out_cfg.get("pngWidth", 1600))

    if not os.path.exists(html_path):
        print("✗ 找不到报告 %s（先跑 build_report.py）" % html_path)
        return 1

    bsk = find_bsk(cfg)
    if not bsk:
        print("✗ 未找到 bsk CLI（BrowserSkill）。装好后重试，或直接用浏览器打开报告后 Ctrl+P 导出 PDF。")
        return 1

    serve_dir = os.path.dirname(os.path.abspath(html_path))
    view_path = os.path.join(serve_dir, ASCII_VIEW)
    shutil.copyfile(html_path, view_path)

    port = free_port()
    httpd = serve(serve_dir, port)
    sid = None
    ok = False
    try:
        rc, out, err = run(bsk, ["session", "start", "--json"], 150)
        m = re.search(r'"session_id"\s*:\s*"([^"]+)"', out or "")
        if not m:
            print("✗ 无法启动 bsk 会话。若提示 daemon 未运行，请先在后台执行：")
            print("    bsk daemon start --foreground")
            print("  （Windows 上 daemon 必须以前台模式挂在后台任务里才能存活）")
            print("  stderr:", (err or out)[:200])
            return 1
        sid = m.group(1)

        run(bsk, ["window", "resize", "--session", sid, "--width", str(width), "--height", "1200"], 60)
        url = "http://127.0.0.1:%d/%s" % (port, ASCII_VIEW)
        rc, out, err = run(bsk, ["navigate", "--session", sid, url], 120)
        if rc != 0:
            print("✗ 打开页面失败：%s" % (err or out)[:200])
            return 1
        time.sleep(2.5)
        rc, out, err = run(bsk, ["screenshot", "--session", sid, "--full-page",
                                 "--scope", "current", "--timeout", "2m",
                                 "--out", png_path], 300)
        print((out or err)[-300:])
        ok = os.path.exists(png_path)
    finally:
        if sid:
            try:
                run(bsk, ["session", "stop", sid], 60)
            except Exception:
                pass
        httpd.shutdown()
        try:
            os.remove(view_path)
        except OSError:
            pass

    if ok:
        print("PNG 已生成：%s（%.0f KB）" % (png_path, os.path.getsize(png_path) / 1024.0))
        return 0
    print("✗ 截图未产出文件，请检查浏览器扩展是否在线（bsk doctor 的 extension connected）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
