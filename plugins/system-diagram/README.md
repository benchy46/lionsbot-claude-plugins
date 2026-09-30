# system-diagram

Claude Code plugin: an MCP server that imports a component **datasheet** into the ECDB
(the parts library behind the LionsBot hub's System Wiring Diagram). Give Claude the
PDF and it fills in the name, MPN, product photo, and every way the part connects:

- **wires**: flying leads, function + IEC 60757 colour, optional group (the plug they share)
- **terminals**: screw / spade / lug points, named as marked
- **connectors**: headers on the body, name + every pin's function in pin order

New components land **without an ECDB ID** (a person presses Assign ID after checking them) and an
existing component is never modified. The database refuses a part number or photo another component holds.

## Install

Needs [uv](https://docs.astral.sh/uv/) (`winget install --id=astral-sh.uv -e`).

```bash
claude plugin marketplace add benchy46/lionsbot-claude-plugins
claude plugin install system-diagram@lionsbot
```

The install asks for your **hub email and password** (the login for hub.mechanical.lionsbot.app,
not your Google or Microsoft one). The password goes into the Windows credential store. Every
component you import is saved as you. Change the login later: `/plugin` > Installed > system-diagram >
Configure options.

## Use

> Import this datasheet into the ECDB: C:\path\to\datasheet.pdf

- `read_datasheet(pdf_path, pages="1-8")`: page text + a picture of each page + embedded images (`xref:<n>`).
- `create_component(name, …, wires | terminals | connectors, image, datasheet_path, apply=False)`:
  `apply=False` previews (row, photo, duplicates, what blocks Assign ID); `apply=True` inserts.
  `image` = `xref:<n>`, `crop:<page>:x0,y0,x1,y1` (PDF points) or an image file path.

The PDF must be a local file: the server reads it from disk, so a file attached to a claude.ai chat is out of reach.

## Import a diagram drawn in draw.io or Miro

> Import C:\path\to\robot.drawio into the System Wiring Diagram
> Import this Miro board as a wiring diagram: https://miro.com/app/board/uXjV…=/

- `import_drawio(path, title="", page=1, mapping={}, unmatched="skip", apply=False)`
- `import_miro(board, title="", mapping={}, unmatched="skip", apply=False)` — needs the optional **Miro access token**
  (`/plugin` > Installed > system-diagram > Configure options): create a developer app at Miro > Profile settings >
  Your apps, install it on your team, copy its token (`boards:read` is enough).

Each box (draw.io vertex; Miro shape, sticky note, text or card) becomes the ECDB component it names — matched by an
ECDB ID in its text (`E000012`), else the exact component name, part number or model number, else a unique partial
name — and each line / connector between two boxes becomes a single-core wire. A line caption naming a pin function
(`GND`, `J3.4`) picks that pin; otherwise the wire takes the first free pin in ECDB order, so check the Wires tab afterwards.
Boxes that match nothing are skipped (`unmatched="splice"` keeps the connected ones as splice nodes), and `mapping`
corrects any box: `{"Brush motor": "E000012", "PSU": "skip", "Junction": "splice"}` (box text or cell id → ID, name, `splice`, `skip`).

`apply=False` previews (every box with what it became plus candidates for the unmatched ones, every wire); `apply=True`
creates a NEW project titled after the file or board. Nothing existing is modified. The draw.io file must be local
(`.drawio` or `.xml`, compressed or plain); Miro boards are read through the Miro REST API.
