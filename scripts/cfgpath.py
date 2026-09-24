# -*- coding: utf-8 -*-
"""cfgpath.py — config.json 定位 + 运行产物目录解析（全技能唯一入口）。

为什么需要它：
  技能包布局是 scripts/ 与 config/ 分离（config.json 在 config/ 下），
  而同事的扁平工作目录里 config.json 与脚本同级。各脚本若只从「自己所在目录」
  找 config.json，在技能包布局下必然 FileNotFoundError；若把路径写死
  （D:/某项目/...），换台机器、换个项目立刻全盘失效。
  → 所有脚本一律通过本模块拿配置与输出路径，禁止自行拼路径。

config.json 查找顺序（找到即返回）：
  ① 环境变量 ASIN_MONITOR_CONFIG（显式指定，最高优先级）
  ② <base>/config.json                 ← 扁平工作目录
  ③ <base>/../config.json              ← 技能包 scripts/ → 根
  ④ <base>/../config/config.json       ← 技能包标准布局
  ⑤ <base>/config/config.json

运行产物目录（monitor_data.json / 基线 / 图片 / 报告都写这里）：
  project_root = config.json 所在目录；若 config.json 在 config/ 子目录则再上一级。
  实际输出目录 = project_root + config.outputs.dir（默认 "data"；填 "." 表示就写项目根，
  这是「脚本与数据同目录」的老项目兼容用法）。
"""
import io
import json
import os

ENV_CONFIG = "ASIN_MONITOR_CONFIG"
ENV_MCP = "ASIN_MONITOR_MCP_CONFIG"

DEFAULT_MCP_CONFIG = os.path.join(os.path.expanduser("~"), ".workbuddy", "mcp.json")

CANDIDATES = (
    lambda d: os.path.join(d, "config.json"),
    lambda d: os.path.join(os.path.dirname(d), "config.json"),
    lambda d: os.path.join(os.path.dirname(d), "config", "config.json"),
    lambda d: os.path.join(d, "config", "config.json"),
)


def script_dir():
    return os.path.dirname(os.path.abspath(__file__))


def find_config(base=None, required=False):
    """返回 config.json 绝对路径；找不到时 required=False → None，True → FileNotFoundError。"""
    base = base or script_dir()
    tries = []
    env = (os.environ.get(ENV_CONFIG) or "").strip()
    if env:
        tries.append(env)
    tries.extend(f(base) for f in CANDIDATES)
    for p in tries:
        if p and os.path.exists(p):
            return os.path.abspath(p)
    if required:
        raise FileNotFoundError(
            "找不到 config.json，已尝试：\n  - " + "\n  - ".join(tries) +
            "\n请把 config/config.example.json 复制为 config.json 并填写，"
            "或用环境变量 %s 指定路径。" % ENV_CONFIG)
    return None


def load_config(base=None, required=True, default=None):
    p = find_config(base, required=required)
    if not p:
        return default if default is not None else {}
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def project_root(base=None):
    """config.json 所在目录；若位于 config/ 子目录则取其上一级。"""
    p = find_config(base)
    if not p:
        return base or script_dir()
    d = os.path.dirname(p)
    if os.path.basename(d).lower() == "config":
        return os.path.dirname(d)
    return d


def outputs_dir(base=None, create=True, cfg=None):
    """运行产物目录。cfg 可传入已加载的 config 以免重复读盘。"""
    cfg = cfg if cfg is not None else load_config(base, required=False, default={})
    rel = ((cfg.get("outputs") or {}).get("dir") or "data").strip() or "data"
    d = rel if os.path.isabs(rel) else os.path.join(project_root(base), rel)
    d = os.path.normpath(d)
    if create:
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
    return d


def out_path(name, base=None, cfg=None):
    """产物文件的绝对路径（name 为文件名，可含子目录）。"""
    return os.path.join(outputs_dir(base, cfg=cfg), name)


def template_path(name, base=None):
    """模板文件路径：技能包 <root>/templates/ 或 <scripts>/../templates/。"""
    root = project_root(base)
    for cand in (os.path.join(root, "templates", name),
                 os.path.join(script_dir(), "..", "templates", name)):
        cand = os.path.normpath(cand)
        if os.path.exists(cand):
            return cand
    return os.path.normpath(os.path.join(root, "templates", name))


def mcp_config_path(cfg=None):
    """MCP 配置文件路径：环境变量 > config.erp.mcpConfig > ~/.workbuddy/mcp.json。"""
    env = (os.environ.get(ENV_MCP) or "").strip()
    if env:
        return env
    cfg = cfg if cfg is not None else load_config(required=False, default={})
    p = ((cfg.get("erp") or {}).get("mcpConfig") or "").strip()
    return p or DEFAULT_MCP_CONFIG


def asin_list(cfg):
    """统一读取监控清单，返回 [(asin, name), ...]。
    兼容两种写法：
      "asinList": [{"asin": "B0...", "name": "产品名"}, ...]
      "asinList": ["B0...", ...]                      （无中文名时用 ASIN 兜底）
    """
    out = []
    for it in (cfg.get("asinList") or []):
        if isinstance(it, dict):
            a = (it.get("asin") or "").strip().upper()
            if a:
                out.append((a, (it.get("name") or a).strip()))
        elif isinstance(it, str) and it.strip():
            a = it.strip().upper()
            out.append((a, a))
    return out


def asin_keys(cfg):
    return [a for a, _ in asin_list(cfg)]


def seller_ids(cfg):
    """品牌方卖家 ID 集合。支持字符串或数组写法；空值忽略。"""
    v = cfg.get("brandSellerIds") or cfg.get("brandSellerId") or []
    if isinstance(v, str):
        v = [v]
    return {str(x).strip() for x in v if str(x).strip()}


def thresholds(cfg):
    t = cfg.get("thresholds") or {}
    return {
        "gross_rank": int(t.get("grossRank", 1000)),
        "sub_rank": int(t.get("subRank", 10)),
        "fba_days": int(t.get("fbaDays", 20)),
    }
