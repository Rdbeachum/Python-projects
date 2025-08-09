# Create a ready-to-run Python scraper that pulls product data (title, description, specs, price, images)
# from Philodo product pages (Falcon and Forester) and outputs a Shopify-ready CSV.
#
# NOTE: This script does not access the internet here, but it will when you run it on your machine.
# You can edit the PRODUCT_URLS list with the exact Falcon/Forester URLs and run the script.

script = r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Philodo → Shopify CSV Scraper
- Input: Philodo product page URLs (Falcon, Forester, etc.)
- Output: shopify_products.csv (minimally valid for import)
Tested with Python 3.10+
"""

import csv
import json
import re
import sys
import time
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ------------------------------
# CONFIG
# ------------------------------

# TODO: Replace with the exact product URLs for Philodo Falcon & Forester
PRODUCT_URLS = [
    # "https://www.philodo.com/products/falcon-60v",   # <-- example placeholder
    # "https://www.philodo.com/products/forester-60v", # <-- example placeholder
]

DEFAULT_VENDOR = "Philodo"
DEFAULT_PRODUCT_TYPE = "Sporting Goods > Outdoor Recreation > Cycling > Electric Bicycles"
DEFAULT_TAGS = ["e-bike", "dual motor", "fat tire", "60V"]
DEFAULT_CURRENCY = "USD"

# CSV output filename
OUTPUT_CSV = "shopify_products.csv"

# HTTP client configuration
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
REQUEST_TIMEOUT = 20
MAX_RETRIES = 3
RETRY_SLEEP = 1.5


# ------------------------------
# DATA MODELS
# ------------------------------

@dataclass
class ProductData:
    handle: str
    title: str
    body_html: str
    vendor: str = DEFAULT_VENDOR
    product_type: str = DEFAULT_PRODUCT_TYPE
    tags: List[str] = field(default_factory=lambda: DEFAULT_TAGS.copy())
    images: List[str] = field(default_factory=list)
    price: Optional[str] = None
    compare_at_price: Optional[str] = None
    sku: Optional[str] = None
    grams: Optional[str] = None
    weight_unit: str = "lb"
    requires_shipping: bool = True
    taxable: bool = True
    published: bool = True
    option1_name: str = "Title"
    option1_value: str = "Default Title"


# ------------------------------
# HTTP HELPERS
# ------------------------------

def get(url: str) -> Optional[requests.Response]:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return resp
            else:
                print(f"[WARN] {url} returned status {resp.status_code} (attempt {attempt}/{MAX_RETRIES})")
        except requests.RequestException as e:
            print(f"[WARN] Request error for {url}: {e} (attempt {attempt}/{MAX_RETRIES})")
        time.sleep(RETRY_SLEEP * attempt)
    print(f"[ERROR] Failed to GET {url} after {MAX_RETRIES} attempts.")
    return None


# ------------------------------
# PARSERS
# ------------------------------

def extract_json_ld(soup: BeautifulSoup) -> Dict:
    """Return combined JSON-LD blocks (if any)."""
    data = {}
    for tag in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            content = tag.string or tag.text
            if not content:
                continue
            block = json.loads(content)
            # Product pages often include a dict or a list of dicts; normalize into a list.
            blocks = block if isinstance(block, list) else [block]
            for b in blocks:
                # Merge all keys naively; prefer last in case of duplicates
                if isinstance(b, dict):
                    data.update(b)
        except Exception:
            continue
    return data


def clean_html_text(text: str) -> str:
    text = re.sub(r"\s+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_images(soup: BeautifulSoup) -> List[str]:
    """Heuristic image picker: grabs large product images from <img> tags."""
    urls = []
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-original")
        if not src:
            continue
        # Skip tiny or irrelevant images
        if any(x in src.lower() for x in ["logo", "icon", "spinner", "placeholder", "svg"]):
            continue
        # Prefer high-res if the site uses srcset
        if img.get("srcset"):
            candidates = [s.split(" ")[0] for s in img.get("srcset").split(",") if s.strip()]
            if candidates:
                src = candidates[-1]
        urls.append(src)
    # Deduplicate while preserving order
    seen = set()
    deduped = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    return deduped[:12]  # limit to 12 images per product for Shopify


def extract_specs_block(soup: BeautifulSoup) -> Optional[str]:
    """Try to capture a 'Specifications' HTML section to enrich description."""
    # Look for headings that indicate specs
    headings = soup.find_all(["h2", "h3", "h4"], string=re.compile(r"(spec|feature)", re.I))
    if not headings:
        return None
    # Take the first heading and gather sibling elements until next heading
    start = headings[0]
    parts = [str(start)]
    for sib in start.find_next_siblings():
        if sib.name in ["h2", "h3", "h4"]:
            break
        # Keep tables, lists, paragraphs, divs
        if sib.name in ["p", "ul", "ol", "table", "div"]:
            parts.append(str(sib))
        # stop if we run into a very long unrelated section
        if len("".join(parts)) > 12000:
            break
    html = "\n".join(parts)
    return html if html and len(html) > 50 else None


def parse_product(url: str) -> Optional[ProductData]:
    resp = get(url)
    if not resp:
        return None
    soup = BeautifulSoup(resp.text, "html.parser")

    # 1) Title
    title = None
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    h1 = soup.find("h1")
    if h1 and len(h1.get_text(strip=True)) > 2:
        title = h1.get_text(strip=True)
    title = title or "Philodo Electric Bike"

    # 2) JSON-LD
    json_ld = extract_json_ld(soup)

    # 3) Description
    description_html = ""
    # Prefer JSON-LD description
    desc = None
    if isinstance(json_ld, dict):
        desc = json_ld.get("description") or json_ld.get("Description")
    if not desc:
        # Try common description containers
        desc_container = soup.find(attrs={"class": re.compile(r"(product-desc|description|prose|rte)", re.I)})
        if desc_container:
            description_html = str(desc_container)
    else:
        description_html = f"<p>{desc}</p>"

    # 4) Specs section (optional enrichment)
    specs_html = extract_specs_block(soup)
    if specs_html and specs_html not in description_html:
        description_html += f"\n<h3>Specifications</h3>\n{specs_html}"

    description_html = clean_html_text(description_html) or "<p>High-performance dual-motor e-bike.</p>"

    # 5) Price (from JSON-LD or meta tags)
    price = None
    if isinstance(json_ld, dict):
        offers = json_ld.get("offers")
        if isinstance(offers, dict):
            price = offers.get("price")
        elif isinstance(offers, list) and offers:
            price = offers[0].get("price")
    if not price:
        # Try meta tags
        price_meta = soup.find("meta", {"property": "product:price:amount"}) or soup.find("meta", {"name": "price"})
        if price_meta and price_meta.get("content"):
            price = price_meta.get("content")

    # 6) SKU (from JSON-LD or page)
    sku = None
    if isinstance(json_ld, dict):
        sku = json_ld.get("sku")
        if not sku and isinstance(json_ld.get("offers"), dict):
            sku = json_ld["offers"].get("sku")

    # 7) Images
    images = extract_images(soup)

    # 8) Handle (slug)
    handle = re.sub(r"[^a-z0-9\-]+", "-", title.lower()).strip("-")
    # Special-case: keep brand in handle
    if "philodo" not in handle:
        handle = f"philodo-{handle}"

    # 9) Weight guess (can be adjusted later)
    grams = None  # leave empty; you can map later to exact shipping weight

    product = ProductData(
        handle=handle,
        title=title,
        body_html=description_html,
        vendor=DEFAULT_VENDOR,
        product_type=DEFAULT_PRODUCT_TYPE,
        tags=DEFAULT_TAGS.copy(),
        images=images,
        price=price,
        compare_at_price=None,
        sku=sku,
        grams=grams,
        weight_unit="lb",
        requires_shipping=True,
        taxable=True,
        published=True,
        option1_name="Title",
        option1_value="Default Title",
    )
    return product


# ------------------------------
# SHOPIFY CSV WRITER
# ------------------------------

CSV_HEADERS = [
    "Handle","Title","Body (HTML)","Vendor","Standard Product Type","Tags","Published",
    "Option1 Name","Option1 Value","Variant SKU","Variant Grams","Variant Inventory Tracker",
    "Variant Inventory Policy","Variant Fulfillment Service","Variant Price","Variant Compare At Price",
    "Variant Requires Shipping","Variant Taxable","Variant Weight Unit","Image Src","Image Position",
    "Image Alt Text","SEO Title","SEO Description","Google Product Category",
]

def product_to_rows(p: ProductData) -> List[List[str]]:
    """Convert a ProductData into Shopify-compatible rows (one base row + image rows)."""
    rows = []

    # Base row (first image if any)
    first_img = p.images[0] if p.images else ""
    base_row = [
        p.handle,
        p.title,
        p.body_html,
        p.vendor,
        "",  # Standard Product Type (leave blank if using Google category below)
        ",".join(p.tags),
        "TRUE" if p.published else "FALSE",
        p.option1_name,
        p.option1_value,
        p.sku or "",
        p.grams or "",
        "shopify",
        "deny",
        "manual",
        p.price or "",
        p.compare_at_price or "",
        "TRUE" if p.requires_shipping else "FALSE",
        "TRUE" if p.taxable else "FALSE",
        p.weight_unit,
        first_img,
        "1" if first_img else "",
        p.title,
        f"Buy {p.title} by {p.vendor} – dual motor e-bike, fast shipping.",
        "Sporting Goods > Outdoor Recreation > Cycling > Electric Bicycles",
    ]
    rows.append(base_row)

    # Additional image rows
    for idx, img in enumerate(p.images[1:], start=2):
        rows.append([
            p.handle, "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "",
            img, str(idx), "", "", "", "",
        ])

    return rows


def main(urls: List[str]) -> None:
    if not urls:
        print("[INFO] No URLs provided in PRODUCT_URLS. Pass URLs as CLI args or edit the script.")
        print("Usage: python philodo_to_shopify.py https://.../falcon https://.../forester")
        return

    products: List[ProductData] = []
    for url in urls:
        print(f"[INFO] Scraping: {url}")
        p = parse_product(url)
        if p:
            products.append(p)
            print(f"[OK] Parsed: {p.title} ({len(p.images)} images)")
        else:
            print(f"[FAIL] Could not parse: {url}")

    if not products:
        print("[ERROR] No products parsed. Exiting.")
        return

    # Write CSV
    with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADERS)
        for prod in products:
            for row in product_to_rows(prod):
                writer.writerow(row)

    print(f"[DONE] Wrote {OUTPUT_CSV} with {len(products)} product(s).")

if __name__ == "__main__":
    urls = PRODUCT_URLS.copy()
    if len(sys.argv) > 1:
        urls = sys.argv[1:]
    main(urls)
'''

reqs = """requests
beautifulsoup4
"""

with open("philodo_to_shopify.py", "w", encoding="utf-8") as f:
    f.write(script)

with open("requirements.txt", "w", encoding="utf-8") as f:
    f.write(reqs)

print("Created files:")
print("philodo_to_shopify.py")
print("requirements.txt")
