# -*- coding: utf-8 -*-
"""pack_skill.py — 打包本技能（白名单收包 + 敏感信息全量扫描）。

流程：
  ① 校验必需文件齐全（SKILL.md / config.example.json / templates / scripts）
  ② 白名单式重打 zip：只收 SKILL.md、config/config.example.json、templates/**、scripts/**；
     data/、config.json、__pycache__、*.pyc、运行产物一律不进包
  ③ 敏感信息扫描（py/js/json/md/html/txt）：MCP token、Bearer、Authorization、
     webhook key、api key/secret 字面值；企业微信会话 ID / 成员 ID；已知泄露串
     含「填/YOUR/占位/示例/<...>」的模板值豁免
  ④ 扫描不过 → 打印明细并退出码 1，**zip 严禁外发**

用法：python pack_skill.py
"""
import io
import os
import re
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
ZIP = os.path.join(HERE, "amazon-asin-daily-monitor-skill.zip")

REQUIRED = [
    "SKILL.md",
    os.path.join("config", "config.example.json"),
    os.path.join("templates", "report_template.html"),
    os.path.join("scripts", "cfgpath.py"),
    os.path.join("scripts", "mcpclient.py"),
    os.path.join("scripts", "fetch_sellersprite.py"),
    os.path.join("scripts", "fetch_prime.py"),
    os.path.join("scripts", "fetch_fba_inventory.py"),
    os.path.join("scripts", "apply_erp_coupons.py"),
    os.path.join("scripts", "apply_fba_stock.py"),
    os.path.join("scripts", "generate_monitor_image.py"),
    os.path.join("scripts", "build_report.py"),
    os.path.join("scripts", "render_report_png.py"),
    os.path.join("scripts", "run_pipeline.py"),
    os.path.join("scripts", "frontend_prime_probe.js"),
]

# 白名单：只有这些进包
INCLUDE_FILES = {"SKILL.md"}
INCLUDE_DIRS = {"scripts", "templates"}
INCLUDE_EXT = {".py", ".js", ".html", ".json", ".md"}

DENY_DIR = {"data", "__pycache__", ".workbuddy", ".git", "outputs"}
DENY_FILE = {"config.json", ".env", "config.local.json"}

SCAN_EXT = {".py", ".js", ".json", ".md", ".html", ".txt", ".yaml", ".yml"}

# 已确认外发过的泄露串（分片拼装，避免本文件自检命中自己）
KNOWN_LEAKS = [s.encode() for s in (
    "wodq" "k7EQAAhQ",
    "mcp_0a" "56c416",
)]

SECRET_PATTERNS = [
    ("mcp_token", re.compile(r"mcp_[A-Za-z0-9]{12,}")),
    ("bearer", re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/=]{16,}")),
    ("authorization", re.compile(r"""[Aa]uthorization["']?\s*[:=]\s*["'][^"']{8,}["']""")),
    ("webhook_key", re.compile(r"webhook/send\?key=[A-Za-z0-9\-]{16,}")),
    ("key_secret", re.compile(
        r"""(?:api[_-]?key|apikey|secret|token|password|credential)s?["']?\s*[:=]\s*["'][A-Za-z0-9\-_]{16,}["']""")),
    # 企业微信会话 / 成员 ID（长串 wo/wr 开头）
    ("wecom_id", re.compile(r"\b(?:wo|wr)[A-Za-z0-9_\-]{18,}\b")),
]
PLACEHOLDER_RE = re.compile(r"填|占位|示例|YOUR|your_|XXXX|xxxx|<[^>]{2,}>|\{\}|\{\{")


def scan_zip(path):
    problems = []
    z = zipfile.ZipFile(path)
    for name in z.namelist():
        blob = z.read(name)
        for kl in KNOWN_LEAKS:
            if kl in blob:
                problems.append("%s: 已知泄露串 %s***" % (name, kl.decode()[:12]))
        if os.path.splitext(name)[1].lower() not in SCAN_EXT:
            continue
        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError:
            problems.append("%s: 非 UTF-8 文本（人工确认）" % name)
            continue
        for tag, pat in SECRET_PATTERNS:
            for m in pat.finditer(text):
                frag = m.group(0)
                if PLACEHOLDER_RE.search(frag):
                    continue
                problems.append("%s: %s → %s***" % (name, tag, frag[:44]))
    return problems


def main():
    missing = [f for f in REQUIRED if not os.path.exists(os.path.join(SKILL, f))]
    if missing:
        print("✗ 缺必需文件，先补齐再打包：")
        for f in missing:
            print("   -", f)
        return 1

    if os.path.exists(ZIP):
        os.remove(ZIP)

    n = 0
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(SKILL):
            dirs[:] = sorted(d for d in dirs if d not in DENY_DIR)
            rel_root = os.path.relpath(root, SKILL).replace("\\", "/")
            top = rel_root.split("/")[0] if rel_root != "." else ""
            if top and top not in INCLUDE_DIRS and top != "config":
                continue
            for fn in sorted(files):
                if fn in DENY_FILE:
                    continue
                ext = os.path.splitext(fn)[1].lower()
                if ext not in INCLUDE_EXT:
                    continue
                rel = os.path.relpath(os.path.join(root, fn), SKILL).replace("\\", "/")
                # config/ 只放脱敏示例
                if rel.startswith("config/") and rel != "config/config.example.json":
                    continue
                if not (rel in INCLUDE_FILES or rel.startswith("scripts/")
                        or rel.startswith("templates/") or rel == "config/config.example.json"):
                    continue
                z.write(os.path.join(root, fn), rel)
                n += 1

    problems = scan_zip(ZIP)
    if problems:
        print("❌ 敏感信息扫描未通过（%d 处），zip 严禁外发：" % len(problems))
        for p in problems:
            print("   -", p)
        return 1
    print("✅ 技能包已生成：%s（%.0f KB，%d 个文件，敏感扫描 clean）"
          % (ZIP, os.path.getsize(ZIP) / 1024.0, n))
    print("   外发前请再确认 config/config.example.json 里没有你自己的真实值。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
