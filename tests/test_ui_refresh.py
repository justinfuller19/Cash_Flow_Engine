from app.ui_refresh import enhance_html


def test_enhance_html_injects_shared_styles_and_navigation():
    html = "<!doctype html><html><head><title>Page</title></head><body><main>Hello</main></body></html>"

    refreshed = enhance_html(html)

    assert 'id="cfe-ui-refresh"' in refreshed
    assert 'aria-label="Main navigation"' in refreshed
    assert 'href="/checklist"' in refreshed
    assert refreshed.index('id="cfe-ui-refresh"') < refreshed.index("</head>")
    assert refreshed.index('aria-label="Main navigation"') < refreshed.index("<main>")


def test_enhance_html_is_idempotent():
    html = "<html><head></head><body><main>Page</main></body></html>"

    refreshed = enhance_html(enhance_html(html))

    assert refreshed.count('id="cfe-ui-refresh"') == 1
    assert refreshed.count('class="cfe-global-nav"') == 1


def test_enhance_html_can_skip_navigation_for_auth_pages():
    html = "<html><head></head><body><form>Login</form></body></html>"

    refreshed = enhance_html(html, include_navigation=False)

    assert 'id="cfe-ui-refresh"' in refreshed
    assert 'class="cfe-global-nav"' not in refreshed


def test_enhance_html_does_not_duplicate_existing_main_navigation():
    html = '<html><head></head><body><nav aria-label="Main navigation">Existing</nav></body></html>'

    refreshed = enhance_html(html)

    assert refreshed.count('aria-label="Main navigation"') == 1
    assert 'aria-label="Main navigation">Existing' in refreshed


def test_enhance_html_without_head_remains_valid_text():
    html = "<body><main>Fragment</main></body>"

    assert enhance_html(html).startswith("<body>")
