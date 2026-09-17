"""Renders static/img/og-image.jpg (1200x630), the picture shown when a Corect.uk link is shared.

Run after changing the hero picture, the logo or the tagline:
    python -m qa.tools.make_og_image
Needs the development requirements (Playwright with Chromium).
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
IMAGES = ROOT / "static" / "img"
OUTPUT = IMAGES / "og-image.jpg"

PAGE = """<!doctype html><html lang="ro"><head><meta charset="utf-8"><style>
  html, body {{ margin: 0; width: 1200px; height: 630px; overflow: hidden; }}
  body {{ font-family: Inter, "Segoe UI", Arial, sans-serif; background: #f4f8ff; position: relative; }}
  .art {{ position: absolute; inset: 0 0 0 420px; background: url('{hero}') center / cover no-repeat; }}
  .fade {{ position: absolute; inset: 0; background: linear-gradient(90deg, #f4f8ff 38%, rgba(244,248,255,.85) 52%,
           rgba(244,248,255,0) 78%); }}
  .copy {{ position: absolute; left: 72px; top: 96px; width: 640px; }}
  .brand {{ display: flex; align-items: center; gap: 20px; font-size: 46px; font-weight: 800; color: #0b2a5b; }}
  .brand img {{ width: 84px; height: 84px; }}
  h1 {{ margin: 56px 0 0; font-size: 64px; line-height: 1.08; color: #0b2a5b; letter-spacing: -1px; }}
  p {{ margin: 28px 0 0; font-size: 32px; line-height: 1.35; color: #1f3f73; }}
  .bar {{ position: absolute; left: 0; right: 0; bottom: 0; height: 14px; background: #287dff; }}
</style></head><body>
  <div class="art"></div><div class="fade"></div>
  <div class="copy">
    <div class="brand"><img src="{logo}" alt="">corect.uk</div>
    <h1>Engleză britanică naturală</h1>
    <p>din română sau engleză, cu greșelile explicate simplu în română</p>
  </div>
  <div class="bar"></div>
</body></html>"""


def main():
    html = PAGE.format(hero=(IMAGES / "hero.webp").as_uri(), logo=(IMAGES / "logo.svg").as_uri())
    page_file = OUTPUT.with_suffix(".html")
    page_file.write_text(html, encoding="utf-8")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1200, "height": 630})
            page.goto(page_file.as_uri())
            page.wait_for_load_state("networkidle")
            page.screenshot(path=str(OUTPUT), type="jpeg", quality=88)
            browser.close()
    finally:
        page_file.unlink(missing_ok=True)
    print(f"Wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
