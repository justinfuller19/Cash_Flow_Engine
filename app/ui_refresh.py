"""Shared presentation layer for the CashFlow Engine UI refresh."""
import re

GLOBAL_STYLES = """<style id="cfe-ui-refresh">
:root{font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#172b3a;background:#f2f7f7;font-synthesis:none;text-rendering:optimizeLegibility;color-scheme:light}
*,*::before,*::after{box-sizing:border-box}
html{min-width:320px;scroll-behavior:smooth}
body{color:#172b3a;background:radial-gradient(ellipse at top,#e5f3f0 0,#f2f7f7 480px);line-height:1.55;-webkit-font-smoothing:antialiased}
main{width:min(1180px,100%);margin-inline:auto}
h1,h2,h3,h4{color:#153b47;line-height:1.25;letter-spacing:-.025em}
h1{font-size:clamp(1.65rem,3vw,2.25rem)}h2{font-size:clamp(1.08rem,2vw,1.28rem)}
a{color:#086d75;text-underline-offset:.18em}a:hover{color:#064e55;text-decoration:underline}
button,input,select,textarea{font:inherit}
button{min-height:40px;border:1px solid transparent;border-radius:10px;padding:9px 14px;background:#0f5b63;color:white;font-weight:700;cursor:pointer;transition:background .15s ease,transform .15s ease}
button:hover{background:#103e49;box-shadow:0 3px 10px #103e4920}button:active{transform:translateY(1px)}button:disabled{opacity:.55;cursor:not-allowed}
button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:3px solid #55bdb5;outline-offset:2px}
input,select,textarea{max-width:100%;min-height:42px;border:1px solid #c5d4d8;border-radius:9px;padding:9px 11px;color:#172b3a;background:#fff}
input:hover,select:hover,textarea:hover{border-color:#8aaeb2}label{display:block;margin-bottom:5px;color:#304b58;font-size:.9rem;font-weight:700}
table{width:100%;border-collapse:separate;border-spacing:0;overflow:hidden;border-radius:10px}th{background:#eaf3f2;color:#244651;font-size:.82rem;text-align:left}th,td{padding:11px 13px;border-bottom:1px solid #dce6e8;vertical-align:top}tr:last-child td{border-bottom:0}
.card,section.card,.metric{background:#fff;border:1px solid #e3ebec;border-radius:16px;box-shadow:0 5px 20px #1b454d0a}
.card{padding:clamp(16px,2.5vw,24px)}.metrics{gap:12px}.metric{padding:16px}.metric span{color:#5b6b78;font-size:.86rem;font-weight:650}.metric strong{color:#0f5b63;font-size:clamp(1.2rem,2.4vw,1.55rem);overflow-wrap:anywhere}
.muted,.detail{color:#5b6b78}.status{border:1px solid #b9e3df;background:#e8f8f5;color:#155a5a}.status.error,.error{border-color:#f1c7c7;background:#fff0ef;color:#a82f38}
.positive{color:#176b4d}.negative{color:#a82f38}.progress{height:9px;background:#e3ecec;border-radius:99px}.fill{background:linear-gradient(90deg,#0f7779,#49a995);border-radius:inherit}.list li{border-color:#e8eeee}
.app-header{background:linear-gradient(120deg,#103e49,#0d6469);box-shadow:0 10px 30px #103e491c}.brand-subtitle{color:#d1e8e7}.nav-group{background:#ffffff12;border-color:#ffffff25}.nav-label{color:#b8ddda}.nav a{transition:background .15s ease,color .15s ease}.nav a[aria-current="page"]{background:#d6f3ed;color:#124b50}
.cfe-global-nav{display:flex;align-items:center;flex-wrap:wrap;gap:5px;margin:0 0 22px;padding:10px 12px;border:1px solid #dbe7e8;border-radius:13px;background:#ffffffdf;box-shadow:0 4px 16px #173d4409}
.cfe-global-nav .cfe-nav-brand{margin-right:auto;padding:6px 9px;color:#174b55;font-size:.93rem;font-weight:850;letter-spacing:-.02em;white-space:nowrap}
.cfe-global-nav a{display:inline-flex;align-items:center;min-height:35px;padding:7px 10px;border-radius:8px;color:#355560;font-size:.87rem;font-weight:700;text-decoration:none}
.cfe-global-nav a:hover,.cfe-global-nav a:focus-visible{background:#e5f4f1;color:#0e555d}.cfe-global-nav a[aria-current=page]{background:#d6f3ed;color:#104e53}
@media(max-width:700px){body{padding:16px 12px 34px!important}main{width:100%}.cfe-global-nav{gap:4px;padding:8px}.cfe-global-nav .cfe-nav-brand{flex-basis:100%;margin-bottom:2px}.cfe-global-nav a{padding:7px 8px;font-size:.82rem}.card{padding:16px}table{display:block;overflow-x:auto;white-space:nowrap}form{max-width:100%}}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{scroll-behavior:auto!important;transition:none!important}}
</style>"""

GLOBAL_NAV = """<nav class="cfe-global-nav" aria-label="Main navigation">
<span class="cfe-nav-brand">CashFlow Engine</span><a href="/">Overview</a><a href="/weekly">Weekly report</a><a href="/setup">Household &amp; income</a><a href="/bills">Bills &amp; debt</a><a href="/savings">Savings</a><a href="/checklist">Payday checklist</a><a href="/transactions">Transactions</a><a href="/settings-data">Settings &amp; data</a>
</nav>"""


def enhance_html(html: str, *, include_navigation: bool = True) -> str:
    """Apply shared styling and navigation without changing page data."""
    if 'id="cfe-ui-refresh"' not in html:
        if re.search(r"</head\s*>", html, re.I):
            html = re.sub(r"</head\s*>", GLOBAL_STYLES + "\n</head>", html, count=1, flags=re.I)
        else:
            # Older auth pages omit a head element; insert before their inline
            # styles, falling back to immediately after the opening html tag.
            style_match = re.search(r"<style(?:\s[^>]*)?>", html, re.I)
            html_match = re.search(r"<html(?:\s[^>]*)?>", html, re.I)
            body_match = re.search(r"<body(?:\\s[^>]*)?>", html, re.I)
            insertion = (
                style_match.start() if style_match
                else html_match.end() if html_match
                else body_match.end() if body_match
                else 0
            )
            html = html[:insertion] + GLOBAL_STYLES + "\n" + html[insertion:]
    if include_navigation and 'aria-label="Main navigation"' not in html:
        match = re.search(r"<body(?:\s[^>]*)?>", html, re.I)
        if match:
            html = html[:match.end()] + "\n" + GLOBAL_NAV + "\n" + html[match.end():]
    return html
