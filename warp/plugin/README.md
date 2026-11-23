# 9spokes.warp: Cloudflare WARP bar widget

An `omarchy-shell` bar widget for the Cloudflare One (WARP) client. It draws in the active
theme's bar colours, so a theme switch restyles it with the rest of the bar.

| State | Bar icon |
|---|---|
| Connected | solid cloud |
| Connecting / disconnecting | pulsing cloud |
| Disconnected / paused | faded cloud with a slash |
| Not enrolled, service down, unable, unknown | faded cloud with the theme's urgent `!` badge |

- **Left click** opens a panel: state, reason, organization, a connect switch, and actions (sign in when not enrolled, re-authenticate Access when connected, refresh).
- **Right click** connects or disconnects. Middle click refreshes.
- **Keyboard** in the panel: `j`/`k` or arrows, `enter`, `t` toggle, `r` refresh, `esc`.
- **IPC:** `omarchy-shell 9spokes.warp status|toggle|open|close|refresh|connect|disconnect|signIn`.

State comes from `warp-cli -j status`, polled every `refreshIntervalSec`. A long-running
`warp-cli -l status` re-polls whenever it prints something; its output is used only as a
trigger. It is assumed to print on daemon changes, which has not been observed yet. A
disconnect that policy refuses shows the daemon's message in the panel. Sign-in runs in
its own process and is abandoned after 5 minutes.

## Install (prototype)

The plugin is a subdirectory of this repo, and `omarchy plugin add` needs a manifest at a
repo root. Until it has its own repo, copy it:

```bash
mkdir -p ~/.config/omarchy/plugins
cp -r warp/plugin ~/.config/omarchy/plugins/9spokes.warp
omarchy plugin validate ~/.config/omarchy/plugins/9spokes.warp
omarchy-shell shell rescanPlugins
omarchy plugin enable 9spokes.warp
```

See [docs/warp.md](../../docs/warp.md) for the WARP install, enrollment and CA trust.
