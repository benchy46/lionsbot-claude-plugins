# plm

Claude Code plugin for the LionsBot PLM (plm.mechanical.lionsbot.app). It wraps the npm package
[`plm-upload-mcp`](https://www.npmjs.com/package/plm-upload-mcp): read SKU folders, component metadata,
drawing-extracted metadata and BOMs, and upload SolidWorks STEP / PDF exports to a SKU's Drive folder.

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
