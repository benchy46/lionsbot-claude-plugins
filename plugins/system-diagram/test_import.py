"""Self-check for the draw.io / Miro import (no network: the ECDB rows and the Miro API are stubbed).
Run from this folder:  uv run --no-project --with "mcp>=1.2,<2" --with pymupdf python test_import.py"""
import base64, json, os, tempfile, urllib.parse, zlib
import server

ROWS = [
    {"id": "ct-motor", "name": "Brush Motor", "electrical_id": "E000012", "category": "Motor", "icon": "M", "num_pinouts": 2, "has_wires": True,
     "pins": [{"id": "p0", "name": "+24V", "color": "rd"}, {"id": "p1", "name": "GND", "color": "bk"}]},
    {"id": "ct-drv", "name": "ZLAC8015D", "electrical_id": "E000040", "category": "Motor Driver", "icon": "D", "num_pinouts": 1, "has_connectors": True,
     "pins": [{"id": "x", "name": "1"}], "builtin_connectors": [{"id": "c1", "name": "J3", "pins": 2, "pinNames": ["EN", "GND"]}]},
    {"id": "ct-psu-a", "name": "PSU", "category": "Electrical", "icon": "P", "num_pinouts": 1, "pins": [{"id": "a", "name": "V+"}]},
    {"id": "ct-psu-b", "name": "PSU", "category": "Electrical", "icon": "P", "num_pinouts": 1, "pins": [{"id": "b", "name": "V+"}]},
    {"id": "ct-drv-copy", "name": "ZLAC8015D", "electrical_id": None, "category": "Motor Driver", "icon": "D", "num_pinouts": 1, "pins": [{"id": "x", "name": "1"}]},
    {"id": "ct-cable", "name": "USB cable", "electrical_id": "E000050", "category": "OEM Cable", "icon": "C", "num_pinouts": 0, "is_wire": True, "pins": []},
]
calls = []
server.call = lambda path, data=None, method="GET", ctype="", extra=None: (calls.append((method, path, data)) or (ROWS if method == "GET" else None))
server.login = lambda: {"uid": "user-1", "token": "t"}

XML = """<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>
<mxCell id="g" value="Drive" style="group" vertex="1" parent="1"><mxGeometry x="100" y="100" width="300" height="200"/></mxCell>
<mxCell id="m" value="&lt;b&gt;Brush motor&lt;/b&gt; E000012" vertex="1" parent="g"><mxGeometry x="20" y="30" width="120" height="60"/></mxCell>
<object label="ZLAC8015D" id="d"><mxCell vertex="1" parent="1"><mxGeometry x="500" y="100" width="120" height="60"/></mxCell></object>
<mxCell id="p" value="PSU" vertex="1" parent="1"><mxGeometry x="500" y="300" width="120" height="60"/></mxCell>
<mxCell id="t" value="Robot v2 (title)" style="text;" vertex="1" parent="1"><mxGeometry x="0" y="0" width="200" height="30"/></mxCell>
<mxCell id="j" value="Junction" vertex="1" parent="1"><mxGeometry x="300" y="300" width="60" height="60"/></mxCell>
<mxCell id="e1" edge="1" source="m" target="d" parent="1"><mxGeometry relative="1" as="geometry"/></mxCell>
<mxCell id="e1l" value="GND" style="edgeLabel" vertex="1" connectable="0" parent="e1"><mxGeometry relative="1" as="geometry"/></mxCell>
<mxCell id="e2" edge="1" source="m" target="j" parent="1"/>
<mxCell id="e3" edge="1" source="j" target="p" parent="1"/>
<mxCell id="e4" edge="1" source="d" parent="1"/>
</root></mxGraphModel>"""


def drawio_file(compressed: bool) -> str:
    body = XML if not compressed else base64.b64encode(zlib.compress(urllib.parse.quote(XML, safe="").encode())[2:-4]).decode()
    fd, path = tempfile.mkstemp(suffix=".drawio"); os.close(fd)
    with open(path, "w", encoding="utf-8") as f: f.write(f'<mxfile><diagram id="a" name="Page-1">{body}</diagram></mxfile>')
    return path


for compressed in (False, True):
    nodes, links = server.read_drawio(drawio_file(compressed), 1)
    byid = {n["id"]: n for n in nodes}
    assert set(byid) == {"m", "d", "p", "t", "j"}, byid.keys()          # the group and the edge label are not boxes
    assert byid["m"]["label"] == "Brush motor E000012" and (byid["m"]["x"], byid["m"]["y"]) == (120, 130)   # HTML stripped, group offset applied
    assert byid["d"]["label"] == "ZLAC8015D"                              # <object label> wrapper
    assert {l["id"]: l["label"] for l in links}["e1"] == "GND"            # edge label cell folded into its edge
    assert [l for l in links if l["id"] == "e4"][0]["target"] is None     # dangling

# preview: skip unmatched (default)
out = json.loads(server.import_graph(nodes, links, "T", {}, "skip", False, "test"))
b = {x["box"]: x for x in out["boxes"]}
assert b["Brush motor E000012"]["as"] == "E000012 Brush Motor" and b["Brush motor E000012"]["by"] == "id"
assert b["ZLAC8015D"]["as"] == "E000040 ZLAC8015D" and b["ZLAC8015D"]["by"] == "name (the ID'd one)", b["ZLAC8015D"]   # the No-ID copy loses
assert b["PSU"]["as"] == "skipped" and b["PSU"]["why"] == "ambiguous" and len(b["PSU"]["candidates"]) == 2
assert b["Robot v2 (title)"]["as"] == "skipped" and b["Junction"]["as"] == "skipped"
w = out["wires"]
assert w[0]["from"] == "Brush motor E000012 · GND" and w[0]["to"] == "ZLAC8015D · J3.2 GND" and w[0]["colour"] == "bk", w[0]   # caption picks the pin on both ends
assert w[1]["refused"] and w[2]["refused"] and w[3]["refused"], w
assert not calls or all(c[0] == "GET" for c in calls)

# apply: splice for unmatched + mapping fixes PSU
calls.clear()
try:
    server.import_graph(nodes, links, "T", {"PSU": "nope"}, "splice", False, "test"); assert False
except ValueError as e: assert "no ECDB component" in str(e)
out = json.loads(server.import_graph(nodes, links, "T", {"PSU": "E000050", "t": "skip"}, "splice", True, "test"))
assert out["applied"] and out["summary"] == "4 of 5 boxes placed, 3 of 4 links wired", out["summary"]
b = {x["box"]: x for x in out["boxes"]}
assert b["Junction"]["as"] == "splice" and b["PSU"]["as"] == "E000050 USB cable" and b["Robot v2 (title)"]["as"] == "skipped"
post = [c for c in calls if c[0] == "POST"]
assert len(post) == 1 and post[0][1] == "/rest/v1/projects"
row = json.loads(post[0][2])
assert row["owner_id"] == "user-1" and row["title"] == "T" and len(row["nodes"]) == 4 and len(row["edges"]) == 3
nodes_by_type = {n["type"] for n in row["nodes"]}
assert nodes_by_type == {"component", "splice"}
drv = next(n for n in row["nodes"] if n["data"].get("componentTypeId") == "ct-drv")
assert drv["data"]["openConnectors"] == ["c1"] and drv["data"]["componentType"]["builtinConnectors"][0]["name"] == "J3"
motor = next(n for n in row["nodes"] if n["data"].get("componentTypeId") == "ct-motor")
assert motor["position"] == {"x": 80 + round((120 - 0) * 220 / 120), "y": 80 + round((130 - 0) * 3)}, motor["position"]   # median box 120 wide -> 220; 60 tall -> clamp 3
e = {(x["source"], x["target"]): x for x in row["edges"]}
gnd = e[(motor["id"], drv["id"])]
assert gnd["sourceHandle"] == "l-1" and gnd["targetHandle"] == "l-p-c1-2" and gnd["data"]["colour"] == "bk" and gnd["style"]["stroke"] == "#000000"
assert gnd["type"] == "bundled" and gnd["data"]["bundleCores"] == 1 and gnd["data"]["drawSrcBlock"] and gnd["id"] == gnd["data"]["bundleId"] + "-0"
spl = next(n for n in row["nodes"] if n["type"] == "splice")
assert spl["data"] == {"label": "Junction", "numHandles": 1}
assert e[(motor["id"], spl["id"])]["sourceHandle"] == "l-0" and e[(motor["id"], spl["id"])]["data"]["colour"] == "rd"   # first free pin = +24V, red
assert e[(spl["id"], next(n["id"] for n in row["nodes"] if n["data"].get("componentTypeId") == "ct-cable"))]["targetHandle"] == "l-0"

# Miro: centre positions -> top-left, frame offsets, captions
ITEMS = [{"id": "f", "type": "frame", "position": {"x": 1000, "y": 1000}, "geometry": {"width": 400, "height": 400}},
         {"id": "a", "type": "shape", "data": {"content": "<p>Brush Motor</p>"}, "position": {"x": 100, "y": 50, "relativeTo": "parent_top_left"}, "geometry": {"width": 200, "height": 100}, "parent": {"id": "f"}},
         {"id": "b", "type": "sticky_note", "data": {"content": "E000040"}, "position": {"x": 0, "y": 0, "relativeTo": "canvas_center"}, "geometry": {"width": 200, "height": 200}},
         {"id": "c", "type": "card", "data": {"title": "Todo"}, "position": {"x": 5, "y": 5}, "geometry": {"width": 300, "height": 100}},
         {"id": "i", "type": "image", "position": {"x": 0, "y": 0}, "geometry": {"width": 10, "height": 10}}]
CONNS = [{"id": "k1", "startItem": {"id": "a"}, "endItem": {"id": "b"}, "captions": [{"content": "<p>+24V</p>"}]}]
server.miro_get = lambda path, token: ITEMS if path.endswith("/items") else CONNS
os.environ["MIRO_TOKEN"] = "tok"
nodes, links = server.read_miro("https://miro.com/app/board/uXjVabc=/?share=1")
byid = {n["id"]: n for n in nodes}
assert set(byid) == {"a", "b", "c"} and byid["a"]["label"] == "Brush Motor"
assert (byid["a"]["x"], byid["a"]["y"]) == (1000 - 200 + 100 - 100, 1000 - 200 + 50 - 50), byid["a"]   # frame top-left + offset, then centre -> corner
assert (byid["b"]["x"], byid["b"]["y"]) == (-100, -100)
assert links == [{"id": "k1", "source": "a", "target": "b", "label": "+24V"}]
out = json.loads(server.import_graph(nodes, links, "M", {}, "skip", False, "test"))
assert out["wires"][0]["from"] == "Brush Motor · +24V" and out["wires"][0]["colour"] == "rd"
os.environ["MIRO_TOKEN"] = "${user_config.miro_token}"
try: server.read_miro("x"); assert False
except RuntimeError as e: assert "no Miro access token" in str(e)
print("ok")
