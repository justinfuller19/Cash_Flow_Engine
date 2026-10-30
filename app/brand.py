"""Small, self-contained brand asset served to the browser."""

BRAND_LOGO_SVG = r'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 440" role="img" aria-labelledby="title desc">
<title id="title">Cash Flow Engine</title><desc id="desc">Ascending white and blue bars above a cyan upward swoosh, with Cash Flow Engine lettering.</desc>
<defs>
 <linearGradient id="panel" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#102f50"/><stop offset=".58" stop-color="#061d34"/><stop offset="1" stop-color="#001326"/></linearGradient>
 <linearGradient id="bars" x1="0" y1="0" x2=".25" y2="1"><stop offset="0" stop-color="#fff"/><stop offset=".52" stop-color="#d7efff"/><stop offset="1" stop-color="#58c8ff"/></linearGradient>
 <linearGradient id="cyan" x1="0" y1="0" x2="1" y2=".4"><stop offset="0" stop-color="#19a8ed"/><stop offset="1" stop-color="#42c8ff"/></linearGradient>
 <filter id="glow" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="13"/></filter>
</defs>
<rect x="16" y="12" width="568" height="416" rx="92" fill="#0b8bd1" opacity=".18" filter="url(#glow)"/>
<rect x="24" y="16" width="552" height="400" rx="86" fill="url(#panel)" stroke="#0b4167" stroke-width="2"/>
<path d="M24 292 Q26 416 145 416 H455 Q574 416 576 292" fill="none" stroke="#0788c7" stroke-width="5" opacity=".75"/>
<g fill="url(#bars)">
 <rect x="128" y="220" width="67" height="84" rx="11"/>
 <rect x="218" y="177" width="67" height="112" rx="11"/>
 <rect x="308" y="132" width="67" height="128" rx="11"/>
 <rect x="398" y="86" width="67" height="141" rx="11"/>
</g>
<path d="M82 319 C197 323 333 286 423 228 C455 207 477 180 449 160 C492 164 521 185 501 215 C464 271 318 317 196 326 C151 329 112 326 82 319Z" fill="url(#cyan)"/>
<text x="300" y="365" text-anchor="middle" font-family="Inter,Segoe UI,Arial,sans-serif" font-size="47" font-weight="700" letter-spacing="-2" fill="#fff">Cash Flow <tspan fill="#39bff7" font-weight="500">Engine</tspan></text>
</svg>'''
