---
name: amazon-asin-daily-monitor
agent_created: true
description: 亚马逊自家 ASIN 每日监控日报（可换品牌/站点复用）。多源合成一张 12 列监控表：可售状态/Buy Box/到手价/Coupon/Prime专享/Rating计数/Review星级/大类排名/小类排名/FBA库存预警，含环比基线与阈值预警（价格变动、券消失、Buy Box 被抢、排名波动、库存低于 N 天），产出企业微信 PNG 日报 + 自包含 HTML 报告（数据在前、总结在后、附口径与判定规则），可选整页长图。触发词：ASIN 监控、日报、自家 Listing 监控、库存预警、排名监控、BSR 波动、Buy Box 异常、coupon 消失、Prime 专享、监控日报、每日监控。
---

# 亚马逊 ASIN 每日监控日报（可复用模板）

盯自己家的 ASIN：一张表看全部关键维度，每天出图 + 出报告，异常自动标红。

## ⚠️ 第一铁律

**实时促销维度以平台前台实测为准，工具源只用于历史与趋势；工具源成批漏抓时必须显式告警，绝不静默降级。**

- Prime 专享折扣 → 前台实时抓取（BrowserSkill 通道）。卖家精灵 `primePrice` 只作抓不到时的兜底，并标 `prime_source`。
- Coupon → 卖家精灵为主，**为空时**用积加 ERP 广告券兜底（ERP 是券的权威口径）。
- 卖家精灵的 `coupon` 与 `primePrice` 都会**成批漏抓**：同一批数据几分钟内从有值变成全空 / 全 -1。
  看到「整列券消失」「整列 Prime 为空」先别下结论，核对 `coupons_erp.json` 与 `prime_frontend.json`。
- 抓取失败一律：保留兜底值 + 标来源 + 打印显式告警。**列错值比缺值危险。**

## 目录结构

```
amazon-asin-daily-monitor/
├── SKILL.md                        ← 本文件
├── config/config.example.json      ← 复制为 config.json 后填写（换品牌只改它）
├── templates/report_template.html  ← 内置 HTML 报告模板（含样式 + 口径表，占位符注入）
└── scripts/
    ├── cfgpath.py             ← config.json 定位 + 输出目录解析（全技能唯一路径入口）
    ├── mcpclient.py           ← MCP(HTTP JSON-RPC) 客户端（自 mcp.json 读鉴权，429 自动退避）
    ├── fetch_sellersprite.py  ← ① 取数：价格/券/评分/排名/BuyBox + 滚动三份环比基线
    ├── fetch_prime.py         ← ② Prime 专享：前台实时（bsk），覆盖接口值
    ├── fetch_fba_inventory.py ← ③ ERP 平台仓可用量 → erp_stock.json
    ├── apply_erp_coupons.py   ← ④ ERP Running 券兜底修正 coupon 列
    ├── apply_fba_stock.py     ← ⑤ 注入库存 + 算可售天数预警
    ├── generate_monitor_image.py ← ⑥ 出 PNG 日报
    ├── build_report.py        ← ⑦ 出 HTML 报告（渲染 templates/report_template.html）
    ├── render_report_png.py   ← ⑧ 报告整页长图（本地 http + bsk 全页截图）
    ├── run_pipeline.py        ← 一键跑 ①-⑦（可选 ⑧）
    ├── frontend_prime_probe.js← 前台探针（Prime 专享判定）
    └── pack_skill.py          ← 打包 + 脱敏 + 敏感信息扫描
```

## 环境前置

1. **Python 3.8+**，出图需 `Pillow`（`pip install pillow`）。
2. **三个 MCP 服务**（在 WorkBuddy 里连接，鉴权自动写到 `~/.workbuddy/mcp.json`）：
   | 服务名（可改） | 用途 | 缺了会怎样 |
   |---|---|---|
   | `sellersprite-universal` | 价格/券/评分/排名/BuyBox | 致命：取数步骤直接失败 |
   | `gerp-ads` | ERP 优惠券报表（券兜底） | coupon 列在工具漏抓时会误报「券消失」 |
   | `gerp-inventory` | ERP 平台仓库存（FBA 预警） | 库存列显示「—」 |
3. **BrowserSkill（bsk）**：CLI + 浏览器扩展，且**装在平时已登录亚马逊的那个浏览器上**。
   验证 `bsk doctor` 三项全绿（daemon running / extension connected）。
   装法：官方 install.ps1 若被安全策略拦（`iex` 禁用），改从
   `github.com/Tencent/BrowserSkill/releases` 下 zip，取 `bsk.exe` 放进 `~/.local/bin` 并加进 PATH。
   - **Windows 坑**：`bsk daemon start` 直接跑会报 `os error 5 / cannot start an independent
     Windows daemon`；必须**前台模式挂后台**：`bsk daemon start --foreground`（后台任务里跑），
     等 6 秒 `bsk doctor` 确认。`BSK_AUTO_START=0` 可避免 CLI 自己乱拉 daemon。
   - bsk 只在 Prime 专享这一列用得到。没有浏览器也能跑，其余 11 列不受影响（Prime 列会回声明显告警）。

## 快速开始

1. **建项目目录**，把 `scripts/`、`templates/` 拷进去；`config/config.example.json` 复制为
   `config/config.json`（或就放项目根，`cfgpath.py` 都能找到）。
2. **填 config.json**（六处必填，其余有默认值）：
   - `asinList`：`[{"asin": "B0XXXXXXXX", "name": "产品中文简称"}, ...]`，**行序即报表行序**。
   - `brandSellerIds`：品牌方卖家 ID 列表。怎么找：用卖家精灵 `getAsinDetail` 看自己 Listing 的
     `sellerId`，或在前台商品页点「Sold by」看卖家主页链接里的 `seller=` 参数。Buy Box 判定靠它。
   - `erp.marketId`：积加 ERP 里目标店铺的 ID。找法：调 `post_middle_base_polymerizeMarketSimple_list`
     （传 `countryCodes` 或 `shopNames`），返回里的 `marketId`。
   - `erp.warehouseName`：ERP「平台仓库存」里的仓库名，形如 `<品牌>:<站点>_FBA`。
     填错会导致库存全空——因为接口会返回所有站点仓，脚本按仓库名严格过滤。
   - `site`：`marketplace` / `domain` / `label` / `currency`。
   - `thresholds`：`grossRank`(默认1000) / `subRank`(默认10) / `fbaDays`(默认20)。
3. **跑一遍**：
   ```
   python run_pipeline.py                 # 全流程
   python run_pipeline.py --skip-prime    # 没开浏览器时
   python run_pipeline.py --with-report-png   # 额外出报告长图
   ```
   首次运行自动创建输出目录（`outputs.dir`，默认 `data/`）。
4. **接自动化**（WorkBuddy 定时任务 / cron 都可）：每日固定时间跑 `run_pipeline.py`，
   再把 `asin_monitor_daily.png` 推到企业微信。

## 输出与列口径（12 列）

| 列 | 数据源 | 判定规则 |
|---|---|---|
| ASIN / 产品 | config.json | 行序固定为清单顺序 |
| 可售状态 | 卖家精灵 `getAsinDetail` | 有返回=可售；无数据=不可售 |
| Buy Box | `sellerId` + `fulfillment` | 卖家属于品牌方**且** FBA → 是；否则红字「异常」 |
| 价格 | `getAsinDetail.price`（**到手价**） | 与上一交易日基线不一致 → 红字「异常」 |
| Coupon | `coupon`，空时 ERP 兜底 | 有券=紫徽章；昨日有今日无=红「券消失」；两日无=灰「无」 |
| Prime 专享 | **前台实时**，`primePrice` 兜底 | 有折扣=紫字 `Prime$29.99`；没做=留空 |
| Rating | `ratings` | 累计评论数，+N 绿徽章为环比增量 |
| Review | `rating` | 星级 0–5（如 ★4.6），不是评论数 |
| 大类排名 | `bsrRank` | 波动 ≥ `grossRank` → 红字「异常」 |
| 小类排名 | `subcategories[0].rank` | 波动 ≥ `subRank` → 红字「异常」 |
| FBA 库存预警 | ERP `availableQty` ÷ `avgUnitsOrdered30Days` | < `fbaDays` 天 → 红「异常 · N天」 |

**口径红线（混用必误报）**：
- 价格环比**只能**用同口径的到手价 + 自建 `price_baseline.json`。历史接口的 `price` 是**标价**
  （实例：某 ASIN 标价长期 59.99、到手价 41.99），拿它比价会让整列误报「异常」。
- `history.couponPrice` 是**券面额**（5% 券会被折成金额如 1.10），只用于判「昨日有无券」，不展示。
- ERP 券的 `discountAmount.currencyAmount` 对百分比券不可信（5% 也显示 $5.00），判券值只认
  `discountType` + `discount`。
- 库存分子**只能**用平台仓可用量。商品列表里的「可履约数量」口径更小，会系统性低估天数。
- 同一 ASIN 在同一仓库下可能有多条 MSKU（老 SKU 常年 0 库存），必须取**有库存的那条**。

## HTML 报告（内置模板）

`build_report.py` 把当日数字算好填进 `templates/report_template.html`，产出**单文件 HTML**
（样式内联、零外部依赖、可 Ctrl+P 导出 PDF）：

1. **一、监控数据** —— 12 列完整表格
2. **二、日报总结** —— KPI 卡（可售/BuyBox、价格异常、在券、Prime、排名异常、库存预警）
   + 库存预警明细表 + 在券清单 + Prime 清单 + 排名异常 + 评论增长 + 当日异动说明
3. **三、口径与判定规则** —— 逐列来源与规则，品牌卖家 ID / 仓库名 / 阈值全部由 config 注入

换品牌不用改 HTML：只改 config。要给客户换成别的列名或加列，改模板 + `build_data_row` 即可
（模板里任何 `{{TOKEN}}` 都会被脚本替换，未被替换的占位符会在运行日志里告警）。

## 已知坑（都有实证，别再踩）

- **卖家精灵 429 限流**：突发请求下网关返回 429。`mcpclient.py` 已对 429/5xx 自动退避重试。
  退化批次的特征是「coupon 全空 + primePrice 全 -1 同时出现」——**这不代表促销真的结束了**。
- **类目变更不环比**：亚马逊调类目后新旧排名不可比，脚本会自动跳过该列环比并告警。这是防误报设计，别当 bug 修。
- **变体家族排名/评论共享**：同父体的多个子 ASIN 会拿到相同的大类/小类排名与评论数，属正常。
- **个别 ASIN 挂多个小类**：脚本固定取 `subcategories[0]`（主类目）。
- **排名基线首日不判异常**：新接入时首次快照只建基线，自下一次采集起才输出波动预警。
- **报告文件名含中文**：`render_report_png.py` 会先复制成 ASCII 临时名再喂给浏览器（URL 编码坑）。
- **截图视口宽度**：默认 1600，低于页面 `max-width` 时右侧列会被容器裁掉（`outputs.pngWidth` 可调）。
- **`bsk` 相关命令**：Windows 上 `.cmd` 包装器在 subprocess 下不稳，脚本一律直接调 `bsk.exe`。
- **别把 `data/` 提交或外发**：里面是你的真实经营数据与历史基线。

## 维护

改完任何脚本 / 模板后重新打包：

```
python scripts/pack_skill.py
```

会做白名单收包（`data/`、`config.json`、缓存、运行产物一律不进包）+ 全量敏感信息扫描
（MCP token / Bearer / Authorization / webhook key / api key / 企业微信 ID / 已知泄露串；
「填/YOUR/占位」模板值豁免）。**扫描不过 = 退出码 1，zip 严禁外发。**

分发给别人前额外确认一次：`config/config.example.json` 里没有你自己的真实值
（ASIN 清单、卖家 ID、marketId、仓库名都属于经营信息）。
