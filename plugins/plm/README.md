# plm

Claude Code plugin for the LionsBot PLM (plm.mechanical.lionsbot.app). It wraps the npm package
[`plm-upload-mcp`](https://www.npmjs.com/package/plm-upload-mcp): read SKU folders, component metadata,
drawing-extracted metadata and BOMs; upload SolidWorks STEP / PDF exports to a SKU's Drive folder (a drawing PDF is
read into the part, like an upload in the PLM); create a SKU's Drive folder; and uprev a staging prototype
(`MNT-1234-X1` → `X2`) with its new drawing and STEP. An uprev whose drawing still shows the old SKU/REV stops and
asks first.

## Install

Needs [Node.js](https://nodejs.org/en/download) 18+. Windows only (it starts the server with `cmd /c npx`).

```bash
claude plugin marketplace add benchy46/lionsbot-claude-plugins
claude plugin install plm@lionsbot
```

The install asks for your **PLM email and password** (your hub login, not your Google or Microsoft one).
The password goes into the Windows credential store, and every upload is attributed to you.
Added the server by hand before (`claude mcp add plm-upload …`)? Remove that copy:
`claude mcp remove plm-upload -s user`.

## Update

```bash
claude plugin marketplace update lionsbot
```

Then restart Claude Code. The server starts as `plm-upload-mcp@latest`, so each start picks up a new npm release.
