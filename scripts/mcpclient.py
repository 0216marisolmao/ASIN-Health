# -*- coding: utf-8 -*-
"""mcpclient.py — 直连 WorkBuddy MCP 服务（HTTP JSON-RPC）的通用客户端。

为什么直连而不用内置 MCP 工具：
  自动化在无人值守的定时任务里跑，直连端点最稳（不依赖工具索引刷新）。
鉴权：从 ~/.workbuddy/mcp.json 的对应服务节点读 url + headers，代码里零凭据。
      （路径可用环境变量 ASIN_MONITOR_MCP_CONFIG 或 config.erp.mcpConfig 覆盖。）

用法：
    from mcpclient import McpClient, load_server_config
    url, headers = load_server_config("sellersprite-universal")   # 或 "gerp-ads"
    c = McpClient(url, headers)
    c.initialize()
    body = c.call_tool("getAsinDetail", {"asin": "B0...", "marketplace": "US"})

限流：网关在突发请求下会返回 HTTP 429。call_tool 默认对 429/5xx 自动退避重试
      （最多 retries 次），并把最终失败转成 RuntimeError 让调用方显式处理。
"""
import json
import time
import urllib.error
import urllib.request

import cfgpath

DEFAULT_TIMEOUT = 180
RETRY_STATUS = {429, 500, 502, 503, 504}


def load_server_config(server, cfg=None):
    """从 mcp.json 读指定服务的 url 与请求头。"""
    path = cfgpath.mcp_config_path(cfg)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise RuntimeError(
            "找不到 MCP 配置 %s；请在 WorkBuddy 中连接对应服务，"
            "或用环境变量 %s 指定路径。" % (path, cfgpath.ENV_MCP))
    node = (data.get("mcpServers") or {}).get(server)
    if not node:
        raise RuntimeError("MCP 配置 %s 中没有服务 %r（可用的有：%s）"
                           % (path, server, ", ".join(sorted((data.get("mcpServers") or {}).keys()))))
    if not node.get("url"):
        raise RuntimeError("MCP 服务 %r 没有 url 字段（仅支持 HTTP/SSE 型服务）" % server)
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    headers.update(node.get("headers") or {})
    return node["url"], headers


class McpClient:
    def __init__(self, url, headers, timeout=DEFAULT_TIMEOUT, retries=2):
        self.url = url
        self.headers = dict(headers)
        self.timeout = timeout
        self.retries = retries
        self.sid = None
        self._id = 0

    def _post(self, payload):
        raw = None
        last = None
        for attempt in range(self.retries + 1):
            h = dict(self.headers)
            if self.sid:
                h["Mcp-Session-Id"] = self.sid
            req = urllib.request.Request(
                self.url, data=json.dumps(payload).encode("utf-8"),
                headers=h, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    if resp.headers.get("Mcp-Session-Id"):
                        self.sid = resp.headers.get("Mcp-Session-Id")
                    raw = resp.read().decode("utf-8")
                break
            except urllib.error.HTTPError as e:
                last = e
                if e.code in RETRY_STATUS and attempt < self.retries:
                    wait = 30 * (attempt + 1)
                    print("  ! MCP HTTP %d（疑似限流），%ds 后重试…" % (e.code, wait))
                    time.sleep(wait)
                    continue
                raise RuntimeError("MCP HTTP %d: %s" % (e.code, e.reason))
            except urllib.error.URLError as e:
                last = e
                if attempt < self.retries:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise RuntimeError("MCP 连接失败: %s" % e)
        if raw is None:
            raise RuntimeError("MCP 无响应: %s" % last)
        chunks = [ln[5:].strip() for ln in raw.splitlines() if ln.startswith("data:")]
        return json.loads(chunks[-1]) if chunks else json.loads(raw)

    def initialize(self):
        self._id += 1
        self._post({"jsonrpc": "2.0", "id": self._id, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "workbuddy", "version": "1.0"}}})

    def call_tool(self, name, arguments):
        self._id += 1
        r = self._post({"jsonrpc": "2.0", "id": self._id, "method": "tools/call",
                        "params": {"name": name, "arguments": arguments}})
        if r.get("error"):
            raise RuntimeError("MCP error: %s" % r["error"])
        content = (r.get("result") or {}).get("content") or []
        text = content[0].get("text") if content else json.dumps(r)
        return json.loads(text)


def unwrap(body):
    """取 MCP 返回里的业务数据。不同服务层级不同，逐层剥壳。

    实测形态：
      sellersprite: {"code":0,"data":{"data":{...}}}
      gerp-ads:     {"data":{"data":[...]}}
      gerp-inventory: {"data":{"data":{"rows":[...]}}}
    """
    d = body.get("data", body) if isinstance(body, dict) else body
    if isinstance(d, dict) and "data" in d:
        inner = d.get("data")
        if inner is not None:
            return inner
        return d
    return d
