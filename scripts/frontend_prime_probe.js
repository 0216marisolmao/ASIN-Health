// Prime 专享折扣前台探针（amazon.com 商品详情页实时）
// 由 fetch_prime.py 通过 BrowserSkill（bsk）的 evaluate 命令注入执行，返回一行 JSON。
//
// 判定（两条信号取并集）：
//   ① primeNote —— 价格区出现 "Exclusive Prime price" / "Prime Member Price" 等文案（强信号）
//   ② corePrime —— 价格容器 HTML 里含 prime 标记（弱信号，页面改版时的兜底）
// 命中时，当前 buybox 价即为 Prime 专享价。
//
// 维护提示：所有 innerText 读取都写成 `(x.innerText || x.textContent || "")`。
// 直接写 e.innerText 会在遇到无 innerText 的节点时抛错，导致整页 evaluate 失败、
// 读数为空（实测在带 coupon 徽章的页面上必崩）。
JSON.stringify({
  title: (document.getElementById("productTitle") || {}).innerText || "",
  price: (document.querySelector("#corePrice_feature_div .a-offscreen") || {}).innerText
      || (document.querySelector("#apex_desktop .a-offscreen") || {}).innerText || "",
  basis: (document.querySelector(".basisPrice .a-offscreen") || {}).innerText || "",
  priceBlock: ((document.querySelector("#corePriceDisplay_desktop_feature_div") || {}).innerText || "")
      .replace(/\s+/g, " ").trim().slice(0, 300),
  corePrime: (function () {
      var c = document.querySelector("#corePrice_feature_div")
          || document.querySelector("#corePriceDisplay_desktop_feature_div");
      return c ? /prime/i.test(c.innerHTML) : false;
  })(),
  primeNote: (function () {
      var els = document.querySelectorAll("#corePrice_feature_div span, #corePrice_feature_div div, #apex_desktop span");
      for (var i = 0; i < els.length; i++) {
          var t = (els[i].innerText || "").replace(/\s+/g, " ").trim();
          if (/^(Prime Member Price|Exclusive Prime price|This price is exclusively for Amazon Prime members)/i.test(t))
              return t.slice(0, 80);
      }
      return "";
  })(),
  dealBadge: [...document.querySelectorAll("#dealBadge_feature_div, .dealBadge, #zeitgeistBadge_feature_div")]
      .map(e => (e.innerText || e.textContent || "").replace(/\s+/g, " ").trim()).filter(Boolean).slice(0, 2),
  avail: ((document.querySelector("#availability") || {}).innerText || "")
      .split("{")[0].replace(/\s+/g, " ").trim().slice(0, 40)
})
