export default {
  async fetch(request, env, ctx) {
    try {
      return await routeRequest(request, ctx);
    } catch (e) {
      return jsonResponse({
        ok: false,
        error: String(e && e.message ? e.message : e),
        stack: String(e && e.stack ? e.stack : "")
      }, 500, { "Cache-Control": "no-store" });
    }
  }
};

async function routeRequest(request, ctx) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") return corsResponse("", 204);
    if (url.pathname === "/finviz-industry") return handleFinvizIndustry(request, ctx);
    if (url.pathname === "/finviz-custom") return handleFinvizCustom(request, ctx);
    if (url.pathname === "/finviz-ticker") return handleFinvizTicker(request);
    if (url.pathname === "/finviz-debug") return handleFinvizDebug(request);

    const target = url.searchParams.get("url");
    if (!target) return corsResponse("Pass ?url=encoded_url", 400);
    try {
      const resp = await fetch(target, { headers: browserHeaders(target) });
      const body = await resp.text();
      return new Response(body, {
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Content-Type": resp.headers.get("Content-Type") || "text/plain",
          "Cache-Control": "public, max-age=300"
        }
      });
    } catch (e) {
      return corsResponse("Fetch error: " + e.message, 500);
    }
}

const PARSER_VERSION = "styled-row-v4-ticker-search";

async function handleFinvizIndustry(request, ctx) {
  const url = new URL(request.url);
  const name = url.searchParams.get("name") || "";
  if (!name.trim()) return jsonResponse({ ok: false, error: "Missing industry name" }, 400);
  const filters = ["cap_smallover", industrySlug(name), "sh_avgvol_o100"].join(",");
  return handleFinvizList(request, ctx, { label: name, filters, order: "-perf52w", source: "finviz-industry" });
}

async function handleFinvizCustom(request, ctx) {
  const url = new URL(request.url);
  const filters = cleanFilters(url.searchParams.get("f") || "");
  const order = cleanOrder(url.searchParams.get("o") || "-perf52w");
  if (!filters) return jsonResponse({ ok: false, error: "Missing f= Finviz filters" }, 400);
  return handleFinvizList(request, ctx, { label: "Custom Flow", filters, order, source: "finviz-custom" });
}

async function handleFinvizTicker(request) {
  const url = new URL(request.url);
  const ticker = String(url.searchParams.get("ticker") || "").trim().toUpperCase().replace(/[^A-Z0-9.-]/g, "");
  if (!ticker) return jsonResponse({ ok: false, error: "Missing ticker" }, 400);
  try {
    const quoteUrl = `https://finviz.com/quote.ashx?t=${encodeURIComponent(ticker)}`;
    const html = await fetchTextWithRetry(quoteUrl);
    const industryMatch = html.match(/<a[^>]+href=["'][^"']*f=(ind_[a-z0-9]+)[^"']*["'][^>]*>([\s\S]*?)<\/a>/i);
    const sectorMatch = html.match(/<a[^>]+href=["'][^"']*f=(sec_[a-z0-9]+)[^"']*["'][^>]*>([\s\S]*?)<\/a>/i);
    if (!industryMatch) return jsonResponse({ ok: false, ticker, error: "Finviz industry was not found for this ticker" }, 404, { "Cache-Control": "no-store" });
    return jsonResponse({
      ok: true,
      source: "finviz-ticker",
      ticker,
      industry: cleanCell(industryMatch[2]),
      industrySlug: industryMatch[1],
      sector: sectorMatch ? cleanCell(sectorMatch[2]) : "",
      sectorSlug: sectorMatch ? sectorMatch[1] : "",
      quoteUrl
    }, 200, { "Cache-Control": "public, max-age=86400" });
  } catch (e) {
    return jsonResponse({ ok: false, ticker, error: e.message }, 500, { "Cache-Control": "no-store" });
  }
}

async function handleFinvizList(request, ctx, cfg) {
  const url = new URL(request.url);
  const refresh = url.searchParams.get("refresh") === "1";
  const page = Math.max(1, parseInt(url.searchParams.get("page") || "1", 10) || 1);
  const start = ((page - 1) * 20) + 1;

  const cacheUrl = new URL(request.url);
  cacheUrl.searchParams.delete("refresh");
  cacheUrl.searchParams.set("_parser", PARSER_VERSION);
  const cacheKey = new Request(cacheUrl.toString(), request);
  if (!refresh) {
    const cached = await caches.default.match(cacheKey);
    if (cached) {
      const hit = new Response(cached.body, cached);
      hit.headers.set("Access-Control-Allow-Origin", "*");
      hit.headers.set("X-Worker-Cache", "HIT");
      return hit;
    }
  }

  try {
    const overviewUrl = finvizUrl(111, cfg.filters, cfg.order);
    const perfUrl = finvizUrl(141, cfg.filters, cfg.order);
    const valUrl = finvizUrl(121, cfg.filters, cfg.order);
    const techUrl = finvizUrl(171, cfg.filters, cfg.order, "ft=3");
    const overviewPack = await fetchOneFinvizPage(overviewUrl, 111, start);
    await sleep(350);
    const perfPack = await fetchOneFinvizPage(perfUrl, 141, start);
    await sleep(350);
    const valPack = await fetchOneFinvizPage(valUrl, 121, start);
    await sleep(350);
    const techPack = await fetchOneFinvizPage(techUrl, 171, start);
    const rows = mergeRows(overviewPack.rows, perfPack.rows, valPack.rows, techPack.rows);

    const payload = {
      ok: rows.length > 0,
      source: cfg.source,
      parserVersion: PARSER_VERSION,
      label: cfg.label,
      filters: cfg.filters,
      order: cfg.order,
      page,
      start,
      hasMore: perfPack.rows.length >= 19,
      fetchedAt: new Date().toISOString(),
      count: rows.length,
      parseDebug: {
        overviewRows: overviewPack.rows.length,
        performanceRows: perfPack.rows.length,
        valuationRows: valPack.rows.length,
        technicalRows: techPack.rows.length,
        overviewMode: overviewPack.mode,
        performanceMode: perfPack.mode,
        valuationMode: valPack.mode,
        technicalMode: techPack.mode
      },
      urls: { overview: overviewPack.url, performance: perfPack.url, valuation: valPack.url, technical: techPack.url },
      rows
    };

    if (!rows.length) {
      payload.error = "No stock rows parsed or no rows passed Vol M filter for this page.";
      return jsonResponse(payload, 502, { "Cache-Control": "no-store", "X-Worker-Cache": "BYPASS_EMPTY" });
    }

    const response = jsonResponse(payload, 200, { "Cache-Control": "public, max-age=21600", "X-Worker-Cache": "MISS" });
    ctx.waitUntil(caches.default.put(cacheKey, response.clone()));
    return response;
  } catch (e) {
    return jsonResponse({ ok: false, source: cfg.source, label: cfg.label, filters: cfg.filters, page, start, fetchedAt: new Date().toISOString(), error: e.message }, 500, { "Cache-Control": "no-store" });
  }
}

async function handleFinvizDebug(request) {
  const url = new URL(request.url);
  const filters = cleanFilters(url.searchParams.get("f") || ["cap_smallover", industrySlug(url.searchParams.get("name") || "Gold"), "sh_avgvol_o100"].join(","));
  const order = cleanOrder(url.searchParams.get("o") || "-perf52w");
  const page = Math.max(1, parseInt(url.searchParams.get("page") || "1", 10) || 1);
  const start = ((page - 1) * 20) + 1;
  const testUrl = start === 1 ? finvizUrl(141, filters, order) : addParam(finvizUrl(141, filters, order), "r", String(start));
  try {
    const resp = await fetch(testUrl, { headers: browserHeaders(testUrl) });
    const text = await resp.text();
    const parsed = parseFinvizRows(text, 141);
    return jsonResponse({ ok: true, filters, order, page, start, status: resp.status, length: text.length, parsedRows: parsed.rows.length, parseMode: parsed.mode, sampleRows: parsed.rows.slice(0, 5) }, 200, { "Cache-Control": "no-store" });
  } catch (e) {
    return jsonResponse({ ok: false, error: e.message }, 500);
  }
}

function finvizUrl(view, filters, order, extra) {
  let out = `https://finviz.com/screener.ashx?v=${view}&f=${encodeURIComponent(filters).replace(/%2C/g, ",")}&o=${encodeURIComponent(order || "-perf52w")}`;
  if (extra) out += `&${extra}`;
  return out;
}

async function fetchOneFinvizPage(baseUrl, view, start) {
  const pageUrl = start === 1 ? baseUrl : addParam(baseUrl, "r", String(start));
  const html = await fetchTextWithRetry(pageUrl);
  const parsed = parseFinvizRows(html, view);
  return { rows: dedupeRows(parsed.rows), mode: parsed.mode, url: pageUrl };
}

async function fetchTextWithRetry(url) {
  let lastErr;
  for (let i = 0; i < 4; i++) {
    try {
      const resp = await fetch(url, { headers: browserHeaders(url) });
      if (resp.status === 429) {
        lastErr = new Error("Finviz HTTP 429");
        await sleep(2500 * (i + 1));
        continue;
      }
      if (!resp.ok) throw new Error(`Finviz HTTP ${resp.status}`);
      const text = await resp.text();
      if (!text || text.length < 1000) throw new Error("Finviz returned a short/empty page");
      return text;
    } catch (e) {
      lastErr = e;
      await sleep(1000 * (i + 1));
    }
  }
  throw lastErr;
}

function parseFinvizRows(html, view) {
  let rows = parseOldFinvizTable(html, view);
  if (rows.length) return { rows, mode: "old-html-table" };
  rows = parseAnchorBasedRows(html, view);
  if (rows.length) return { rows, mode: "anchor-html-table" };
  return { rows: [], mode: "none" };
}

function parseOldFinvizTable(html, view) {
  const out = [];
  const rowMatches = html.match(/<tr[^>]*>([\s\S]*?)<\/tr>/gi) || [];
  for (const rowHtml of rowMatches) {
    if (!/quote\.ashx\?t=/i.test(rowHtml)) continue;
    const ticker = extractTickerFromRow(rowHtml);
    const cells = extractCells(rowHtml);
    if (!ticker || isHeaderRow(cells)) continue;
    const row = rowFromCells(ticker, cells, view, extractIndustryFromRow(rowHtml));
    if (row) out.push(row);
  }
  return out;
}

function parseAnchorBasedRows(html, view) {
  const out = [];
  const styledRows = html.match(/<tr[^>]*class=["'][^"']*\bstyled-row\b[^"']*["'][^>]*>[\s\S]*?<\/tr>/gi) || [];
  const rowMatches = styledRows.length ? styledRows : (html.match(/<tr[^>]*>([\s\S]*?)<\/tr>/gi) || []);
  for (const rowHtml of rowMatches) {
    const isRealStockRow = /data-boxover-ticker=/i.test(rowHtml) || /href=["'][^"']*stock\?t=/i.test(rowHtml) || /href=["'][^"']*\/stock\?t=/i.test(rowHtml);
    if (!isRealStockRow) continue;
    const ticker = extractTickerFromRow(rowHtml);
    const cells = extractCells(rowHtml);
    if (!ticker || cells.length < 8 || isHeaderRow(cells)) continue;
    const row = rowFromCells(ticker, cells, view, extractIndustryFromRow(rowHtml));
    if (row) out.push(row);
  }
  return out;
}

function isHeaderRow(cells) {
  const joined = cells.join(" ").toLowerCase();
  return joined.includes("perf week") || joined.includes("market cap") || joined.includes("sma20") || joined.includes("20-day sma") || joined.includes("ticker");
}

function extractTickerFromRow(rowHtml) {
  const patterns = [/data-boxover-ticker=["']([^"']+)["']/i, /href=["'][^"']*stock\?t=([^"&']+)/i, /href=["'][^"']*\/stock\?t=([^"&']+)/i, /quote\.ashx\?t=([^"&']+)/i, /data-ticker=["']([^"']+)["']/i, /data-symbol=["']([^"']+)["']/i];
  for (const pattern of patterns) {
    const match = rowHtml.match(pattern);
    if (match) return normalizeTicker(match[1]);
  }
  return "";
}

function extractIndustryFromRow(rowHtml) {
  const match = rowHtml.match(/data-boxover-industry=["']([^"']*)["']/i);
  return match ? decodeHtml(match[1]) : "";
}

function extractCells(rowHtml) {
  const cells = [];
  const cellMatches = rowHtml.match(/<t[dh][^>]*>([\s\S]*?)<\/t[dh]>/gi) || [];
  for (const cellHtml of cellMatches) cells.push(cleanCell(cellHtml));
  return cells;
}

function rowFromCells(ticker, cells, view, industry) {
  if (!ticker || cells.length < 3) return null;
  if (view === 111) return { ticker, company: cells[2] || "", sector: cells[3] || "", industry: cells[4] || industry || "", country: cells[5] || "", marketCap: cells[6] || "", pe: cells[7] || "", price: cells[8] || "", change: cells[9] || "", volume: cells[10] || "", quoteUrl: `https://finviz.com/quote.ashx?t=${encodeURIComponent(ticker)}` };
  if (view === 141) return { ticker, industry: industry || "", perfWeek: cells[2] || "", perfMonth: cells[3] || "", perfQuart: cells[4] || "", perfHalf: cells[5] || "", perfYtd: cells[6] || "", perfYear: cells[7] || "", volW: cells[11] || "", volM: cells[12] || "", avgVolume: cells[13] || "", relVolume: cells[14] || "", price: cells[15] || "", change: cells[16] || "", volume: cells[17] || "", quoteUrl: `https://finviz.com/quote.ashx?t=${encodeURIComponent(ticker)}` };
  if (view === 121) return { ticker, industry: industry || "", marketCap: cells[2] || "", pe: cells[3] || "", fwdPe: cells[4] || "", peg: cells[5] || "", ps: cells[6] || "", pb: cells[7] || "", pc: cells[8] || "", pfcf: cells[9] || "" };
  if (view === 171) return { ticker, industry: industry || "", sma20: cells[4] || "", sma50: cells[5] || "", sma200: cells[6] || "" };
  return { ticker, industry: industry || "" };
}

function mergeRows(overviewRows, perfRows, valRows, techRows) {
  const byTicker = new Map();
  for (const row of overviewRows || []) if (row.ticker) byTicker.set(row.ticker, { ...row });
  for (const row of perfRows || []) if (row.ticker) byTicker.set(row.ticker, { ...(byTicker.get(row.ticker) || { ticker: row.ticker }), ...row, industry: row.industry || (byTicker.get(row.ticker) || {}).industry || "" });
  for (const row of valRows || []) if (row.ticker) byTicker.set(row.ticker, { ...(byTicker.get(row.ticker) || { ticker: row.ticker }), ...row, industry: row.industry || (byTicker.get(row.ticker) || {}).industry || "" });
  for (const row of techRows || []) if (row.ticker) byTicker.set(row.ticker, { ...(byTicker.get(row.ticker) || { ticker: row.ticker }), ...row, industry: row.industry || (byTicker.get(row.ticker) || {}).industry || "" });
  const rows = Array.from(byTicker.values());
  for (const row of rows) {
    row.rotationScore = calcRotation(row);
    row.buyGauge = calcBuyGauge(row);
  }
  rows.sort((a, b) => (typeof b.rotationScore === "number" ? b.rotationScore : -999) - (typeof a.rotationScore === "number" ? a.rotationScore : -999));
  return rows;
}

function dedupeRows(rows) {
  const map = new Map();
  for (const row of rows || []) if (row.ticker) map.set(row.ticker, { ...(map.get(row.ticker) || {}), ...row });
  return Array.from(map.values());
}

function calcRotation(row) {
  const d = parsePercent(row.perfMonth), e = parsePercent(row.perfQuart), l = parsePercent(row.volW), m = parsePercent(row.volM);
  if ([d, e, l, m].some(v => v === null) || l === 0 || m === 0) return "";
  const score = (((d * 0.7) + ((d - (e / 3)) * 0.3)) * 0.4) + ((((d * 0.4) + (e * 0.6)) * (m / l)) * 0.6);
  return Number(score.toFixed(2));
}

function calcBuyGauge(row) {
  const u = typeof row.rotationScore === "number" ? row.rotationScore : null;
  const h = parsePercent(row.perfYear), e = parsePercent(row.perfQuart), l = parsePercent(row.volW), m = parsePercent(row.volM);
  if (u === null || h === null || e === null || l === null || m === null) return "";
  if (h > 0 && e > 0 && u > 0.4 && l >= m) return "🔥 Overheated / Expanding Vol";
  if (h > 0 && e > 0 && u > 0.4) return "🔥 Overheated";
  if (h > 0 && e > 0 && u > 0.15) return "🏆 Holy Grail";
  if (h > 0 && e > 0 && u > 0) return "🟢 Constructive";
  if (h > 0 && e < 0 && u > 0.15) return "🚀 Reversal (Dip Buy)";
  if (h > 0 && e < 0 && u > 0) return "👀 Early Reversal";
  if (h < 0 && e > 0 && u > 0.15) return "⚡ Trend Shift (Recovery)";
  if (h < 0 && e > 0 && u > 0) return "👀 Early Recovery";
  if (h < 0 && e < 0 && u > 0.15) return "🐣 Bottom Breakout";
  if (h < 0 && e < 0 && u > 0) return "👀 Early Bottom";
  if (h > 0 && e > 0 && u < 0) return "🪤 Bull Trap";
  if (h < 0 && e < 0 && u < 0) return "⛔ Avoid";
  return "⚖️ Neutral";
}

function industrySlug(name) {
  const overrides = { "Oil & Gas Drilling": "ind_oilgasdrilling", "Beverages - Brewers": "ind_beveragesbrewers", "Software - Application": "ind_softwareapplication" };
  if (overrides[name]) return overrides[name];
  return "ind_" + String(name).toLowerCase().replace(/&/g, "").replace(/[\/\-]/g, "").replace(/[^a-z0-9]/g, "");
}

function cleanFilters(value) {
  return String(value || "").split(",").map(s => s.trim()).filter(Boolean).join(",");
}

function cleanOrder(value) {
  return String(value || "-perf52w").replace(/[^a-zA-Z0-9_-]/g, "") || "-perf52w";
}

function addParam(rawUrl, key, value) {
  const url = new URL(rawUrl);
  url.searchParams.set(key, value);
  return url.toString();
}

function cleanCell(html) {
  return decodeHtml(String(html || "").replace(/<script[\s\S]*?<\/script>/gi, "").replace(/<style[\s\S]*?<\/style>/gi, "").replace(/<[^>]+>/g, "").replace(/\s+/g, " ").trim());
}

function decodeHtml(text) {
  return String(text || "").replace(/&amp;/g, "&").replace(/&nbsp;/g, " ").replace(/&#37;/g, "%").replace(/&#x25;/g, "%").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'");
}

function normalizeTicker(value) {
  const ticker = String(value || "").trim().replace(/^\$/, "").toUpperCase();
  return /^[A-Z][A-Z0-9.\-]{0,7}$/.test(ticker) ? ticker : "";
}

function parseRawNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const cleaned = String(value).replace(/[%,$,+]/g, "").replace(/,/g, "").trim();
  if (!cleaned || cleaned === "-") return null;
  const num = Number(cleaned);
  return Number.isFinite(num) ? num : null;
}

function parsePercent(value) {
  const num = parseRawNumber(value);
  return num === null ? null : num / 100;
}

function browserHeaders(url) {
  const isFinviz = String(url || "").includes("finviz.com");
  return { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36", "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9", "Referer": isFinviz ? "https://finviz.com/screener.ashx" : "https://www.google.com/" };
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function corsResponse(body, status) {
  return new Response(body, { status, headers: { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, OPTIONS", "Access-Control-Allow-Headers": "Content-Type", "Content-Type": "text/plain" } });
}

function jsonResponse(data, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(data, null, 2), { status, headers: { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, OPTIONS", "Access-Control-Allow-Headers": "Content-Type", "Content-Type": "application/json", ...extraHeaders } });
}
