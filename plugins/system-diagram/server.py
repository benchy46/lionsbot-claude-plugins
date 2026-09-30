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
import os, re, json, time, uuid, hashlib, collections, urllib.request, urllib.error, urllib.parse, html, base64, zlib, statistics
import xml.etree.ElementTree as ET
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


# ── Diagram import: a draw.io page or a Miro board → a new System Wiring Diagram project ───────────────────────────────
# Both readers hand import_graph the same thing: boxes [{id, label, x, y, w, h}] (top-left, source px) + links [{id, source, target, label}].
HEX = {"bk": "#000000", "bn": "#c2803a", "rd": "#ef4444", "or": "#f97316", "ye": "#eab308", "gn": "#22c55e", "bu": "#3b82f6", "vt": "#8b5cf6",
       "gy": "#cbd5e1", "wh": "#e2e8f0", "pk": "#f472b6", "tq": "#2dd4bf", "tr": "#f8fafc"}   # iecColours.ts; the diagram page re-derives strokes on load anyway
MIRO = os.environ.get("MIRO_URL") or "https://api.miro.com"
NODE_W = 220   # a component node on the canvas is about this big; boxes are scaled so the median box matches it on each axis
key = lambda s: re.sub(r"\s+", " ", (s or "").strip().lower())


def text(fragment: str) -> str:
    """draw.io values and Miro content are HTML fragments; keep the words."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


def read_drawio(path: str, page: int) -> tuple[list[dict], list[dict]]:
    root = ET.parse(path).getroot()
    diagrams = root.findall("diagram") if root.tag == "mxfile" else [root]
    if not 1 <= page <= len(diagrams): raise ValueError(f"page {page}: the file has {len(diagrams)} page(s)")
    d = diagrams[page - 1]
    model = d if d.tag == "mxGraphModel" else d.find("mxGraphModel")
    if model is None:   # a compressed page = base64(deflate(url-encoded xml))
        model = ET.fromstring(urllib.parse.unquote(zlib.decompress(base64.b64decode((d.text or "").strip()), -15).decode()))
    cells: dict[str, dict] = {}
    for el in model.find("root"):
        c = el if el.tag == "mxCell" else el.find("mxCell")   # <object label=.. id=..><mxCell/></object> wraps a cell that carries attributes
        if c is None: continue
        g = c.find("mxGeometry")
        num = lambda a: float(g.get(a, 0)) if g is not None else 0.0
        cells[el.get("id", "")] = dict(label=text(el.get("label") or c.get("value") or ""), parent=c.get("parent"), vertex=c.get("vertex") == "1",
                                       edge=c.get("edge") == "1", source=c.get("source"), target=c.get("target"), x=num("x"), y=num("y"), w=num("width"), h=num("height"))
    def abs_xy(c):   # a child of a group is placed relative to the group
        x, y, p = c["x"], c["y"], cells.get(c["parent"] or "")
        while p and p["vertex"]: x, y, p = x + p["x"], y + p["y"], cells.get(p["parent"] or "")
        return x, y
    groups = {c["parent"] for c in cells.values() if c["vertex"] and cells.get(c["parent"] or "", {}).get("vertex")}
    links = {cid: dict(id=cid, source=c["source"], target=c["target"], label=c["label"]) for cid, c in cells.items() if c["edge"]}
    nodes = []
    for cid, c in cells.items():
        if not c["vertex"]: continue
        if c["parent"] in links: links[c["parent"]]["label"] = (links[c["parent"]]["label"] + " " + c["label"]).strip(); continue   # an edge's label cell
        if cid in groups: continue   # a group / swimlane holding other boxes is a frame, not a part
        x, y = abs_xy(c)
        nodes.append(dict(id=cid, label=c["label"], x=x, y=y, w=c["w"], h=c["h"]))
    return nodes, list(links.values())


def miro_get(path: str, token: str) -> list[dict]:
    """every page of a cursor-paged Miro v2 list."""
    out, cursor = [], ""
    while True:
        req = urllib.request.Request(f"{MIRO}{path}?limit=50" + (f"&cursor={urllib.parse.quote(cursor)}" if cursor else ""),
                                     headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r: page = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Miro {path} -> {e.code}: {e.read().decode(errors='replace')[:300]}") from None
        out += page.get("data") or []
        cursor = page.get("cursor")
        if not cursor: return out


def read_miro(board: str) -> tuple[list[dict], list[dict]]:
    token = os.environ.get("MIRO_TOKEN", "").strip()
    if not token or token.startswith("${"): raise RuntimeError("no Miro access token: add one in /plugin > Installed > system-diagram > Configure options (a Miro developer app installed on your team, boards:read)")
    m = re.search(r"/board/([^/?#]+)", board)
    bid = urllib.parse.quote((m.group(1) if m else board).strip(), safe="")
    items = miro_get(f"/v2/boards/{bid}/items", token)
    frames = {i["id"]: i for i in items if i.get("type") == "frame"}
    nodes = []
    for i in items:
        if i.get("type") not in ("shape", "sticky_note", "text", "card", "app_card"): continue
        d, p, g = i.get("data") or {}, i.get("position") or {}, i.get("geometry") or {}
        x, y, w, h = p.get("x", 0), p.get("y", 0), g.get("width", 0), g.get("height", 0)
        f = frames.get((i.get("parent") or {}).get("id"))
        if f and p.get("relativeTo") == "parent_top_left":   # ponytail: one frame level; a frame inside a frame keeps its own offset
            fp, fg = f.get("position") or {}, f.get("geometry") or {}
            x, y = fp.get("x", 0) - fg.get("width", 0) / 2 + x, fp.get("y", 0) - fg.get("height", 0) / 2 + y
        nodes.append(dict(id=i["id"], label=text(d.get("content") or d.get("title") or ""), x=x - w / 2, y=y - h / 2, w=w, h=h))   # Miro positions are centres
    links = [dict(id=c["id"], source=(c.get("startItem") or {}).get("id"), target=(c.get("endItem") or {}).get("id"),
                  label=" ".join(text(cap.get("content", "")) for cap in c.get("captions") or []).strip())
             for c in miro_get(f"/v2/boards/{bid}/connectors", token)]
    return nodes, links


def match(label: str, nid: str, rows: list[dict], mapping: dict[str, str]) -> tuple[dict | None, str, list[str]]:
    """(component row, how, candidates) for one box. mapping (box text or id -> 'E000012' | name | 'splice' | 'skip') wins over matching."""
    by_eid = {(r.get("electrical_id") or "").upper(): r for r in rows if r.get("electrical_id")}
    m = {key(k): v.strip() for k, v in mapping.items()}
    want = m.get(key(nid)) or m.get(key(label))
    if want:
        if want.lower() in ("skip", "splice"): return None, want.lower(), []
        row = by_eid.get(want.upper()) or next((r for r in rows if key(r["name"]) == key(want)), None)
        if not row: raise ValueError(f"mapping '{label or nid}' -> '{want}': no ECDB component has that ID or name")
        return row, "mapping", []
    eid = re.search(r"\bE\d{6}\b", label, re.I)
    if eid and eid.group().upper() in by_eid: return by_eid[eid.group().upper()], "id", []
    for how, hit in (("name", lambda r: key(r["name"]) == key(label)),
                     ("part number", lambda r: key(label) in {key(r.get("manufacturer_part_no")), key(r.get("model_number"))} - {""}),
                     ("partial name", lambda r: len(key(label)) >= 4 and len(key(r["name"])) >= 4 and (key(r["name"]) in key(label) or key(label) in key(r["name"])))):
        found = [r for r in rows if hit(r)]
        if len(found) > 1 and sum(bool(r.get("electrical_id")) for r in found) == 1:   # an ID'd part beats its No-ID drafts / copies
            found, how = [r for r in found if r.get("electrical_id")], how + " (the ID'd one)"
        if len(found) == 1: return found[0], how, []
        if found: return None, "ambiguous", [f"{r.get('electrical_id') or 'No ID'} {r['name']}" for r in found[:6]]
    return None, "unmatched", []


def endpoints(row: dict) -> list[dict]:
    """a component's single-wire ends [{handle, name, colour?, conn?}] in the order its node lists them (grouped wires last)."""
    if row.get("is_wire"): return [dict(handle="l-0", name="A"), dict(handle="r-0", name="B")]
    out = []
    if row.get("has_wires") or not (row.get("has_terminals") or row.get("has_connectors")):   # wiresOn() in the hub's lib/ecdb.ts
        pins = row.get("pins") or [{} for _ in range(max(1, row.get("num_pinouts") or 1))]
        out += [dict(handle=f"l-{i}", name=(p.get("name") or "").strip() or str(i + 1), colour=p.get("color"), grouped=bool(p.get("group") and row.get("group_wires")))
                for i, p in enumerate(pins)]
    if row.get("has_terminals"): out += [dict(handle=f"l-t-{t['id']}", name=(t.get("name") or "").strip() or f"T{i + 1}") for i, t in enumerate(row.get("terminals") or [])]
    if row.get("has_connectors"):
        for c in row.get("builtin_connectors") or []:
            names = c.get("pinNames") or []
            fn = lambda k: f" {names[k - 1].strip()}" if k <= len(names) and names[k - 1].strip() else ""
            out += [dict(handle=f"l-p-{c['id']}-{k}", name=f"{c['name']}.{k}{fn(k)}", conn=c["id"]) for k in range(1, int(c.get("pins") or 0) + 1)]
    return sorted(out, key=lambda e: e.get("grouped", False))


def pick(box: dict, label: str) -> dict | None:
    """the free end a link's caption names ('GND', 'J3.4'), else the first free one."""
    free = [e for e in box["ends"] if e["handle"] not in box["used"]]
    L = key(label)
    names = lambda e: {key(e["name"]), *re.split(r"[\s,;/]+", key(e["name"]))} - {""}   # 'J3.2 GND' answers to 'J3.2', 'GND' and itself
    word = lambda t: len(t) >= 2 and re.search(rf"(?<![\w+#.-]){re.escape(t)}(?![\w+#.-])", L)
    named = [e for e in free if L and (L in names(e) or any(word(t) for t in names(e)))]
    return (named or free or [None])[0]


def ct_of(row: dict) -> dict:
    """rowToComponentType (hub lib/db.ts) in miniature; the diagram page swaps in the live one on load."""
    return {"id": row["id"], "name": row["name"], "modelNumber": row.get("model_number") or "", "manufacturer": row.get("manufacturer") or "",
            "category": row.get("category") or "Electrical", "icon": row.get("icon") or "🔧", "numPinouts": row.get("num_pinouts") or 1, "pins": row.get("pins") or [],
            "defaultConnector": "", "connectors": [], "defaultProperties": row.get("default_properties") or {}, "imageUrl": row.get("image_url"),
            "hasWires": bool(row.get("has_wires")), "groupWires": bool(row.get("group_wires")), "wireGroups": row.get("wire_groups") or [],
            "hasTerminals": bool(row.get("has_terminals")), "terminals": row.get("terminals") or [], "hasConnectors": bool(row.get("has_connectors")),
            "builtinConnectors": row.get("builtin_connectors") or [], "electricalId": row.get("electrical_id"), "manufacturerPartNo": row.get("manufacturer_part_no"),
            "isWire": bool(row.get("is_wire")), "locked": bool(row.get("locked"))}


def import_graph(nodes: list[dict], links: list[dict], title: str, mapping: dict[str, str], unmatched: str, apply: bool, source: str) -> str:
    if unmatched not in ("skip", "splice"): raise ValueError("unmatched must be 'skip' or 'splice'")
    rows = call("/rest/v1/component_types?select=*&order=name")
    degree = collections.Counter(x for l in links for x in (l["source"], l["target"]) if x)
    scale = lambda dim: min(3.0, max(0.5, NODE_W / statistics.median([n[dim] for n in nodes if n[dim] > 0]))) if any(n[dim] > 0 for n in nodes) else 1.0
    kx, ky = scale("w"), scale("h")   # a component node is ~220 px square-ish, a drawn box is usually wide and flat: scale each axis on its own
    ox, oy = min((n["x"] for n in nodes), default=0), min((n["y"] for n in nodes), default=0)
    ms = int(time.time() * 1000)
    placed: dict[str, dict] = {}   # box id -> {node, ends, used}
    boxes, out_nodes = [], []
    for i, n in enumerate(nodes):
        name = n["label"] or n["id"]
        row, how, cands = match(n["label"], n["id"], rows, mapping)
        if row is None and how != "splice" and (how == "skip" or unmatched == "skip" or not degree[n["id"]]):
            boxes.append({"box": name, "as": "skipped", "why": how, **({"candidates": cands} if cands else {})}); continue
        pos = {"x": round((n["x"] - ox) * kx + 80), "y": round((n["y"] - oy) * ky + 80)}
        if row is None:
            pairs = max(1, min(16, -(-degree[n["id"]] // 2)))   # SpliceNode: 1..16 handle pairs
            node = {"id": f"splice-{ms}-{i}", "type": "splice", "position": pos, "data": {"label": n["label"] or "Splice", "numHandles": pairs}}
            ends = [dict(handle=f"{s}-{j}", name="") for j in range(pairs) for s in "lr"]
            boxes.append({"box": name, "as": "splice", **({"why": how, "candidates": cands} if cands else {})})
        else:
            node = {"id": f"comp-{ms}-{i}", "type": "component", "position": pos,
                    "data": {"componentTypeId": row["id"], "name": row["name"], "label": n["label"] or row["name"], "pinSide": "left", "properties": {}, "componentType": ct_of(row)}}
            ends = endpoints(row)
            boxes.append({"box": name, "as": f"{row.get('electrical_id') or 'No ID'} {row['name']}", "by": how})
        placed[n["id"]] = dict(node=node, ends=ends, used=set(), name=name)
        out_nodes.append(node)
    wires, out_edges = [], []
    for i, l in enumerate(links):
        a, b = placed.get(l["source"] or ""), placed.get(l["target"] or "")
        cap = l["label"] or l["id"]
        if not (a and b) or a is b: wires.append({"link": cap, "refused": "an end is not on an imported box" if not (a and b) else "both ends on one box"}); continue
        ea, eb = pick(a, l["label"]), pick(b, l["label"])
        if not (ea and eb): wires.append({"link": cap, "refused": f"no free pin on {(a if not ea else b)['name']}"}); continue
        for box, e in ((a, ea), (b, eb)):
            box["used"].add(e["handle"])
            if e.get("conn") and e["conn"] not in box["node"]["data"].setdefault("openConnectors", []): box["node"]["data"]["openConnectors"].append(e["conn"])
        colour = ea.get("colour") or eb.get("colour") or "bk"   # a new single-core wire in the hub defaults to IEC core 1 = black
        bid = f"bundle-{ms}-{i}"   # createBundleEdges + regroup (hub lib/useWireFlow.ts, lib/wireEnds.ts) for one core
        out_edges.append({"id": f"{bid}-0", "source": a["node"]["id"], "target": b["node"]["id"], "sourceHandle": ea["handle"], "targetHandle": eb["handle"], "type": "bundled",
                          "style": {"stroke": HEX.get(colour, colour if colour.startswith("#") else "#94a3b8"), "strokeWidth": 2.5},
                          "data": {"bundleId": bid, "specs": {"numCores": 1, "crimpType": "", "crimpPinType": "", "crimpPinSize": ""}, "bundleCores": 1, "coreIndex": 0, "colour": colour,
                                   "srcSideCores": 1, "srcSideIndex": 0, "tgtSideCores": 1, "tgtSideIndex": 0, "drawSrcBlock": True, "drawTgtBlock": True, "isFirst": True}})
        wires.append({"from": f"{a['name']} · {ea['name'] or ea['handle']}", "to": f"{b['name']} · {eb['name'] or eb['handle']}", "colour": colour, **({"caption": l["label"]} if l["label"] else {})})
    result = {"applied": False, "source": source, "title": title, "boxes": boxes, "wires": wires,
              "summary": f"{len(out_nodes)} of {len(nodes)} boxes placed, {len(out_edges)} of {len(links)} links wired"}
    if apply:
        if not out_nodes: raise ValueError("nothing to import: no box matched an ECDB component (see the preview's candidates and use mapping)")
        pid = str(uuid.uuid4())
        call("/rest/v1/projects", json.dumps({"id": pid, "owner_id": login()["uid"], "title": title, "nodes": out_nodes, "edges": out_edges}).encode(), "POST", extra={"Prefer": "return=minimal"})
        result.update(applied=True, projectId=pid, next=f"Created. Open the hub > System Wiring Diagram > '{title}'. Wires without a pin caption took the first free pin in ECDB order: check them in the Wires tab. Skipped boxes can be dragged in from the library.")
    return json.dumps(result, ensure_ascii=False, indent=1)


@mcp.tool()
def import_drawio(path: str, title: str = "", page: int = 1, mapping: dict[str, str] = {}, unmatched: str = "skip", apply: bool = False) -> str:
    """Import a diagram drawn in draw.io (diagrams.net) as a NEW System Wiring Diagram project: each box becomes the ECDB component it
    names, each line between two boxes a single-core wire. path = a local .drawio / .xml file (compressed or plain); page = 1-based.
    A box matches by an ECDB ID in its text ('E000012'), else its exact component name, part number or model number, else a unique
    partial name. mapping = corrections: box text (or cell id) -> 'E000012' | a component name | 'splice' | 'skip'. unmatched = 'skip'
    (default) or 'splice' (keep an unmatched box that has lines as a splice node named after it). A line caption naming a pin function
    ('GND', 'J3.4') picks that pin; otherwise the first free pin in ECDB order.
    apply=False (default) is a PREVIEW: nothing is written; it lists every box with what it became (candidates for the unmatched ones)
    and every wire. Show it to the user, fix the mapping, then call again with apply=True to create the project (as you)."""
    if not os.path.isfile(path): raise FileNotFoundError(path)
    nodes, links = read_drawio(path, page)
    return import_graph(nodes, links, title.strip() or os.path.splitext(os.path.basename(path))[0], mapping, unmatched, apply, f"draw.io {os.path.basename(path)} page {page}")


@mcp.tool()
def import_miro(board: str, title: str = "", mapping: dict[str, str] = {}, unmatched: str = "skip", apply: bool = False) -> str:
    """Import a Miro board (its URL or board id) as a NEW System Wiring Diagram project: shapes, sticky notes, text and cards become the
    ECDB components they name, connectors become single-core wires (a caption naming a pin function picks that pin). Needs a Miro
    access token in the plugin options (from a Miro developer app installed on your team; boards:read is enough). Matching, mapping,
    unmatched and apply work exactly as in import_drawio: preview first, then apply=True."""
    nodes, links = read_miro(board)
    m = re.search(r"/board/([^/?#]+)", board)
    return import_graph(nodes, links, title.strip() or f"Miro {(m.group(1) if m else board).strip()}", mapping, unmatched, apply, f"Miro board {board.strip()}")


if __name__ == "__main__":
    mcp.run()
