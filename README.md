# LionsBot Claude plugins

Claude Code plugins published by the LionsBot mechanical team. Nothing here holds a key or a password:
each plugin asks for **your own** hub login when you install it and signs in as you, so it can only do
what your account can.

| Plugin | What it does | Needs |
|---|---|---|
| `system-diagram` | Import a component datasheet into the System Wiring Diagram's component database (ECDB): photo, MPN, wires / terminals / connectors with every pin's function. New parts have no ID until a person assigns one. | [uv](https://docs.astral.sh/uv/) |
| `plm` | Read the PLM (SKU folders, metadata, BOMs) and upload STEP / PDF exports to a SKU. | [Node.js](https://nodejs.org/en/download) 18+ |

## Install (Claude Code)

In a terminal:

```bash
claude plugin marketplace add benchy46/lionsbot-claude-plugins
claude plugin install system-diagram@lionsbot
claude plugin install plm@lionsbot
```

Or inside a Claude Code session, the same commands with `/plugin` (e.g. `/plugin install plm@lionsbot`).
In the Claude desktop app's **Code** tab: click **+** beside the prompt box > **Plugins** > **Add plugin**.

Each install asks for your hub email + password. Change them later: `/plugin` > Installed > the plugin >
Configure options. Update to the latest version: `claude plugin marketplace update lionsbot`.

Plugins run in Claude Code only (terminal, IDE, desktop Code tab), not in Claude Desktop chat or claude.ai.
Step-by-step pages: the hub's System Wiring Diagram > **MCP**, and the PLM's **MCP** page.
