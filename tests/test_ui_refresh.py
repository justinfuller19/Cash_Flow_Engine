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


def test_home_page_gets_action_first_ui_and_buffer_explanation():
    html = '<html><head></head><body><nav aria-label="Main navigation">old nav</nav><form id="dashboard-cash-snapshot-form"></form><div id="metrics"></div><div id="weekly-summary"></div></body></html>'
    refreshed = enhance_html(html)
    assert 'id="cfe-ui-interactions"' in refreshed
    assert "actions.id='cfe-home-actions'" in refreshed
    assert 'Safe to spend this week' in refreshed
    assert 'household buffer' in refreshed.lower()
    assert '/api/dashboard' not in refreshed
    assert 'free_to_spend_cents' not in refreshed
