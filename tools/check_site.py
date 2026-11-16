"""Check the built site in a headless browser. This is a developer check, not part of the pipeline.

What it does:
1. Serves the repo root on http://localhost:8765 with Python's http.server.
2. Opens index.html in headless Microsoft Edge (Playwright, channel "msedge").
3. Checks the landing page: the map shading, the summary, and the state list.
4. Clicks Texas on the map and records which data files the page fetched.
5. Looks up three ZIP codes given on the command line and counts the cards in each section.
6. Opens a book details dialog, applies filters, and tries the error states.
7. Checks the layout at 390 px wide: no sideways scroll.
8. Saves screenshots and prints every console error, warning, and HTTP error.

The script exits with code 1 when any check fails or any console or HTTP error shows up.

Needs: `pip install playwright` (no browser download, it uses the installed Edge).
Usage: python tools/check_site.py OUT_DIR TX_ZIP UT_ZIP MULTI_DISTRICT_ZIP
Example: python tools/check_site.py out 78217 84101 32792
"""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8765
BASE = f"http://localhost:{PORT}/index.html"
DESKTOP = {"width": 1280, "height": 900}
PHONE = {"width": 390, "height": 844}


def start_server():
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(PORT), "--bind", "127.0.0.1"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(50):  # Poll until the server answers, for at most 5 seconds.
        try:
            urllib.request.urlopen(BASE, timeout=1).close()
            break
        except OSError:
            time.sleep(0.1)
    return server


def data_requests(requests):
    """Return the data file paths the page asked for, in order."""
    return [url.split(f":{PORT}/", 1)[1] for url in requests if f":{PORT}/data/" in url]


def state_center(page, code):
    """Scroll a state's shape into view and return the screen position of its center."""
    return page.evaluate(
        f"(() => {{ const n = simplemaps_usmap.states['{code}'].node; n.scrollIntoView({{block: 'center'}});"
        f" const r = n.getBoundingClientRect(); return {{x: r.x + r.width / 2, y: r.y + r.height / 2}}; }})()"
    )


def click_state(page, code):
    """Click the center of a state's shape on the simplemaps map."""
    page.wait_for_function(f"window.simplemaps_usmap && simplemaps_usmap.loaded && simplemaps_usmap.states['{code}']")
    box = state_center(page, code)
    page.mouse.click(box["x"], box["y"])


def section_summary(page):
    """Return [(section title, books counted in the header, cards drawn)] for the results area."""
    return page.evaluate("""() => [...document.querySelectorAll('#results-body section.block')]
        .filter(s => s.querySelector('.section-body'))
        .map(s => [(s.querySelector('h2')||{}).textContent || '', (s.querySelector('.block-count')||{}).textContent || '',
                   s.querySelectorAll('.book-card').length])""")


def wait_for_covers(page):
    """Wait until every cover image near the screen has loaded or failed. Return (loaded, placeholders, pending)."""
    visible = "[...document.images].filter(i => { const r = i.getBoundingClientRect(); return r.bottom > -50 && r.top < innerHeight + 200; })"
    try:
        page.wait_for_function(f"{visible}.every(i => i.complete)", timeout=30000)
    except Exception:
        pass  # Report the pending count below instead of failing the whole check.
    page.wait_for_timeout(300)  # Let a failed image swap in its placeholder.
    return page.evaluate(f"""() => [
        {visible}.filter(i => i.complete && i.naturalWidth > 1).length,
        document.querySelectorAll('.cover-placeholder').length,
        {visible}.filter(i => !i.complete).length ]""")


def no_sideways_scroll(page):
    """True when the page is not wider than the window."""
    return page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")


def run(out_dir, zips):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"console": [], "expected_404": [], "steps": [], "failures": []}
    zip_tx, zip_ut, zip_multi = [z for _, z in zips]

    def check(condition, message):
        if not condition:
            report["failures"].append(message)

    server = start_server()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)

            def new_page(viewport=DESKTOP):
                page = browser.new_page(viewport=viewport)
                requests = []
                page.on("request", lambda r: requests.append(r.url))
                page.on("console", lambda m: m.type in ("error", "warning") and report["console"].append(
                    {"page": page.url, "type": m.type, "text": m.text}))
                page.on("response", lambda r: r.status >= 400 and report["console"].append(
                    {"page": page.url, "type": f"http {r.status}", "text": r.url}))
                page.on("pageerror", lambda e: report["console"].append({"page": page.url, "type": "pageerror", "text": str(e)}))
                return page, requests

            def search_zip(page, zip_code):
                page.fill("#zip-input", zip_code)
                page.press("#zip-input", "Enter")
                page.wait_for_selector("#results-body .book-card, #results-body .empty-note", timeout=15000)
                page.wait_for_load_state("networkidle")

            # Step 1: landing page and map shading.
            page, requests = new_page()
            page.goto(BASE, wait_until="networkidle")
            page.wait_for_function("window.simplemaps_usmap && simplemaps_usmap.loaded")
            before = data_requests(requests)
            check(set(before) == {"data/index.json", "data/books_unbanned.json"}, f"page load fetched {before}")
            fills = page.evaluate("() => new Set([...document.querySelectorAll('#map svg path')].map(p => p.getAttribute('fill')).filter(Boolean)).size")
            check(fills >= 5, f"the map shows only {fills} fill colors")
            options = page.evaluate("() => document.querySelectorAll('#state-select option').length")
            check(options > 45, f"state list has {options} options")
            check(page.evaluate("() => document.querySelectorAll('#map-legend .legend-item').length") == 6, "legend does not have 6 items")
            stats = page.evaluate("() => ['stat-events','stat-books','stat-states'].map(i => document.getElementById(i).textContent)")
            check(all(stats), f"national summary is empty: {stats}")
            page.screenshot(path=str(out_dir / "1_landing.png"), full_page=False)
            page.screenshot(path=str(out_dir / "1_landing_full.png"), full_page=True)
            # Hover a state: the tip names it and gives the count.
            click_box = state_center(page, "TX")
            page.mouse.move(click_box["x"], click_box["y"])
            page.wait_for_timeout(300)
            tip = page.evaluate("() => document.getElementById('map-tip').hidden ? '' : document.getElementById('map-tip').textContent")
            check("Texas" in tip, f"hover tip is '{tip}'")
            page.screenshot(path=str(out_dir / "1_landing_map_hover.png"), full_page=False)
            report["steps"].append({"step": "landing", "fetched_on_load": before, "map_fill_colors": fills, "state_options": options,
                                    "stats": stats, "hover_tip": tip})

            # Step 2: click Texas on the map. Only the Texas file may load.
            page.mouse.move(5, 5)
            click_state(page, "TX")
            page.wait_for_selector("#results-body .book-card", timeout=15000)
            page.wait_for_load_state("networkidle")
            after = data_requests(requests)[len(before):]
            check(after == ["data/states/TX.json"], f"clicking Texas fetched {after}")
            check(page.url.endswith("#state=TX"), f"url after click is {page.url}")
            covers = wait_for_covers(page)
            page.screenshot(path=str(out_dir / "2_state_TX.png"), full_page=False)
            top = page.evaluate("() => document.querySelectorAll('.rank-btn').length")
            check(top >= 5, f"top districts list has {top} rows")
            report["steps"].append({"step": "click TX", "covers_loaded_placeholder_pending": covers, "fetched_on_load": before,
                                    "fetched_after_click": after, "sections": section_summary(page), "top_district_rows": top})

            # Step 2b: a district click filters; the back button returns to the landing page.
            page.locator(".rank-btn").first.click()
            page.wait_for_timeout(300)
            chip = page.evaluate("() => (document.querySelector('.chip-district') || {}).textContent || ''")
            check("District:" in chip, "district click did not add a district chip")
            page.go_back()
            page.wait_for_selector("#landing:not([hidden])", timeout=5000)
            report["steps"].append({"step": "district click and back button", "chip": chip, "url_after_back": page.url})
            page.close()

            # Steps 3-5: ZIP lookups, each on a fresh page.
            for n, (label, zip_code) in enumerate(zips, start=3):
                page, requests = new_page()
                page.goto(BASE, wait_until="networkidle")
                before = len(data_requests(requests))
                search_zip(page, zip_code)
                covers = wait_for_covers(page)
                check(page.url.endswith(f"#zip={zip_code}"), f"url after ZIP search is {page.url}")
                sections = section_summary(page)
                libs = page.evaluate("() => [...document.querySelectorAll('#libraries .card')].map(c => c.textContent.trim().replace(/\\s+/g, ' '))")
                check(len(libs) == 3, f"ZIP {zip_code} shows {len(libs)} libraries")
                check(page.locator("#libraries a:has-text('Directions')").count() == 3, "libraries need 3 Directions links")
                tally = page.evaluate("() => [...document.querySelectorAll('.tally li')].map(li => li.textContent.trim().replace(/\\s+/g, ' '))")
                check(len(tally) == len(sections), "one tally line per section")
                nav = page.evaluate("() => [...document.querySelectorAll('.section-link')].map(b => b.textContent)")
                page.screenshot(path=str(out_dir / f"{n}_zip_{zip_code}.png"), full_page=False)
                if zip_code == zips[2][1]:
                    district_sections = [s for s in sections if s[0].startswith("Bans in ")]
                    check(len(district_sections) == 2, f"ZIP {zip_code} should show two district sections, got {len(district_sections)}")
                report["steps"].append({"step": f"ZIP {zip_code} ({label})", "covers_loaded_placeholder_pending": covers,
                                        "fetched": data_requests(requests)[before:], "sections": sections,
                                        "tally": tally, "section_bar": nav, "libraries": libs})

                # Book details: open the first card of the first section that has one.
                if zip_code == zips[1][1]:
                    page.locator(".book-card").first.click()
                    page.wait_for_selector("#book-dialog[open]")
                    wait_for_covers(page)
                    dialog_text = page.evaluate("() => document.getElementById('book-dialog-body').textContent.replace(/\\s+/g, ' ')")
                    check("Where it was banned" in dialog_text, "dialog lacks the events heading")
                    check(page.locator("#book-dialog-body a:has-text('WorldCat')").count() == 1, "dialog lacks the WorldCat link")
                    page.screenshot(path=str(out_dir / "6_book_details.png"), full_page=False)
                    report["steps"].append({"step": "book details", "text_start": dialog_text[:300]})
                    page.keyboard.press("Escape")
                    check(not page.evaluate("() => document.getElementById('book-dialog').open"), "Escape did not close the dialog")

                    # Statewide ban of Utah with a note, when the data has one.
                    notes = page.evaluate("() => document.querySelectorAll('.event-note').length")
                    report["steps"].append({"step": "dialog notes seen so far", "count": notes})
                page.close()

            # Step 7: filters on the Texas state view.
            page, requests = new_page()
            page.goto(BASE + "#state=TX", wait_until="networkidle")
            page.wait_for_selector("#results-body .book-card", timeout=15000)
            total_text = page.locator(".filter-count").inner_text()
            page.locator(".chip", has_text="Restricted").click()
            page.select_option("#filter-sort", "places")
            page.wait_for_timeout(300)
            filtered_text = page.locator(".filter-count").inner_text()
            check(filtered_text != total_text, "status chip did not change the count")
            page.screenshot(path=str(out_dir / "7_filtered_top.png"), full_page=False)
            page.evaluate("document.getElementById('sec-districts').scrollIntoView()")
            wait_for_covers(page)
            page.screenshot(path=str(out_dir / "7_filtered.png"), full_page=False)
            # An empty result.
            page.fill("#filter-q", "zzzzqqqq")
            page.wait_for_timeout(400)
            empty = page.evaluate("() => document.querySelectorAll('.empty-note').length")
            check(empty >= 1, "an empty search shows no message")
            # The section bar stays at the top when the page scrolls.
            page.evaluate("window.scrollTo(0, 700)")
            page.wait_for_timeout(200)
            bar_top = page.evaluate("() => Math.round(document.querySelector('.section-bar').getBoundingClientRect().top)")
            check(bar_top == 0, f"section bar is at {bar_top}px after scroll, not 0")
            report["steps"].append({"step": "filters on TX", "count_before": total_text, "count_after_status_chip": filtered_text,
                                    "empty_notes_after_nonsense_search": empty, "section_bar_top_after_scroll": bar_top})
            page.close()

            # Step 8: error and edge states, opened from a direct link.
            # The server answers 404 for a ZIP or state file that does not exist. That is expected here.
            # The check moves those entries from "console" to "expected_404" so they do not count as errors.
            console_before_edges = len(report["console"])
            page, requests = new_page()
            page.goto(BASE + "#zip=00000", wait_until="networkidle")
            page.wait_for_selector("#results-body h1", timeout=15000)
            nodata = page.locator("#results-body h1").inner_text()
            check(nodata == "We have no data for ZIP 00000", f"no-data title is '{nodata}'")
            page.screenshot(path=str(out_dir / "8_no_data_zip.png"), full_page=False)
            page.goto(BASE + "#state=NV")
            page.wait_for_selector("#results-body .panel", timeout=15000)
            nostate = page.locator("#results-body .panel h2").inner_text()
            check(nostate.startswith("No bans recorded in"), f"empty state title is '{nostate}'")
            new = report["console"][console_before_edges:]
            report["expected_404"] = [c for c in new if "404" in c["type"] + c["text"]]
            report["console"] = report["console"][:console_before_edges] + [c for c in new if c not in report["expected_404"]]
            report["steps"].append({"step": "edge states", "no_data_zip": nodata, "empty_state": nostate})
            page.close()

            # Step 8b: a failed fetch shows an error with a retry button, and the retry works.
            console_before = len(report["console"])
            page, requests = new_page()
            page.route("**/data/states/TX.json", lambda route: route.abort())
            page.goto(BASE + "#state=TX", wait_until="networkidle")
            page.wait_for_selector("#results-body [role=alert]", timeout=15000)
            failed_title = page.locator("#results-body h1").inner_text()
            check("could not load" in failed_title, f"failed-fetch title is '{failed_title}'")
            page.screenshot(path=str(out_dir / "8_failed_fetch.png"), full_page=False)
            page.unroute("**/data/states/TX.json")
            page.get_by_role("button", name="Try again").click()
            page.wait_for_selector("#results-body .book-card", timeout=15000)
            new = report["console"][console_before:]
            report["expected_failed_fetch"] = new
            report["console"] = report["console"][:console_before]
            report["steps"].append({"step": "failed fetch and retry", "title": failed_title})
            page.close()

            # Step 8c: the ?data= parameter points the page at the fixture folder.
            page, requests = new_page()
            page.goto(BASE + "?data=tests/fixtures/site_data/#zip=84101", wait_until="networkidle")
            page.wait_for_selector("#results-body .book-card", timeout=15000)
            fixture_requests = [u.split(f":{PORT}/", 1)[1] for u in requests if "tests/fixtures/site_data" in u]
            check(any(u.endswith("states/UT.json") for u in fixture_requests), f"fixture mode fetched {fixture_requests}")
            check(not data_requests(requests), f"fixture mode also fetched {data_requests(requests)}")
            report["steps"].append({"step": "?data= fixture folder", "fetched": fixture_requests})
            page.close()

            # Step 8d: a ban event with a "note" shows the note in the dialog. The check adds a note in transit.
            page, requests = new_page()

            def add_note(route):
                data = json.loads((ROOT / "data" / "states" / "UT.json").read_text(encoding="utf-8"))
                for ban in data["bans"]:
                    if ban["scope"] == "statewide":
                        ban["note"] = "Check note: triggered by 3 districts."
                route.fulfill(status=200, content_type="application/json", body=json.dumps(data))

            page.route("**/data/states/UT.json", add_note)
            page.goto(BASE + "#zip=84101", wait_until="networkidle")
            page.wait_for_selector("#results-body .book-card", timeout=15000)
            page.locator("#sec-statewide-UT .book-card").first.click()
            page.wait_for_selector("#book-dialog[open]")
            note = page.evaluate("() => (document.querySelector('.event-note') || {}).textContent || ''")
            check("Check note" in note, f"dialog note is '{note}'")
            page.close()

            # Step 8e: keyboard path. Tab from the state list reaches a state on the map and shows its tip.
            page, requests = new_page()
            page.goto(BASE, wait_until="networkidle")
            page.wait_for_function("window.simplemaps_usmap && simplemaps_usmap.loaded")
            page.focus("#state-select")
            tip = ""
            for _ in range(6):
                page.keyboard.press("Tab")
                tip = page.evaluate("() => document.getElementById('map-tip').hidden ? '' : document.getElementById('map-tip').textContent")
                if tip:
                    break
            check(bool(tip), "tabbing to the map shows no tip")
            page.screenshot(path=str(out_dir / "12_keyboard_focus.png"), full_page=False)
            page.keyboard.press("Enter")
            page.wait_for_selector("#results-body h1", timeout=15000)
            check("#state=" in page.url, f"Enter on a focused state did not change the route: {page.url}")
            report["steps"].append({"step": "keyboard path", "tip": tip, "url": page.url})
            page.close()

            # Step 9: phone layout.
            page, requests = new_page(PHONE)
            page.goto(BASE, wait_until="networkidle")
            page.wait_for_function("window.simplemaps_usmap && simplemaps_usmap.loaded")
            ok_landing = no_sideways_scroll(page)
            check(ok_landing, "landing page scrolls sideways at 390 px")
            page.screenshot(path=str(out_dir / "9_phone_landing.png"), full_page=False)
            page.screenshot(path=str(out_dir / "9_phone_landing_full.png"), full_page=True)
            search_zip(page, zips[0][1])
            wait_for_covers(page)
            ok_zip = no_sideways_scroll(page)
            check(ok_zip, "ZIP result scrolls sideways at 390 px")
            page.screenshot(path=str(out_dir / "10_phone_zip.png"), full_page=False)
            page.locator(".book-card").first.click()
            page.wait_for_selector("#book-dialog[open]")
            check(no_sideways_scroll(page), "dialog scrolls sideways at 390 px")
            page.screenshot(path=str(out_dir / "11_phone_dialog.png"), full_page=False)
            report["steps"].append({"step": "phone 390 px", "no_sideways_scroll_landing": ok_landing, "no_sideways_scroll_zip": ok_zip})
            page.close()

            browser.close()
    finally:
        server.terminate()
    report["ok"] = not report["failures"] and not report["console"]
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    out, tx, ut, multi = sys.argv[1:5]
    result = run(out, [("Texas", tx), ("Utah", ut), ("two districts", multi)])
    sys.exit(0 if result["ok"] else 1)
