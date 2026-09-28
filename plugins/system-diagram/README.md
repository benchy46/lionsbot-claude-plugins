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
