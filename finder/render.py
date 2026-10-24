"""Render docs/index.html: a static page with the listing data embedded as JSON
and all filtering done in the browser. No build step, no framework."""
import html
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config, dedupe, store


def render(listings_store, source_status, state):
    active = dedupe.collapse(store.active(listings_store))
    gone = [r for r in listings_store.values() if r.get("gone") and not r.get("dup_of")]
    t = store.now()
    for r in active:
        r["is_new"] = store.is_new(r, t)
        r["price_drop"] = bool(r.get("price_history")) and r["price_history"][-1]["to"] < r["price_history"][-1]["from"]
    active.sort(key=lambda r: (not r["is_new"], r["first_seen"]), reverse=False)
    active.sort(key=lambda r: r["first_seen"], reverse=True)

    ny = t.astimezone(ZoneInfo(config.TIMEZONE))
    updated = ny.strftime("%a %d %b, %-I:%M %p") + " NY time"
    payload = {
        "updated": updated,
        "updated_iso": store.iso(t),
        "max_price": config.MAX_PRICE,
        "new_hours": config.NEW_HIGHLIGHT_HOURS,
        "listings": active,
        "gone": gone[:200],
        "sources": source_status,
        "neighborhoods": sorted({r["neighborhood"] for r in active if r.get("neighborhood")}),
    }
    page = TEMPLATE.replace("__DATA__", json.dumps(payload, default=str).replace("</", "<\\/"))
    page = page.replace("__UPDATED__", html.escape(updated))
    page = page.replace("__COUNT__", str(len(active)))
    page = page.replace("__NEW__", str(sum(1 for r in active if r["is_new"])))
    os.makedirs(config.DOCS_DIR, exist_ok=True)
    with open(os.path.join(config.DOCS_DIR, "index.html"), "w") as f:
        f.write(page)
    with open(os.path.join(config.DOCS_DIR, "listings.json"), "w") as f:
        json.dump(payload, f, default=str)
    with open(os.path.join(config.DOCS_DIR, ".nojekyll"), "w") as f:
        f.write("")


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NYC Studio Finder</title>
<meta name="description" content="Studios under $2,500 in lower and midtown Manhattan and Williamsburg, refreshed hourly.">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🏙️</text></svg>">
<style>
:root{
  --bg:#f6f4ef; --card:#ffffff; --ink:#1b1b1f; --muted:#6b6b73; --line:#e5e1d8;
  --accent:#0f5f5c; --accent-ink:#ffffff; --new:#fff3c4; --new-ink:#7a5a00;
  --drop:#e3f4ea; --drop-ink:#1e6b3a; --warn:#fbe9e7; --warn-ink:#9b2c20; --chip:#eeeae2;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --bg:#15161a; --card:#1f2026; --ink:#ebe9e4; --muted:#9a9aa3; --line:#2c2d35;
    --accent:#4fb3ad; --accent-ink:#0f1a19; --new:#3a3312; --new-ink:#f2d26b;
    --drop:#163424; --drop-ink:#8fd7a8; --warn:#3a1d1a; --warn-ink:#f0a89e; --chip:#2a2b33;
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,Helvetica,Arial,sans-serif}
a{color:var(--accent)}
.wrap{max-width:1100px;margin:0 auto;padding:20px 16px 60px}
header{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px 16px;margin-bottom:6px}
h1{font-size:26px;margin:0;letter-spacing:-.01em}
.sub{color:var(--muted);font-size:14px}
.stats{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0 18px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 14px;min-width:120px}
.stat b{display:block;font-size:22px;font-weight:650}
.stat span{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.controls{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:14px;position:sticky;top:0;background:var(--bg);padding:8px 0;z-index:2}
.controls select,.controls input[type=search]{background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:10px;padding:8px 10px;font:inherit}
.controls input[type=search]{min-width:180px;flex:1}
.toggle{display:inline-flex;align-items:center;gap:6px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:7px 10px;cursor:pointer;user-select:none;font-size:14px}
.toggle input{accent-color:var(--accent)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px;align-items:start}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden;display:flex;flex-direction:column;transition:transform .08s}
.card:hover{transform:translateY(-1px)}
.card.new{box-shadow:0 0 0 2px var(--new-ink) inset}
.img{aspect-ratio:16/10;background:var(--chip);display:block;overflow:hidden}
.img img{width:100%;height:100%;object-fit:cover;display:block}
.body{padding:12px 14px 14px;display:flex;flex-direction:column;gap:6px;flex:1}
.top{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.price{font-size:20px;font-weight:700}
.price small{font-size:12px;color:var(--muted);font-weight:500}
.neigh{font-weight:600}
.title{color:var(--ink);text-decoration:none;font-size:14px;line-height:1.35}
.title:hover{text-decoration:underline}
.meta{color:var(--muted);font-size:12.5px;display:flex;flex-wrap:wrap;gap:4px 10px}
.badges{display:flex;flex-wrap:wrap;gap:5px;margin-top:2px}
.badge{font-size:11px;padding:2px 7px;border-radius:999px;background:var(--chip);color:var(--muted);font-weight:600;letter-spacing:.02em}
.badge.new{background:var(--new);color:var(--new-ink)}
.badge.drop{background:var(--drop);color:var(--drop-ink)}
.badge.warn{background:var(--warn);color:var(--warn-ink)}
.cta{margin-top:auto;padding-top:8px}
.also{display:block;margin-top:6px;font-size:12px;color:var(--muted)} .also a{margin-right:2px}
.cta>a{display:inline-block;background:var(--accent);color:var(--accent-ink);text-decoration:none;padding:7px 12px;border-radius:9px;font-weight:600;font-size:13px}
.empty{color:var(--muted);padding:40px;text-align:center;border:1px dashed var(--line);border-radius:14px}
details{margin-top:28px;color:var(--muted);font-size:13px}
summary{cursor:pointer;color:var(--ink);font-weight:600}
table{border-collapse:collapse;width:100%;margin-top:10px;font-size:13px}
td,th{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}
.ok{color:var(--drop-ink)} .blocked{color:var(--warn-ink)} .skipped{color:var(--muted)}
footer{margin-top:30px;color:var(--muted);font-size:12.5px}
@media (max-width:600px){ .grid{grid-template-columns:1fr} h1{font-size:22px} .controls{position:static} .stat{min-width:90px;flex:1} }
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>NYC Studio Finder</h1>
  <div class="sub">Studios at or under $__MAXPRICE__ across lower &amp; midtown Manhattan and Williamsburg. Updated hourly. Last run: <b id="updated">__UPDATED__</b></div>
</header>

<div class="stats">
  <div class="stat"><b id="s-total">__COUNT__</b><span>live listings</span></div>
  <div class="stat"><b id="s-new">__NEW__</b><span>new in 24h</span></div>
  <div class="stat"><b id="s-shown">0</b><span>matching filters</span></div>
</div>

<div class="controls">
  <input type="search" id="q" placeholder="Search title, address, neighbourhood…">
  <select id="neigh"><option value="">All neighbourhoods</option></select>
  <select id="source"><option value="">All sources</option></select>
  <select id="maxp">
    <option value="2500">Up to $2,500</option>
    <option value="2300">Up to $2,300</option>
    <option value="2200">Up to $2,200</option>
    <option value="2000">Up to $2,000</option>
    <option value="1800">Up to $1,800</option>
  </select>
  <select id="sort">
    <option value="newest">Newest first</option>
    <option value="price">Price: low to high</option>
    <option value="neigh">Neighbourhood</option>
  </select>
  <label class="toggle"><input type="checkbox" id="newonly"> New only</label>
  <label class="toggle"><input type="checkbox" id="hidesub" checked> Hide sublets &amp; shares</label>
  <label class="toggle"><input type="checkbox" id="hideinf"> Hide inferred areas</label>
</div>

<div id="grid" class="grid"></div>
<div id="empty" class="empty" hidden>Nothing matches those filters right now. Loosen the price or neighbourhood, or check back in an hour.</div>

<details id="gonewrap">
  <summary>Recently taken down (<span id="gonecount">0</span>)</summary>
  <div id="gone" class="grid" style="margin-top:12px;opacity:.75"></div>
</details>

<details>
  <summary>Source health this run</summary>
  <table id="sources"><thead><tr><th>Source</th><th>Status</th><th>Listings</th><th>Note</th></tr></thead><tbody></tbody></table>
  <p>Sources marked blocked refuse automated requests. Where a Tavily fallback exists it runs every few hours rather than every hour to stay inside the free credit allowance.</p>
</details>

<footer>
  <p>Prices and availability come straight from each platform and can change between runs. Always confirm on the listing itself. “Inferred area” means the platform did not state a neighbourhood and it was worked out from the zip code or description.</p>
</footer>
</div>

<script id="data" type="application/json">__DATA__</script>
<script>
(function(){
  const DATA = JSON.parse(document.getElementById('data').textContent);
  const $ = s => document.querySelector(s);
  const fmt = n => '$' + Number(n).toLocaleString('en-US');
  const esc = s => String(s==null?'':s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const sourceLabel = {}; (DATA.sources||[]).forEach(s => sourceLabel[s.name] = s.label); DATA.listings.forEach(l => { if(l.source_label) sourceLabel[l.source] = l.source_label; });

  // Populate selects
  const neighSel = $('#neigh'); DATA.neighborhoods.forEach(n => { const o = document.createElement('option'); o.value = n; o.textContent = n; neighSel.appendChild(o); });
  const srcSel = $('#source');
  const srcCounts = {}; DATA.listings.forEach(l => srcCounts[l.source] = (srcCounts[l.source]||0)+1);
  Object.keys(srcCounts).sort().forEach(s => { const o = document.createElement('option'); o.value = s; o.textContent = (sourceLabel[s]||s) + ' (' + srcCounts[s] + ')'; srcSel.appendChild(o); });

  // Restore per-viewer filter prefs
  const PREFS = 'nycsf-prefs';
  try { const p = JSON.parse(localStorage.getItem(PREFS)||'{}'); for (const k in p){ const el = document.getElementById(k); if(!el) continue; if(el.type==='checkbox') el.checked = p[k]; else el.value = p[k]; } } catch(e){}
  function savePrefs(){ try { const p = {}; ['neigh','source','maxp','sort','newonly','hidesub','hideinf'].forEach(k => { const el = document.getElementById(k); p[k] = el.type==='checkbox' ? el.checked : el.value; }); localStorage.setItem(PREFS, JSON.stringify(p)); } catch(e){} }

  function ago(iso){ const d = (Date.now() - new Date(iso).getTime())/36e5; if (d < 1) return 'just now'; if (d < 24) return Math.round(d) + 'h ago'; const days = Math.round(d/24); return days + (days===1?' day ago':' days ago'); }

  function card(l, gone){
    const flags = l.flags||[];
    const badges = [];
    if (l.is_new && !gone) badges.push('<span class="badge new">New</span>');
    if (l.price_drop) badges.push('<span class="badge drop">Price drop</span>');
    if (l.no_fee === true) badges.push('<span class="badge">No fee</span>');
    if (flags.includes('alcove-or-junior')) badges.push('<span class="badge">Alcove / Jr 1BR</span>');
    if (flags.includes('sublet')) badges.push('<span class="badge warn">Sublet</span>');
    if (flags.includes('room-or-share')) badges.push('<span class="badge warn">Room / share?</span>');
    if (flags.includes('area-inferred')) badges.push('<span class="badge">Inferred area</span>');
    if (l.via_tavily) badges.push('<span class="badge">via Tavily</span>');
    if (gone) badges.push('<span class="badge warn">Taken down ' + esc(ago(l.gone_at||l.last_seen)) + '</span>');
    const meta = [];
    if (l.address) meta.push(esc(l.address));
    if (l.sqft) meta.push(l.sqft + ' sq ft');
    if (l.bathrooms) meta.push(l.bathrooms + ' bath');
    meta.push(esc(sourceLabel[l.source]||l.source));
    meta.push('seen ' + esc(ago(l.first_seen)));
    const img = l.image_url ? '<a class="img" href="'+esc(l.url)+'" target="_blank" rel="noopener"><img loading="lazy" src="'+esc(l.image_url)+'" alt=""></a>' : '';
    return '<article class="card'+(l.is_new&&!gone?' new':'')+'">'+img+
      '<div class="body"><div class="top"><div class="price">'+fmt(l.price)+'<small>/mo</small></div><div class="neigh">'+esc(l.neighborhood||'')+'</div></div>'+
      '<a class="title" href="'+esc(l.url)+'" target="_blank" rel="noopener">'+esc(l.title)+'</a>'+
      (l.description?'<div class="meta" style="display:block">'+esc(l.description)+'</div>':'')+
      '<div class="meta">'+meta.join(' · ')+'</div>'+
      '<div class="badges">'+badges.join('')+'</div>'+
      '<div class="cta"><a href="'+esc(l.url)+'" target="_blank" rel="noopener">View on '+esc(sourceLabel[l.source]||l.source)+' ↗</a>'+
      ((l.also&&l.also.length)?'<span class="also">also on '+l.also.map(a=>'<a href="'+esc(a.url)+'" target="_blank" rel="noopener">'+esc(a.source_label)+'</a>').join(', ')+'</span>':'')+'</div>'+
      '</div></article>';
  }

  function apply(){
    const q = $('#q').value.trim().toLowerCase();
    const n = $('#neigh').value, s = $('#source').value, maxp = +$('#maxp').value, sort = $('#sort').value;
    const newonly = $('#newonly').checked, hidesub = $('#hidesub').checked, hideinf = $('#hideinf').checked;
    let rows = DATA.listings.filter(l => {
      if (l.price > maxp) return false;
      if (n && l.neighborhood !== n) return false;
      if (s && l.source !== s) return false;
      if (newonly && !l.is_new) return false;
      const f = l.flags||[];
      if (hidesub && (f.includes('sublet') || f.includes('room-or-share'))) return false;
      if (hideinf && f.includes('area-inferred')) return false;
      if (q){ const hay = [l.title,l.address,l.neighborhood,l.description,l.neighborhood_raw].join(' ').toLowerCase(); if (!hay.includes(q)) return false; }
      return true;
    });
    if (sort==='price') rows.sort((a,b)=>a.price-b.price || (a.first_seen<b.first_seen?1:-1));
    else if (sort==='neigh') rows.sort((a,b)=>(a.neighborhood||'').localeCompare(b.neighborhood||'') || a.price-b.price);
    else rows.sort((a,b)=> (b.first_seen>a.first_seen?1:b.first_seen<a.first_seen?-1:0) || a.price-b.price);
    $('#grid').innerHTML = rows.map(l=>card(l,false)).join('');
    $('#empty').hidden = rows.length>0;
    $('#s-shown').textContent = rows.length;
    savePrefs();
  }
  ['q','neigh','source','maxp','sort','newonly','hidesub','hideinf'].forEach(id => document.getElementById(id).addEventListener('input', apply));
  apply();

  // Gone listings
  const gone = DATA.gone||[]; $('#gonecount').textContent = gone.length;
  $('#gone').innerHTML = gone.sort((a,b)=> (b.gone_at||'') > (a.gone_at||'') ? 1 : -1).slice(0,60).map(l=>card(l,true)).join('');

  // Source health
  const tb = $('#sources tbody');
  (DATA.sources||[]).forEach(s => {
    const tr = document.createElement('tr');
    tr.innerHTML = '<td>'+esc(s.label)+'</td><td class="'+esc(s.status)+'">'+esc(s.status)+'</td><td>'+(s.count==null?'':s.count)+'</td><td>'+esc(s.note||'')+'</td>';
    tb.appendChild(tr);
  });
})();
</script>
</body>
</html>
"""
TEMPLATE = TEMPLATE.replace("__MAXPRICE__", f"{config.MAX_PRICE:,}")
