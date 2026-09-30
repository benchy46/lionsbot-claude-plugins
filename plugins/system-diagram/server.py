# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=1.2,<2", "pymupdf>=1.24,<2"]
# ///
"""system-diagram MCP server (stdio): turn a component datasheet into a new ECDB component (component_types, the parts
library behind the hub's System Wiring Diagram) — name, MPN, product photo, and its wires / terminals / built-in
connectors with every pin's function.

Add-only: a new row lands WITHOUT an ECDB ID (a person presses Assign ID in the ECDB) and existing rows are never touched.
Signs in as the user (their hub login), so every write is theirs and the database's own access rules apply.
Run: uv run --script server.py   (env: HUB_EMAIL, HUB_PASSWORD; optional HUB_URL, HUB_ANON_KEY)."""
import os, re, json, time, uuid, hashlib, collections, urllib.request, urllib.error
from pydantic import BaseModel, Field
import pymupdf as fitz   # not `import fitz`: that prints a deprecation warning on STDOUT = the MCP transport
from mcp.server.fastmcp import FastMCP, Image

URL = os.environ.get("HUB_URL") or "https://gateway-production-c13d.up.railway.app"
# the PUBLIC anon key: the hub ships it in its browser bundle; on its own it can read and write nothing (RLS needs a login)
ANON_KEY = os.environ.get("HUB_ANON_KEY") or "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiIsImlzcyI6InN1cGFiYXNlIiwiaWF0IjoxNzg0NzE1NTExLCJleHAiOjIxMDAwNzU1MTF9.kqtdfumqx4EjRyJ15Ld0gSZBYdeE0VnpmlnJy68qQT0"
BUCKET = "component-images"
# IEC 60757 codes + tr (Transparent) = the ecdb_assign_id whitelist (migration 20240015); '#rrggbb' is a custom colour.
COLOURS = {"bk", "bn", "rd", "or", "ye", "gn", "bu", "vt", "gy", "wh", "pk", "gd", "tq", "sr", "gnye", "tr"}
WORDS = {"black": "bk", "brown": "bn", "red": "rd", "orange": "or", "yellow": "ye", "green": "gn", "blue": "bu", "violet": "vt",
         "purple": "vt", "grey": "gy", "gray": "gy", "white": "wh", "pink": "pk", "gold": "gd", "turquoise": "tq", "silver": "sr",
         "green/yellow": "gnye", "green-yellow": "gnye", "yellow/green": "gnye", "transparent": "tr", "clear": "tr"}
MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}
mcp = FastMCP("system-diagram")


_session: dict = {}


def login() -> dict:
    """a hub access token for HUB_EMAIL / HUB_PASSWORD, signed in again shortly before it expires (tokens last an hour)."""
    if _session.get("exp", 0) > time.time() + 60: return _session
    email, pw = os.environ.get("HUB_EMAIL", "").strip(), os.environ.get("HUB_PASSWORD", "")
    if not (email and pw): raise RuntimeError("not signed in: set your hub email + password in /plugin > Installed > system-diagram > Configure options")
    req = urllib.request.Request(URL + "/auth/v1/token?grant_type=password", json.dumps({"email": email, "password": pw}).encode(),
                                 {"apikey": ANON_KEY, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r: t = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"hub sign-in failed for {email} ({e.code}): check the email + password in /plugin > Installed > system-diagram > Configure options") from None
    _session.update(token=t["access_token"], uid=t["user"]["id"], exp=time.time() + t.get("expires_in", 3600))
    return _session


def call(path: str, data: bytes | None = None, method: str = "GET", ctype: str = "application/json", extra: dict | None = None):
    req = urllib.request.Request(URL + path, data=data, method=method,
                                 headers={"apikey": ANON_KEY, "Authorization": "Bearer " + login()["token"],
                                          **({"Content-Type": ctype} if data else {}), **(extra or {})})   # storage 400s a bodyless request that names a type
    try:
        with urllib.request.urlopen(req, timeout=60) as r: body = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path.split('?')[0]} -> {e.code}: {e.read().decode(errors='replace')[:400]}") from None
    return json.loads(body) if body else None


def pages_of(spec: str, n: int) -> list[int]:
    """'1-3,7' -> [0, 1, 2, 6] (0-based, clamped to the document)."""
    out = []
    for part in filter(None, spec.replace(" ", "").split(",")):
        a, _, b = part.partition("-")
        out += range(int(a), int(b or a) + 1)
    return [p - 1 for p in dict.fromkeys(out) if 1 <= p <= n]


def xref_image(doc, xref: int) -> tuple[bytes, str]:
    """an embedded image as browser-safe bytes: PNG/JPEG as stored, anything else (CMYK, JPX, soft mask) re-encoded to PNG."""
    info = doc.extract_image(xref)
    if not info: raise ValueError(f"xref {xref} is not an image in this PDF")
    if info["ext"] in ("png", "jpeg") and info["colorspace"] in (1, 3) and not info.get("smask"):
        return info["image"], "jpg" if info["ext"] == "jpeg" else "png"
    pix = fitz.Pixmap(doc, xref)
    if pix.n - pix.alpha > 3: pix = fitz.Pixmap(fitz.csRGB, pix)
    if info.get("smask") and not pix.alpha: pix = fitz.Pixmap(pix, fitz.Pixmap(doc, info["smask"]))   # newer PyMuPDF applies the mask itself
    return pix.tobytes("png"), "png"


def load_image(spec: str, datasheet: str) -> tuple[bytes, str]:
    """image = 'xref:<n>' (embedded in the datasheet) | 'crop:<page>:x0,y0,x1,y1' (PDF points, rendered at 200 dpi) | a file path."""
    if spec.startswith(("xref:", "crop:")):
        if not os.path.isfile(datasheet): raise ValueError(f"image '{spec}' needs datasheet_path = the PDF it comes from")
        doc = fitz.open(datasheet)
        if spec.startswith("xref:"): return xref_image(doc, int(spec[5:]))
        page, _, box = spec[5:].partition(":")
        return doc[int(page) - 1].get_pixmap(dpi=200, clip=fitz.Rect(*map(float, box.split(",")))).tobytes("png"), "png"
    ext = os.path.splitext(spec)[1].lower().lstrip(".")
    if ext not in MIME: raise ValueError(f"image file must be one of {sorted(MIME)}: {spec}")
    return open(spec, "rb").read(), ext


def colour(c: str) -> str | None:
    c = c.strip().lower()
    if not c: return None
    c = WORDS.get(c, c)
    if c in COLOURS or re.fullmatch(r"#[0-9a-f]{6}", c): return c
    raise ValueError(f"unknown wire colour '{c}': use an IEC 60757 code {sorted(COLOURS)}, a colour word, or '#rrggbb'")


def id_blockers(row: dict) -> list[str]:
    """why ecdb_assign_id would refuse (mirror of migrations 20240015/20240018) — empty = a person can press Assign ID."""
    out = []
    if not row["name"].strip(): out.append("no name")
    if not row["image_url"]: out.append("no product image")
    if row["has_wires"] or not (row["has_terminals"] or row["has_connectors"]):
        if not row["pins"]: out.append("no wires")
        bad = [p["name"] or "?" for p in row["pins"] if not p["name"].strip() or not p.get("color")]
        if bad: out.append("wires missing a function or colour: " + ", ".join(bad))
    if row["has_terminals"] and any(not t["name"].strip() for t in row["terminals"]): out.append("a terminal has no name")
    if row["has_connectors"] and any(not c["name"].strip() or c["pins"] < 1 for c in row["builtin_connectors"]):
        out.append("a connector has no name or pin count")
    return out


class Wire(BaseModel):
    function: str = Field(description="what the flying lead carries, as the datasheet names it: '+24V', 'GND', 'CAN_H'")
    colour: str = Field("", description="IEC 60757 code (bk bn rd or ye gn bu vt gy wh pk gd tq sr gnye), 'tr' for transparent, a colour word, or '#rrggbb'")
    group: str = Field("", description="optional: wires that end in the same plug share a group name, e.g. 'Power' — the diagram draws a group as one fused wire")


class Connector(BaseModel):
    name: str = Field(description="connector name as printed on the part or datasheet: 'CN1', 'J3', 'Hall', 'RS232 (RJ12)'")
    pins: list[str] = Field(description="every pin's function in pin order (pin 1 first) exactly as the datasheet's pin table; '' for an unnamed pin, 'NC' for not connected. The pin count is the list length")


@mcp.tool()
def read_datasheet(pdf_path: str, pages: str = "1-8") -> list:
    """Read a component datasheet PDF (a local file path) so its pinout can be imported with create_component.
    Returns, per page: the text, a picture of the page (pinout tables and connector drawings are often images), and the
    embedded images as 'xref:<n>' with their size and position (PDF points) on the page — pass one as create_component's
    `image` for the product photo. pages = '1-3,7' (1-based); the header gives the page count, so read long datasheets in chunks."""
    if not os.path.isfile(pdf_path): raise FileNotFoundError(pdf_path)
    doc = fitz.open(pdf_path)
    idx = pages_of(pages, doc.page_count)
    out: list = [f"{os.path.basename(pdf_path)}: {doc.page_count} pages, showing {', '.join(str(i + 1) for i in idx)}"]
    for i in idx:
        page = doc[i]
        imgs = []
        for x in dict.fromkeys(im[0] for im in page.get_images()):
            info = doc.extract_image(x)
            if min(info["width"], info["height"]) < 64: continue   # logos, bullets, icons
            rects = [[round(v) for v in r] for r in page.get_image_rects(x)]
            imgs.append(f"xref:{x} {info['width']}x{info['height']}px" + (f" at {rects[0]}" if rects else " (not placed directly on the page)"))
        out.append(f"--- page {i + 1} --- images: {'; '.join(imgs) or 'none'}\n{page.get_text().strip()}")
        out.append(Image(data=page.get_pixmap(dpi=100).tobytes("jpg", jpg_quality=80), format="jpeg"))
    return out


@mcp.tool()
def create_component(name: str, category: str = "Electrical", manufacturer: str = "", mpn: str = "", model_number: str = "",
                     wires: list[Wire] = [], terminals: list[str] = [], connectors: list[Connector] = [],
                     image: str = "", datasheet_path: str = "", properties: dict[str, str] = {}, apply: bool = False) -> list:
    """Create a NEW component in the ECDB (the System Wiring Diagram's parts library) from a datasheet, WITHOUT an ECDB ID —
    a person presses Assign ID in the ECDB once they have checked it. Existing components are never modified.

    Fill every way the part connects, as its datasheet shows it (any mix):
    - wires: flying leads, each with its function + colour (+ optional group = the plug they share)
    - terminals: screw / spade / ring-lug points, one wire each, named as marked ('B+', 'L', 'PE')
    - connectors: headers / sockets on the body, each with its name and every pin's function in pin order
    image: the product photo — 'xref:<n>' or 'crop:<page>:x0,y0,x1,y1' from read_datasheet (needs datasheet_path), or an
    image file path (.png .jpg .webp). mpn = the manufacturer part number; category e.g. Motor Driver, Motor, Sensor, LED,
    Electrical; properties = short extra facts (rated voltage, source page …).

    apply=False (default) is a PREVIEW: nothing is written; it returns the row, the chosen photo, duplicates, and what
    still blocks Assign ID. Show the user the preview, then call again with apply=True to insert it."""
    if not name.strip(): raise ValueError("name is required")
    if not (wires or terminals or connectors): raise ValueError("give at least one of wires, terminals or connectors")
    for c in connectors:
        if not c.pins: raise ValueError(f"connector '{c.name}' has no pins: list each pin's function ('' if the datasheet names none)")
    cid = str(uuid.uuid4())
    rid = lambda: uuid.uuid4().hex[:8]   # same 8-char ids the ECDB editor makes
    groups = {g: rid() for g in dict.fromkeys(w.group.strip() for w in wires) if g}
    pins = [{"id": f"{cid}-p{i}", "name": w.function.strip(), "color": colour(w.colour), **({"group": groups[w.group.strip()]} if w.group.strip() else {})}
            for i, w in enumerate(wires)]
    img, ext = load_image(image, datasheet_path) if image else (b"", "")
    sha = hashlib.sha256(img).hexdigest() if img else None
    existing = call("/rest/v1/component_types?select=id,name,category,icon,electrical_id,manufacturer_part_no,image_sha256")
    icons = collections.Counter(r["icon"] for r in existing if r["category"] == category.strip() and r["icon"])
    uid = login()["uid"]   # the photo goes in the user's own folder, like an upload from the hub
    path = f"{uid}/{cid}.{ext}"
    row = {"id": cid, "owner_id": uid, "name": name.strip(), "model_number": model_number.strip(), "manufacturer": manufacturer.strip(),
           "category": category.strip() or "Electrical", "icon": icons.most_common(1)[0][0] if icons else "🔧",
           "num_pinouts": max(1, len(pins)), "pins": pins, "default_connector": "", "connectors": [], "default_properties": properties,
           "image_url": f"{URL}/storage/v1/object/public/{BUCKET}/{path}" if img else None, "image_sha256": sha,
           "has_wires": bool(wires), "group_wires": bool(groups), "wire_groups": [{"id": v, "name": k} for k, v in groups.items()],
           "has_terminals": bool(terminals), "terminals": [{"id": rid(), "name": t.strip()} for t in terminals],
           "has_connectors": bool(connectors),
           "builtin_connectors": [{"id": rid(), "name": c.name.strip(), "pins": len(c.pins), "pinNames": [p.strip() for p in c.pins]} for c in connectors],
           "manufacturer_part_no": mpn.strip() or None, "is_wire": False, "locked": False, "sort_order": 1000}
    # the DB refuses a part number or picture another row already holds (trigger component_types_no_duplicates, 23505)
    norm = lambda s: (s or "").strip().lower()
    refuse = [f"part number '{mpn}' is already {r['electrical_id'] or 'No ID'} {r['name']}" for r in existing if mpn.strip() and norm(r["manufacturer_part_no"]) == norm(mpn)]
    refuse += [f"this photo is already used by {r['electrical_id'] or 'No ID'} {r['name']}" for r in existing if sha and r["image_sha256"] == sha]
    similar = [f"{r['electrical_id'] or 'No ID'} {r['name']}" for r in existing if norm(r["name"]) == norm(name)]
    result = {"applied": False, "row": {k: v for k, v in row.items() if k not in ("owner_id", "image_sha256")},
              "idBlockers": id_blockers(row), "refused": refuse, "sameName": similar}
    if apply:
        if refuse: raise ValueError("not inserted: " + "; ".join(refuse))
        if img: call(f"/storage/v1/object/{BUCKET}/{path}", img, "POST", MIME[ext], {"x-upsert": "true"})
        try: call("/rest/v1/component_types", json.dumps(row).encode(), "POST", extra={"Prefer": "return=minimal"})
        except Exception:
            if img: call(f"/storage/v1/object/{BUCKET}/{path}", method="DELETE")   # no orphan photo when the insert is refused
            raise
        result["applied"] = True
        result["next"] = "Inserted with No ID. Open the hub > System Wiring Diagram > Component Database, check it, then press Assign ID."
    out: list = [json.dumps(result, ensure_ascii=False, indent=1)]
    if img: out.append(Image(data=img, format="jpeg" if ext == "jpg" else ext))
    return out


if __name__ == "__main__":
    mcp.run()
