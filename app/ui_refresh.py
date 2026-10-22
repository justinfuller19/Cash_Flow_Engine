"""Shared presentation layer for the CashFlow Engine UI."""
import re

GLOBAL_STYLES = """<style id="cfe-ui-refresh">
:root{font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;color:#173642;background:#f3f7f7;color-scheme:light;--ink:#173642;--muted:#5c707a;--line:#dce7e8;--card:#fff}
*,*::before,*::after{box-sizing:border-box}html{min-width:320px;scroll-behavior:smooth}body{margin:0!important;color:var(--ink);background:radial-gradient(ellipse at top,#e6f3f1,#f3f7f7 500px);line-height:1.55}main{width:min(1180px,100%);margin-inline:auto}
h1,h2,h3,h4{color:#153b47;line-height:1.25;letter-spacing:-.025em}h1{font-size:clamp(1.65rem,3vw,2.25rem)}h2{font-size:clamp(1.08rem,2vw,1.28rem)}a{color:#086d75;text-underline-offset:.18em}a:hover{color:#064e55}
button,input,select,textarea{font:inherit}button{min-height:42px;border:1px solid transparent;border-radius:10px;padding:9px 14px;background:#0f6269;color:white;font-weight:750;cursor:pointer}button:hover{background:#103e49}button:disabled{opacity:.55;cursor:not-allowed}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:3px solid #55bdb5;outline-offset:2px}
input,select,textarea{max-width:100%;min-height:44px;border:1px solid #c5d4d8;border-radius:9px;padding:9px 11px;color:#172b3a;background:white}label{display:block;margin-bottom:5px;color:#304b58;font-size:.9rem;font-weight:700}
table{width:100%;border-collapse:separate;border-spacing:0;overflow:hidden;border-radius:10px}th{background:#eaf3f2;color:#244651;font-size:.82rem;text-align:left}th,td{padding:11px 13px;border-bottom:1px solid var(--line);vertical-align:top}
.card,section.card,.metric{background:var(--card);border:1px solid #e0e9ea;border-radius:16px;box-shadow:0 5px 20px #1b454d0a}.card{padding:clamp(16px,2.5vw,24px);min-width:0}.grid{gap:16px}.metrics{gap:12px}.metric{padding:16px;min-width:0}.metric span{display:block;color:var(--muted);font-size:.86rem;font-weight:650}.metric strong{display:block;color:#0f6269;font-size:clamp(1.2rem,2.4vw,1.55rem);overflow-wrap:anywhere;margin-top:4px}
.muted,.detail,.item-detail{color:var(--muted)}.status{border:1px solid #b9e3df;background:#e8f8f5;color:#155a5a;border-radius:10px}.status.error,.error{border-color:#f1c7c7;background:#fff0ef;color:#a82f38}
.progress{height:9px;background:#e3ecec;border-radius:99px;overflow:hidden}.fill{height:100%;background:linear-gradient(90deg,#0f7779,#49a995);border-radius:inherit}
.cfe-global-nav{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin:0 0 22px;padding:10px 12px;border:1px solid #dbe7e8;border-radius:14px;background:#ffffffeb;box-shadow:0 4px 16px #173d4409}.cfe-nav-brand{margin-right:auto;padding:6px 9px;color:#174b55;font-size:.92rem;font-weight:850;white-space:nowrap}
.cfe-global-nav>a,.cfe-more summary{display:inline-flex;align-items:center;justify-content:center;min-height:40px;padding:8px 11px;border:0;border-radius:9px;background:transparent;color:#355560;font-size:.88rem;font-weight:750;text-decoration:none;cursor:pointer;list-style:none}.cfe-global-nav>a:hover,.cfe-more summary:hover,.cfe-global-nav>a[aria-current=page]{background:#dff2ef;color:#104e53}
.cfe-more{position:relative}.cfe-more summary::-webkit-details-marker{display:none}.cfe-more summary:after{content:"⌄";margin-left:7px}.cfe-more[open] .cfe-more-menu{position:absolute;z-index:20;right:0;top:calc(100% + 7px);display:grid;gap:3px;min-width:220px;padding:8px;border:1px solid var(--line);border-radius:12px;background:white;box-shadow:0 12px 30px #173d4420}.cfe-more-menu a{display:block;padding:9px 10px;border-radius:8px;color:#355560;text-decoration:none;font-size:.9rem}.cfe-more-menu a:hover,.cfe-more-menu a[aria-current=page]{background:#e5f4f1;color:#0e555d}
.cfe-home-intro{margin:0 0 16px;color:var(--muted)}#metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));align-items:stretch;margin:16px 0 20px}#metrics .cfe-safe-hero{grid-column:span 2;grid-row:span 2;padding:clamp(20px,3vw,30px);background:linear-gradient(135deg,#e1f2e9,#f4fbf7);border-color:#b8dfc9;display:flex;flex-direction:column;justify-content:center}#metrics .cfe-safe-hero span{color:#176747;font-size:.82rem;letter-spacing:.055em;text-transform:uppercase}#metrics .cfe-safe-hero strong{color:#145a3b;font-size:clamp(2rem,4vw,3rem);line-height:1.15}
#metrics .cfe-attention-metric{background:#fff8e9;border-color:#efd9a9}#metrics .cfe-attention-metric strong{color:#885b13}#cfe-at-a-glance{margin:-4px 0 20px;border:1px solid var(--line);border-radius:12px;background:#ffffffc9}#cfe-at-a-glance summary{padding:12px 15px;color:#355560;font-weight:750;cursor:pointer}#cfe-at-a-glance .metrics{padding:0 12px 12px;margin:0;grid-template-columns:repeat(auto-fit,minmax(160px,1fr))}
#cfe-home-actions{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:0 0 20px}.cfe-action-card{display:flex;align-items:center;gap:12px;min-width:0;padding:15px;border:1px solid var(--line);border-radius:14px;background:white;text-decoration:none!important}.cfe-action-icon{display:grid;place-items:center;flex:0 0 40px;height:40px;border-radius:12px;background:#e4f2ef;color:#155b60;font-size:1.1rem;font-weight:850}.cfe-action-card strong{display:block;color:#173642;font-size:.94rem}.cfe-action-card small{display:block;color:var(--muted);font-size:.82rem}
.cfe-explanation{margin-top:12px;padding:12px;border:1px solid #dce9e7;border-radius:10px;background:#f7fbfa;color:#49636b;font-size:.9rem}#dashboard-cash-snapshot-status.cfe-needs-attention{display:block;padding:10px 12px;border-radius:9px;background:#fff4e5;color:#85500b}
@media(max-width:850px){#metrics{grid-template-columns:repeat(2,minmax(0,1fr))}#cfe-home-actions{grid-template-columns:repeat(2,minmax(0,1fr))}.cfe-nav-brand{flex-basis:100%}}
@media(max-width:600px){body{padding:12px 12px 86px!important}main{width:100%}.cfe-global-nav{position:fixed;z-index:50;left:8px;right:8px;bottom:8px;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:2px;margin:0;padding:7px;border:1px solid #cddfdf;border-radius:16px;background:#fffffff5;box-shadow:0 8px 32px #173d4430}.cfe-nav-brand{display:none}.cfe-global-nav>a,.cfe-more summary{min-width:0;min-height:46px;padding:7px 2px;font-size:.75rem;text-align:center}.cfe-more summary:after{display:none}.cfe-more[open] .cfe-more-menu{position:fixed;right:8px;bottom:68px;top:auto;max-height:60vh;overflow:auto;min-width:min(260px,calc(100vw - 16px))}.card{padding:16px}#metrics{gap:8px}#metrics .cfe-safe-hero{grid-column:1/-1;grid-row:auto;padding:21px}#metrics .cfe-safe-hero strong{font-size:2.4rem}#cfe-at-a-glance .metrics{grid-template-columns:repeat(2,minmax(0,1fr))}#cfe-home-actions{grid-template-columns:1fr;gap:8px}table{display:block;overflow-x:auto;white-space:nowrap}.grid{grid-template-columns:minmax(0,1fr)!important}}
@media(prefers-color-scheme:dark){:root{color-scheme:dark;--ink:#e3eff2;--muted:#a8bdc5;--line:#314a53;--card:#172d35;background:#102128}body{background:radial-gradient(ellipse at top,#173b40,#102128 500px);color:var(--ink)}h1,h2,h3,h4{color:#d9f0f0}.card,section.card,.metric,#cfe-at-a-glance{background:#172d35;border-color:#314a53}.metric strong{color:#8bd8ce}input,select,textarea{background:#102128;color:#e3eff2;border-color:#46616a}.cfe-global-nav{background:#142a32f5;border-color:#314a53}.cfe-nav-brand,.cfe-global-nav>a,.cfe-more summary,.cfe-more-menu a{color:#d4e8eb}.cfe-more[open] .cfe-more-menu{background:#172d35}.cfe-action-card{background:#172d35;border-color:#314a53}.cfe-action-card strong{color:#e3eff2}#metrics .cfe-safe-hero{background:linear-gradient(135deg,#183c32,#172d35);border-color:#326d59}#metrics .cfe-safe-hero span,#metrics .cfe-safe-hero strong{color:#a5e4c0}#metrics .cfe-attention-metric{background:#382e1c;border-color:#745c2e}#metrics .cfe-attention-metric strong{color:#f4cf82}.cfe-explanation{background:#142a32;border-color:#314a53;color:#c0d5d9}th{background:#233d45;color:#d8eaec}td,th{border-color:#314a53}.status{background:#163a37;color:#bce9df}.status.error,.error{background:#422526;color:#ffc8c8}}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{scroll-behavior:auto!important;transition:none!important}}
</style>"""

GLOBAL_NAV = """<nav class="cfe-global-nav" aria-label="Main navigation"><span class="cfe-nav-brand">CashFlow Engine</span><a href="/">Home</a><a href="/checklist">Checklist</a><a href="/transactions">Activity</a><details class="cfe-more"><summary>More</summary><div class="cfe-more-menu"><a href="/weekly">Weekly report &amp; allocate</a><a href="/setup">Household &amp; income</a><a href="/bills">Bills &amp; debt</a><a href="/savings">Savings goals</a><a href="/settings-data">Settings &amp; data</a><a href="/account">Account settings</a><a href="/logout">Log out</a></div></details></nav>"""

INTERACTION_SCRIPT = """<script id="cfe-ui-interactions">
(function(){
 const path=window.location.pathname;
 function markCurrent(){document.querySelectorAll('.cfe-global-nav a,.cfe-more-menu a').forEach(a=>{if(a.getAttribute('href')===path)a.setAttribute('aria-current','page');});}
 markCurrent();
 const home=!!document.getElementById('dashboard-cash-snapshot-form');
 if(!home)return;
 const intro=document.querySelector('main>p.muted');
 if(intro){intro.classList.add('cfe-home-intro');intro.textContent='Your household money at a glance. Start with what is safe to spend, then take care of anything that needs attention.';}
 const nav=document.querySelector('nav[aria-label="Main navigation"]');
 if(nav&&!nav.classList.contains('cfe-global-nav')){
  const replacement=document.createElement('nav');replacement.className='cfe-global-nav';replacement.setAttribute('aria-label','Main navigation');
  replacement.innerHTML='<span class="cfe-nav-brand">CashFlow Engine</span><a href="/">Home</a><a href="/checklist">Checklist</a><a href="/transactions">Activity</a><details class="cfe-more"><summary>More</summary><div class="cfe-more-menu"><a href="/weekly">Weekly report &amp; allocate</a><a href="/setup">Household &amp; income</a><a href="/bills">Bills &amp; debt</a><a href="/savings">Savings goals</a><a href="/settings-data">Settings &amp; data</a><a href="/account">Account settings</a><a href="/logout">Log out</a></div></details>';
  nav.replaceWith(replacement);markCurrent();
 }
 const metrics=document.getElementById('metrics');let arranging=false;
 function arrangeMetrics(){
  if(!metrics||arranging)return;arranging=true;const observer=window.__cfeMetricsObserver;if(observer)observer.disconnect();
  const old=document.getElementById('cfe-at-a-glance');if(old)old.remove();const cards=Array.from(metrics.querySelectorAll('.metric'));
  if(cards.length){
   const safe=cards.find(c=>(c.querySelector('span')?.textContent||'').toLowerCase().includes('safe to spend'));
   const attention=cards.find(c=>(c.querySelector('span')?.textContent||'').toLowerCase().includes('actions needing attention'));
   if(safe){safe.classList.add('cfe-safe-hero');const label=safe.querySelector('span');if(label)label.textContent='Safe to spend this week';metrics.prepend(safe);}
   if(attention){attention.classList.add('cfe-attention-metric');const label=attention.querySelector('span');if(label)label.textContent='Checklist actions left';if(safe)safe.after(attention);else metrics.prepend(attention);}
   const secondary=Array.from(metrics.querySelectorAll('.metric')).filter(c=>c!==safe&&c!==attention);
   if(secondary.length){const details=document.createElement('details');details.id='cfe-at-a-glance';const summary=document.createElement('summary');summary.textContent='At a glance · household and monthly totals';details.appendChild(summary);const grid=document.createElement('div');grid.className='metrics';
    secondary.forEach(card=>{const label=card.querySelector('span');if(label){if(label.textContent.trim()==='Household cash buffer')label.textContent='Household buffer target';if(label.textContent.trim()==='Forecast buffer gap')label.textContent='Future buffer replenishment';}grid.appendChild(card);});
    details.appendChild(grid);metrics.after(details);
   }
  }
  if(observer)observer.observe(metrics,{childList:true,subtree:true});arranging=false;
 }
 if(metrics){const observer=new MutationObserver(()=>arrangeMetrics());window.__cfeMetricsObserver=observer;observer.observe(metrics,{childList:true,subtree:true});if(metrics.children.length)arrangeMetrics();}
 const actions=document.createElement('div');actions.id='cfe-home-actions';actions.setAttribute('aria-label','Quick actions');
 actions.innerHTML='<a class="cfe-action-card" href="/checklist"><span class="cfe-action-icon" aria-hidden="true">✓</span><span><strong>This week’s checklist</strong><small>Review bills and money tasks</small></span></a><a class="cfe-action-card" href="/transactions"><span class="cfe-action-icon" aria-hidden="true">＋</span><span><strong>Record money in or out</strong><small>Add or review a transaction</small></span></a><a class="cfe-action-card" href="/weekly"><span class="cfe-action-icon" aria-hidden="true">↗</span><span><strong>Review the forecast</strong><small>See future cash needs and choices</small></span></a>';
 const status=document.getElementById('status');if(status)status.after(actions);
 const cashStatus=document.getElementById('dashboard-cash-snapshot-status');
 function updateCashStatus(){if(cashStatus){const t=(cashStatus.textContent||'').toLowerCase();cashStatus.classList.toggle('cfe-needs-attention',t.includes('out of date')||t.includes('no cash snapshot'));}}
 if(cashStatus){new MutationObserver(updateCashStatus).observe(cashStatus,{childList:true,characterData:true,subtree:true});updateCashStatus();}
 const weekly=document.getElementById('weekly-summary');
 function addExplanation(){if(!weekly||weekly.querySelector('.cfe-explanation'))return;const details=document.createElement('details');details.className='cfe-explanation';const summary=document.createElement('summary');summary.textContent='How is safe-to-spend calculated?';details.appendChild(summary);const p=document.createElement('p');p.textContent='This amount comes from CashFlow Engine’s existing forecast. It considers the confirmed cash snapshot, recorded transactions, expected income, upcoming obligations, planned savings and allocations, and protection for the household buffer across future weeks. The buffer is a forecast-protection rule, not an extra bill to subtract a second time. This explanation does not change the calculation.';details.appendChild(p);weekly.appendChild(details);}
 if(weekly){new MutationObserver(addExplanation).observe(weekly,{childList:true});addExplanation();}
})();
</script>"""

def enhance_html(html: str, *, include_navigation: bool = True) -> str:
    """Add shared styling/navigation without changing page data or calculations."""
    if 'id="cfe-ui-refresh"' not in html:
        if re.search(r"</head\s*>", html, re.I):
            html = re.sub(r"</head\s*>", GLOBAL_STYLES + "\n</head>", html, count=1, flags=re.I)
        else:
            style_match = re.search(r"<style(?:\s[^>]*)?>", html, re.I)
            html_match = re.search(r"<html(?:\s[^>]*)?>", html, re.I)
            body_match = re.search(r"<body[^>]*>", html, re.I)
            insertion = style_match.start() if style_match else html_match.end() if html_match else body_match.end() if body_match else 0
            html = html[:insertion] + GLOBAL_STYLES + "\n" + html[insertion:]
    is_home = 'id="dashboard-cash-snapshot-form"' in html
    if not is_home and include_navigation and 'aria-label="Main navigation"' not in html:
        match = re.search(r"<body(?:\s[^>]*)?>", html, re.I)
        if match:
            html = html[:match.end()] + "\n" + GLOBAL_NAV + "\n" + html[match.end():]
    body_close = re.search(r"</body\s*>", html, re.I)
    if body_close and 'id="cfe-ui-interactions"' not in html:
        html = html[:body_close.start()] + INTERACTION_SCRIPT + "\n" + html[body_close.start():]
    return html
