from app.ui_refresh import enhance_html


def _markup_before_script(html: str) -> str:
    return html.split('<script id="cfe-ui-interactions">', 1)[0]


def test_enhance_html_injects_shared_styles_and_primary_navigation():
    html = "<!doctype html><html><head><title>Page</title></head><body><main>Hello</main></body></html>"
    refreshed = enhance_html(html)
    markup = _markup_before_script(refreshed)
    assert 'id="cfe-ui-refresh"' in markup
    assert 'aria-label="Main navigation"' in markup
    assert 'href="/checklist"' in markup
    assert 'href="/transactions"' in markup
    assert markup.index('id="cfe-ui-refresh"') < markup.index("</head>")


def test_enhance_html_is_idempotent():
    html = "<html><head></head><body><main>Page</main></body></html>"
    refreshed = enhance_html(enhance_html(html))
    markup = _markup_before_script(refreshed)
    assert markup.count('id="cfe-ui-refresh"') == 1
    assert refreshed.count('id="cfe-ui-interactions"') == 1
    assert markup.count('class="cfe-global-nav"') == 1


def test_enhance_html_can_skip_navigation_for_auth_pages():
    html = "<html><head></head><body><form>Login</form></body></html>"
    refreshed = enhance_html(html, include_navigation=False)
    markup = _markup_before_script(refreshed)
    assert 'id="cfe-ui-refresh"' in markup
    assert 'class="cfe-global-nav"' not in markup
    assert 'id="cfe-ui-interactions"' in refreshed


def test_enhance_html_does_not_duplicate_existing_main_navigation():
    html = '<html><head></head><body><nav aria-label="Main navigation">Existing</nav></body></html>'
    refreshed = enhance_html(html)
    markup = _markup_before_script(refreshed)
    assert markup.count('aria-label="Main navigation"') == 1
    assert 'aria-label="Main navigation">Existing' in markup


def test_enhance_html_without_head_gets_styles():
    html = "<body><main>Fragment</main></body>"
    refreshed = enhance_html(html)
    markup = _markup_before_script(refreshed)
    assert markup.startswith("<body>")
    assert 'id="cfe-ui-refresh"' in markup
    assert 'aria-label="Main navigation"' in markup


def test_auth_page_without_head_gets_styles_but_no_navigation():
    html = '<!doctype html><html lang="en"><meta charset="utf-8"><title>Login</title><style>body{color:black}</style><body><main>Login</main></body></html>'
    refreshed = enhance_html(html, include_navigation=False)
    markup = _markup_before_script(refreshed)
    assert 'id="cfe-ui-refresh"' in markup
    assert markup.index('id="cfe-ui-refresh"') < markup.index("<style>")
    assert 'class="cfe-global-nav"' not in markup


def test_home_page_gets_action_first_ui_and_cash_floor_explanation():
    html = '<html><head></head><body><nav aria-label="Main navigation">old nav</nav><form id="dashboard-cash-snapshot-form"></form><div id="metrics"></div><div id="weekly-summary"></div></body></html>'
    refreshed = enhance_html(html)
    assert 'id="cfe-ui-interactions"' in refreshed
    assert "actions.id='cfe-home-actions'" in refreshed
    assert 'Safe to spend this week' in refreshed
    assert 'there is no mandatory household cash floor' in refreshed.lower()
    assert '/api/dashboard' not in refreshed
    assert 'free_to_spend_cents' not in refreshed


def test_dashboard_metric_labels_are_pinned_to_the_script_hooks():
    # The script currently discovers these metrics by their existing labels.
    # Keep this regression test so a dashboard label change is noticed during review.
    from app.ui_refresh import INTERACTION_SCRIPT

    assert "includes('safe to spend')" in INTERACTION_SCRIPT
    assert "includes('actions needing attention')" in INTERACTION_SCRIPT
    assert "Safe to spend this week" in INTERACTION_SCRIPT
    assert "Checklist actions left" in INTERACTION_SCRIPT
    assert "Projected cash shortfall" in INTERACTION_SCRIPT


def test_future_forecast_gap_has_a_visible_warning_and_forecast_link():
    from app.ui_refresh import GLOBAL_STYLES, INTERACTION_SCRIPT

    assert 'cfe-forecast-warning' in GLOBAL_STYLES
    assert "id='cfe-forecast-warning'" in INTERACTION_SCRIPT
    assert "Future cash-flow gap detected" in INTERACTION_SCRIPT
    assert "href='/weekly'" in INTERACTION_SCRIPT
    assert "numeric<=0" in INTERACTION_SCRIPT


def test_global_navigation_avoids_duplicate_brand_and_account_actions():
    from app.ui_refresh import GLOBAL_NAV, INTERACTION_SCRIPT

    assert 'CashFlow Engine</span>' not in GLOBAL_NAV
    assert 'cfe-nav-brand' not in GLOBAL_NAV
    more_menu = GLOBAL_NAV.split('class="cfe-more-menu">', 1)[1]
    assert 'href="/account">Account settings</a>' in more_menu
    assert 'href="/logout">Log out</a>' in more_menu
    assert 'Account settings' not in GLOBAL_NAV.split('class="cfe-more-menu">', 1)[0]
    assert 'Log out' not in GLOBAL_NAV.split('class="cfe-more-menu">', 1)[0]
    assert 'CashFlow Engine</span>' not in INTERACTION_SCRIPT


def test_enhance_html_keeps_top_title_but_removes_duplicate_account_header_links():
    html = '''<html><head></head><body><header class="app-header"><h1 class="brand">CashFlow Engine</h1><div class="account-links"><a href="/account">Account settings</a><a href="/logout">Log out</a></div></header><main>Page</main></body></html>'''
    refreshed = enhance_html(html)
    markup = _markup_before_script(refreshed)

    assert '<h1 class="brand">CashFlow Engine</h1>' in markup
    assert 'class="account-links"' not in markup
    assert 'href="/account">Account settings</a>' in markup
    assert 'href="/logout">Log out</a>' in markup
    nav = markup.split('aria-label="Main navigation"', 1)[1].split('</nav>', 1)[0]
    assert 'CashFlow Engine' not in nav
    assert nav.index('class="cfe-more-menu"') < nav.index('Account settings')
    assert nav.index('class="cfe-more-menu"') < nav.index('Log out')


def test_shared_styles_include_responsive_brand_logo():
    from app.ui_refresh import GLOBAL_STYLES

    assert ".cfe-brand-lockup" in GLOBAL_STYLES
    assert "max-height:124px" in GLOBAL_STYLES
