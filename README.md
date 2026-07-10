# Terminal Dash

A tiny [Geometry Dash](https://en.wikipedia.org/wiki/Geometry_Dash)-style
auto-runner that lives entirely in your terminal. Your cube runs to the right on
its own — you just decide *when* to jump. Clear the `▲` spikes, ride the `█`
platforms and staircases, hop the `▀` floating stones over spike pits, and ride
the `║` gravity portals that flip you onto the ceiling.

## Download (no Python needed)

Grab a ready-to-run build for your OS from the
[**Releases**](https://github.com/zambradley/terminal-dash/releases/latest) page —
it's a single self-contained binary, no Python or pip required. Run it **inside a
terminal** (it's a terminal game, not a windowed one).

| OS | File | Run it |
|----|------|--------|
| Linux | `terminal-dash-linux` | `chmod +x terminal-dash-linux && ./terminal-dash-linux` |
| macOS | `terminal-dash-macos` | `chmod +x terminal-dash-macos && ./terminal-dash-macos` |
| Windows | `terminal-dash-windows.exe` | run it in **Windows Terminal** for best results |

Notes:
- The binaries are **unsigned**, so macOS Gatekeeper / Windows SmartScreen may warn
  on first launch — allow it (right-click → Open on macOS).
- Windows support is best-effort: the block/color glyphs render well in **Windows
  Terminal** but can look rough in old `cmd.exe`/PowerShell consoles.

## Run from source

Needs **Python 3** (with `curses`, which is standard on Linux/macOS):

```
python3 terminal_dash.py
```

On Windows from source you'd need `pip install windows-curses` first, or use WSL.

## Controls

| Key | Action |
|-----|--------|
| `SPACE` / `↑` / `w` | Jump |
| `P` | Pause / resume |
| `Q` | Quit |
| `↑` `↓` `ENTER` | Navigate the menus |

## Difficulties

Pick one from the menu, or `Dynamic` to ramp up automatically (one tier harder
every 500 distance).

| Tier | Feel |
|------|------|
| Easy | Floaty jump, calm scroll |
| Medium | — |
| Hard | Snappier jump, faster scroll |
| Difficult | Fast fall + fast scroll |
| Extreme | Snappiest jump, fastest scroll |

Every tier keeps the **jump height the same** (~2.5 rows, so every obstacle
stays reachable — verified solvable by a beam-search solver). What changes is
*time*: harder tiers cut both the hang-time and the wall-clock per frame, so your
reaction window shrinks from ~600 ms on Easy to ~140 ms on Extreme. Same reach,
less time to react.

To make that snappiness fair rather than punishing, the controls have:

- **Jump buffering** — a jump pressed a few frames early still fires the instant
  you land.
- **Coyote time** — a jump pressed a few frames after running off an edge still
  counts.

## Obstacles

- `▲` **Spikes** — jump over them (single, double, or planted after a drop-off).
- `█` **Platforms & staircases** — land on top; climb steps up to height 4, then
  either step back down or run off a sheer drop. A spike sometimes sits on the
  top step (with room to land and clear it).
- `▀` **Floating stones** — jump-through stepping stones over spike pits. They
  step up, step up then back down, or step up then drop off to clear ground.
- `║` **Gravity portals** — flip gravity so the cube swings up and runs along a
  `▄` **ceiling**. Up there the world is fully mirrored: dip down to dodge the
  `▼` spikes hanging from the roof, dip onto the underside of ceiling platforms,
  and climb the mirrored ceiling staircases. A second portal drops you back to
  normal gravity.

## Scores

Your best distance per difficulty is saved between runs to
`~/.local/share/terminal-dash/scores.json` and shown on the game-over screen
(`★ NEW BEST ★` when you beat it).

## Command-line flags

```
terminal-dash --difficulty Hard          # skip the menus
terminal-dash --difficulty Hard --seed 42   # reproducible level
terminal-dash --help
terminal-dash --selftest                 # headless engine check (no terminal)
```

(From source, prefix with `python3 terminal_dash.py`.) A fixed `--seed`
regenerates the identical level every run — handy for practising a section, since
**Retry** replays the same layout.

## Build it yourself

The release binaries are produced with [PyInstaller](https://pyinstaller.org/).
To build one for your own machine:

```
pip install pyinstaller           # plus: pip install windows-curses (Windows only)
pyinstaller --onefile --name terminal-dash terminal_dash.py
# -> dist/terminal-dash
```

Pushing a `v*` tag builds all three platforms in CI and attaches them to a GitHub
Release automatically (see `.github/workflows/release.yml`).

## Requirements

- To run **from source**: Python 3 with `curses` (standard on Linux/macOS; Windows
  needs `windows-curses` or WSL). The prebuilt binaries need none of this.
- A terminal at least 34×12; the start screen decorations want ~20 rows.

## License

[MIT](LICENSE).

## Notes

Terminal Dash is an original, from-scratch homage — its code and ASCII art are
its own. It is **not affiliated with or endorsed by RobTop Games**; *Geometry
Dash* is a trademark of its owner.
