"""The Content-Security-Policy blocks nothing the app itself uses."""

from playwright.sync_api import ConsoleMessage, Page, expect


def test_pages_break_no_csp_rule(admin_page: Page, live_server: str):
    violations: list[str] = []

    def record(message: ConsoleMessage) -> None:
        if "Content Security Policy" in message.text:
            violations.append(message.text)

    admin_page.on("console", record)
    for path in ("/", "/catalogue", "/account", "/admin/users", "/about", "/offline"):
        admin_page.goto(f"{live_server}{path}")
        expect(admin_page.locator("main")).to_be_visible()
    # htmx at work: a search swap, the add-book dialog and its suggestions.
    admin_page.goto(f"{live_server}/")
    with admin_page.expect_response("**/?q=*"):
        admin_page.get_by_label("Search books").fill("dune")
    admin_page.get_by_role("link", name="Add book").click()
    with admin_page.expect_response("**/publishers/suggestions*"):
        admin_page.get_by_label("Publisher", exact=True).fill("pen")

    assert violations == []
