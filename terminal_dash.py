#!/usr/bin/env python3
"""
Terminal Dash — a tiny Geometry-Dash-style auto-runner.

  Run:   python3 terminal_dash.py
         python3 terminal_dash.py --difficulty Hard --seed 42   (skip menus,
                 reproducible run — handy for practising a level)
  Menu:  ↑/↓ move, ENTER select — Start, then pick a difficulty
         (Easy → Extreme, or Dynamic which ramps up every 500 distance).
  Play:  SPACE / ↑ / w = jump     P = pause     Q = quit

Your cube auto-runs to the right. Jump over ▲ spikes, ride the █ platforms and
staircases, hop the ▀ floating stones over the spike pits, and ride the ║
gravity portals that flip you onto the ceiling to dodge the ▼ spikes hanging
there. Harder difficulties use a snappier, twitchier jump (same reach, less
reaction time). Your best distance per difficulty is saved between runs.

No dependencies beyond the standard library (uses curses).
"""

import curses
import random
import time
import sys
import os
import json


# input-feel constants (in frames): a small buffer + coyote window make the
# snappy tiers feel tight instead of unfair. A jump pressed a few frames early
# still fires the instant you land; a jump pressed a few frames after running
# off an edge still counts.
JUMP_BUFFER_FRAMES = 4
COYOTE_FRAMES = 4


class Game:
    """All the game state + physics, with zero terminal I/O so it can be
    unit-tested / self-tested headlessly (see --selftest)."""

    def __init__(self, height, width, player_x, seed=None):
        self.H = height
        self.W = width
        self.player_x = player_x        # fixed screen column of the cube
        self.GR = height - 3            # ground surface row (cube rests here)
        self.CEIL = 2                   # ceiling standing row (row 0 HUD, row 1 roof)

        # physics — MEASURED discrete arc: peak ≈ 2.5 rows (clears the height-2
        # obstacles, still lands on them) over just ~6 columns of airtime. Tuned
        # snappy: fast rise, fast fall, minimal hang time.
        self.G = 0.55                   # gravity per frame (high = snappy)
        self.JUMP_V = -1.9              # upward kick

        self.cam = 0                    # world scroll offset (columns), +1/frame
        self.py = float(self.GR)        # cube row (float)
        self.vy = 0.0
        self.on_ground = True
        self.alive = True
        self.dead_reason = ""

        self.gdir = 1                   # gravity direction: +1 down, -1 up (flip)
        self.coyote = 0                 # frames left where a late jump still fires
        self.jump_buffer = 0            # frames left on a buffered (early) jump

        self.spikes = {}                # world column -> row of a floor spike (▲)
        self.cspikes = {}               # world column -> row of a ceiling spike (▼)
        self.blocks = {}                # world column -> ground-pillar height
        self.cblocks = {}               # world column -> depth a ceiling block hangs
        self.floats = {}                # world column -> solid row of a floating ledge
        self.ceil = {}                  # world column -> row of the flip-zone ceiling
        self.portals = {}               # world column -> gravity direction to set
        self.rng = random.Random(seed)
        self.gen_until = player_x + 18  # runway before the first obstacle

    # -- world queries --------------------------------------------------------
    def player_col(self):
        return self.cam + self.player_x

    # -- level generation -----------------------------------------------------
    def generate(self):
        """Lazily spawn obstacles a bit past the right edge, and prune old
        ones behind us so an endless run stays cheap."""
        view_end = self.cam + self.W + 24
        while self.gen_until < view_end:
            c = self.gen_until
            self.gen_until = c + self._spawn(c)

        cutoff = self.cam - 2           # prune everything off-screen to the left
        if cutoff > 0:
            self.spikes = {k: v for k, v in self.spikes.items() if k >= cutoff}
            self.cspikes = {k: v for k, v in self.cspikes.items() if k >= cutoff}
            self.blocks = {k: v for k, v in self.blocks.items() if k >= cutoff}
            self.cblocks = {k: v for k, v in self.cblocks.items() if k >= cutoff}
            self.floats = {k: v for k, v in self.floats.items() if k >= cutoff}
            self.ceil = {k: v for k, v in self.ceil.items() if k >= cutoff}
            self.portals = {k: v for k, v in self.portals.items() if k >= cutoff}

    def _spawn(self, c):
        """Place one obstacle starting at column c; return the gap to the next."""
        GR = self.GR
        r = self.rng.random()

        if r < 0.20:                            # single spike
            self.spikes[c] = GR
            return self.rng.randint(8, 13)

        if r < 0.30:                            # double spike (longer hop)
            self.spikes[c] = GR
            self.spikes[c + 1] = GR
            return self.rng.randint(11, 16)

        if r < 0.47:                            # ground platform (sometimes spiked)
            h = self.rng.randint(1, 2)
            w = self.rng.randint(4, 7)
            for i in range(w):
                self.blocks[c + i] = h
            if w >= 6 and self.rng.random() < 0.4:   # a spike sitting ON the top,
                # keep it >=3 columns in so there's a real landing zone before it
                # (a spike right at the front makes the platform near-impossible)
                sc = c + self.rng.randint(3, w - 2)
                self.spikes[sc] = GR - h
            return w + self.rng.randint(9, 14)

        if r < 0.60:                            # simple floating ledge (under or on)
            w = self.rng.randint(3, 6)
            for i in range(w):
                self.floats[c + i] = GR - 1      # standing = GR-2 (reachable)
            return w + self.rng.randint(9, 14)

        if r < 0.72:                            # floating stepping-stones over spikes
            return self._spawn_float_steps(c)

        if r < 0.90:                            # ground staircase (peak 3-4), ~18%
            return self._spawn_stairs(c)

        return self._spawn_gravity(c)           # gravity-flip ceiling corridor

    def _spawn_stairs(self, c):
        """Ramp up to height 2, 3, or 4 (reachable only by stepping up each +1
        tread), then EITHER step gently back down, OR end in a sheer drop-off from
        the top (or from one step down) straight to the ground — sometimes with a
        spike planted just past where you land, and sometimes one sitting on the
        top tread (with landing room around it). Peaks lean tall: 4 is the most
        common, then 3, then 2."""
        GR = self.GR
        peak = self.rng.choices([2, 3, 4], weights=[2, 3, 4])[0]  # 4 likeliest, 2 rarest
        tread = self.rng.randint(8, 10)         # wide: forgiving to climb at any jump tune
        col = c
        for hgt in range(1, peak + 1):          # ascend 1..peak
            for _ in range(tread):
                self.blocks[col] = hgt
                col += 1

        peak_start = col - tread                 # first column of the top (peak) tread
        if self.rng.random() < 0.30:             # a spike on the top tread, with room
            sc = peak_start + self.rng.randint(2, tread - 3)  # >=2 to land, >=2 after
            self.spikes[sc] = GR - peak

        if self.rng.random() < 0.45:            # gentle: step all the way down
            for hgt in range(peak - 1, 0, -1):
                for _ in range(tread):
                    self.blocks[col] = hgt
                    col += 1
            return (col - c) + self.rng.randint(9, 13)

        # otherwise a DROP-OFF straight to the ground...
        if peak >= 3 and self.rng.random() < 0.5:    # ...from one step down (peak-1)
            for _ in range(tread):
                self.blocks[col] = peak - 1
                col += 1
        edge = col                               # the cube runs off here and falls
        if self.rng.random() < 0.55:             # a spike planted just past the landing
            self.spikes[edge + self.rng.randint(4, 6)] = GR
        return (col - c) + self.rng.randint(11, 15)

    def _spawn_float_steps(self, c):
        """Floating stepping-stones (jump-through) over a bed of ground spikes.
        You mount the first stone at standing GR-2 (the highest a ground jump
        reaches) and hop along. Like the ground staircases, the run has three
        shapes: step UP, step up then back DOWN, or step up then DROP off the last
        stone to clear ground. UP hops leave a spiked gap to jump across; DOWN
        steps butt on contiguously one row lower, so a fast fall can't overshoot
        the next stone into the spikes (validated by sweep on all difficulties)."""
        GR = self.GR
        sw = self.rng.randint(4, 5)             # stone width (room to land)
        gp = self.rng.randint(2, 3)             # spiked gap on the UP hops
        top = self.rng.randint(2, 4)            # highest stone: rows above ground (2..4)
        ascend = [GR - h for h in range(2, top + 1)]     # GR-2, GR-3, ... GR-top
        r = self.rng.random()
        if r < 0.40:                            # UP: reach the top and carry on
            seq, drop = ascend + [GR - top], False
        elif r < 0.72 and top >= 3:             # UP then step back DOWN to the ground
            descend = [GR - h for h in range(top - 1, 1, -1)]   # GR-(top-1) ... GR-2
            seq, drop = ascend + descend, False
        else:                                   # UP then DROP off the top to the ground
            seq, drop = ascend, True

        col = c
        prev = None
        for standing in seq:
            if prev is not None:
                # a lower next stone (larger row) butts on contiguously; an equal
                # or higher one leaves a spiked gap you jump across
                gap = 0 if standing > prev else gp
                for _ in range(gap):
                    self.spikes[col] = GR
                    col += 1
            for _ in range(sw):
                self.floats[col] = standing + 1  # solid row; standing = that - 1
                self.spikes[col] = GR            # spikes directly beneath each stone
                col += 1
            prev = standing

        if drop:                                # clear landing zone off the last stone
            col += self.rng.randint(3, 5)
            if self.rng.random() < 0.4:         # sometimes a spike a bit further on
                self.spikes[col + self.rng.randint(2, 4)] = GR
        return (col - c) + self.rng.randint(11, 15)

    def _spawn_gravity(self, c):
        """A gravity-flip corridor. A ║ portal inverts gravity so the cube swings
        up and runs along the CEILING — a fully mirrored world up there: dodge the
        ▼ spikes that hang from the roof by dipping down, dip onto the underside of
        █ ceiling platforms, and climb down/up mirrored ceiling staircases. A
        second portal restores normal gravity. Generous clear runway around each
        portal so the flip transition is never a trap, and an open-ceiling gap
        after every obstacle so the cube can rise back up before the next."""
        CEIL = self.CEIL
        self.portals[c] = -1                     # flip to ceiling gravity here
        col = c + 18                             # runway: swing up + settle on Easy
        obstacles = self.rng.randint(2, 3)
        for _ in range(obstacles):
            r = self.rng.random()
            if r < 0.40:                         # a cluster of hanging ceiling spikes
                for _ in range(self.rng.randint(1, 2)):
                    self.cspikes[col] = CEIL
                    col += 1
            elif r < 0.70:                       # a ceiling platform (dip onto it)
                d = self.rng.randint(1, 2)
                for _ in range(self.rng.randint(4, 6)):
                    self.cblocks[col] = d
                    col += 1
            else:                                # a mirrored ceiling staircase
                peak = self.rng.randint(2, 3)
                tread = self.rng.randint(6, 8)
                for depth in list(range(1, peak + 1)) + list(range(peak - 1, 0, -1)):
                    for _ in range(tread):
                        self.cblocks[col] = depth
                        col += 1
            col += self.rng.randint(8, 11)       # open ceiling to recover / read next
        col += 6                                 # runway before restoring gravity
        self.portals[col] = 1                    # back to normal gravity
        end = col + 12                           # runway to settle back on the floor
        for cc in range(c, end):                 # draw the ceiling across the whole
            self.ceil[cc] = CEIL - 1             # corridor (roof sits above the cube)
        return (end - c) + self.rng.randint(10, 14)

    # -- one simulation tick --------------------------------------------------
    def step(self, jump):
        if not self.alive:
            return

        self.cam += 1                          # world advances one column
        col = self.player_col()

        # gravity portal: crossing one flips (or restores) gravity direction and
        # peels the cube off whatever it was standing on.
        if col in self.portals:
            self.gdir = self.portals[col]
            self.on_ground = False
            self.coyote = 0

        # buffer the jump press so a slightly-early tap still fires on landing.
        if jump:
            self.jump_buffer = JUMP_BUFFER_FRAMES
        elif self.jump_buffer > 0:
            self.jump_buffer -= 1

        # a jump fires if one is buffered AND we're grounded or inside the coyote
        # window; it kicks away from whichever floor gravity is pulling us onto.
        if self.jump_buffer > 0 and (self.on_ground or self.coyote > 0):
            self.vy = self.JUMP_V * self.gdir
            self.on_ground = False
            self.coyote = 0
            self.jump_buffer = 0

        entry_py = self.py                     # height as we enter the new column

        self.vy += self.G * self.gdir
        self.py += self.vy

        if self.gdir == 1:
            # --- normal gravity: floor is the ground/blocks -------------------
            if self.py < self.CEIL:            # don't fly off the top
                self.py = self.CEIL
                self.vy = 0.0

            step_standing = self.GR - self.blocks.get(col, 0)
            support = step_standing
            crashed = entry_py > step_standing + 0.5

            # Floating ledge: a jump-THROUGH platform. It only supports you when
            # you come down onto it from above; from below or the side you pass
            # through freely (so jumping near one is never a trap).
            fr = self.floats.get(col)
            if fr is not None and entry_py <= (fr - 1) + 0.5:
                support = min(support, fr - 1)

            if crashed:
                self.alive = False
                self.dead_reason = "Crashed into a platform"
                return

            if self.py >= support:             # landed on the surface below
                self.py = support
                self.vy = 0.0
                self.on_ground = True
                self.coyote = COYOTE_FRAMES
            else:
                self.on_ground = False
                if self.coyote > 0:
                    self.coyote -= 1
        else:
            # --- flipped gravity: the ceiling (and blocks hanging from it) is
            # the floor. A ceiling block of depth d hangs down to CEIL+d-1, so the
            # cube rests against its underside at CEIL+d and crashes if it runs
            # into that face — the exact mirror of the ground-block logic above.
            if self.py > self.GR:              # don't fall off the bottom
                self.py = self.GR
                self.vy = 0.0

            ceil_standing = self.CEIL + self.cblocks.get(col, 0)
            support = ceil_standing
            crashed = entry_py < ceil_standing - 0.5

            if crashed:
                self.alive = False
                self.dead_reason = "Crashed into the ceiling"
                return

            if self.py <= support:             # landed against the ceiling/block
                self.py = support
                self.vy = 0.0
                self.on_ground = True
                self.coyote = COYOTE_FRAMES
            else:
                self.on_ground = False
                if self.coyote > 0:
                    self.coyote -= 1

        # Floor spike (points up): bites when the cube is at or below its row.
        srow = self.spikes.get(col)
        if srow is not None and round(self.py) >= srow:
            self.alive = False
            self.dead_reason = "Impaled on spikes"
            return

        # Ceiling spike (points down): bites when the cube is at or above its row.
        crow = self.cspikes.get(col)
        if crow is not None and round(self.py) <= crow:
            self.alive = False
            self.dead_reason = "Impaled on ceiling spikes"


# ---- difficulty --------------------------------------------------------------
# Each tune keeps the jump peak ~2.5 rows (so every obstacle stays reachable —
# proven solvable by beam search) and varies two things: jump snappiness
# (higher gravity + harder launch = a faster, twitchier arc) and wall-clock SCROLL
# SPEED (seconds per frame; smaller = the whole world flies at you faster). Speed
# is pure feel — fairness lives in column-space — so the top tiers can get frantic
# without becoming unbeatable. Fields: (name, gravity, jump_v, frame_seconds).
DIFFICULTIES = [
    ("Easy",      0.36, -1.55, 0.075),   # floaty jump, calm scroll  (~13 cols/s)
    ("Medium",    0.45, -1.72, 0.060),   # (~17 cols/s)
    ("Hard",      0.55, -1.90, 0.047),   # (~21 cols/s)
    ("Difficult", 0.70, -2.20, 0.036),   # fast fall + fast scroll    (~28 cols/s)
    ("Extreme",   0.90, -2.60, 0.028),   # snappiest + fastest        (~36 cols/s)
]
DYNAMIC_STEP = 500   # in Dynamic mode, climb one difficulty every 500 distance


# ---- high-score persistence -------------------------------------------------
def _scores_path():
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "terminal-dash", "scores.json")


def _load_scores():
    try:
        with open(_scores_path()) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_score(key, dist):
    """Record dist as the new best for `key` if it beats the stored one.
    Returns True when a new record was set."""
    scores = _load_scores()
    if dist <= scores.get(key, 0):
        return False
    scores[key] = dist
    try:
        path = _scores_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(scores, f)
    except OSError:
        pass
    return True


# ---- rendering -------------------------------------------------------------
C_PLAYER, C_SPIKE, C_BLOCK, C_FLOOR, C_UI, C_FLOAT, C_PORTAL = 1, 2, 3, 4, 5, 6, 7


def _init_colors():
    if not curses.has_colors():
        return False
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(C_PLAYER, curses.COLOR_YELLOW, -1)
    curses.init_pair(C_SPIKE, curses.COLOR_RED, -1)
    curses.init_pair(C_BLOCK, curses.COLOR_GREEN, -1)
    curses.init_pair(C_FLOOR, curses.COLOR_CYAN, -1)
    curses.init_pair(C_UI, curses.COLOR_WHITE, -1)
    curses.init_pair(C_FLOAT, curses.COLOR_MAGENTA, -1)
    curses.init_pair(C_PORTAL, curses.COLOR_BLUE, -1)
    return True


def _put(stdscr, y, x, ch, attr=0):
    """addstr that swallows the bottom-right-corner error curses loves to raise."""
    if 0 <= y < stdscr.getmaxyx()[0] and 0 <= x < stdscr.getmaxyx()[1]:
        try:
            stdscr.addstr(y, x, ch, attr)
        except curses.error:
            pass


def render(stdscr, g, color):
    stdscr.erase()

    def pair(n):
        return curses.color_pair(n) if color else 0

    # HUD
    diff = getattr(g, "diff_label", "")
    flip = "  ⇅FLIP" if g.gdir == -1 else ""
    hud = (f" TERMINAL DASH   {diff}{flip}   dist {g.cam:>5}"
           f"   [SPACE jump  P pause  Q quit] ")
    _put(stdscr, 0, 0, hud[: g.W - 1], pair(C_UI) | curses.A_BOLD)

    # floor
    for x in range(g.W):
        _put(stdscr, g.GR + 1, x, "▀", pair(C_FLOOR))

    base = g.cam

    # flip-zone ceiling: a solid roof the cube hangs beneath (bottom-of-cell glyph
    # so the cube, one row down, sits flush against it — mirror of the floor)
    for col, crow in g.ceil.items():
        x = col - base
        if 0 <= x < g.W:
            _put(stdscr, crow, x, "▄", pair(C_FLOOR) | curses.A_BOLD)

    # gravity portals: a translucent-looking vertical gate spanning the field
    for col, gd in g.portals.items():
        x = col - base
        if 0 <= x < g.W:
            for r in range(g.CEIL, g.GR + 1):
                _put(stdscr, r, x, "║", pair(C_PORTAL) | curses.A_BOLD)

    # obstacles in view
    for x in range(g.W):
        col = base + x
        h = g.blocks.get(col)
        if h:                                   # ground pillar / step
            for r in range(g.GR + 1 - h, g.GR + 1):
                _put(stdscr, r, x, "█", pair(C_BLOCK))
        d = g.cblocks.get(col)
        if d:                                   # ceiling pillar / step (hangs down)
            for r in range(g.CEIL, g.CEIL + d):
                _put(stdscr, r, x, "█", pair(C_BLOCK))
        fr = g.floats.get(col)
        if fr is not None:                      # floating ledge (top-of-cell so the
            _put(stdscr, fr, x, "▀", pair(C_FLOAT) | curses.A_BOLD)  # cube sits flush

    # spikes last, drawn at whatever row they sit on (ground or a platform top)
    for col, srow in g.spikes.items():
        x = col - base
        if 0 <= x < g.W:
            _put(stdscr, srow, x, "▲", pair(C_SPIKE) | curses.A_BOLD)
    for col, crow in g.cspikes.items():         # ceiling spikes point down
        x = col - base
        if 0 <= x < g.W:
            _put(stdscr, crow, x, "▼", pair(C_SPIKE) | curses.A_BOLD)

    # the cube
    _put(stdscr, int(round(g.py)), g.player_x, "■", pair(C_PLAYER) | curses.A_BOLD)

    stdscr.refresh()


# ---- big block-letter banner for the start screen --------------------------
_FONT = {
    "T": ["█████", "  █  ", "  █  ", "  █  ", "  █  "],
    "E": ["████", "█   ", "███ ", "█   ", "████"],
    "R": ["███ ", "█  █", "███ ", "█ █ ", "█  █"],
    "M": ["█   █", "██ ██", "█ █ █", "█   █", "█   █"],
    "I": ["███", " █ ", " █ ", " █ ", "███"],
    "N": ["█   █", "██  █", "█ █ █", "█  ██", "█   █"],
    "A": [" ██ ", "█  █", "████", "█  █", "█  █"],
    "L": ["█   ", "█   ", "█   ", "█   ", "████"],
    "D": ["███ ", "█  █", "█  █", "█  █", "███ "],
    "S": [" ███", "█   ", " ██ ", "   █", "███ "],
    "H": ["█  █", "█  █", "████", "█  █", "█  █"],
    " ": ["   ", "   ", "   ", "   ", "   "],
}


def _big(word):
    """Render a word as 5 rows of block letters (uniform width per row)."""
    rows = ["", "", "", "", ""]
    for ch in word.upper():
        g = _FONT.get(ch, _FONT[" "])
        for r in range(5):
            rows[r] += g[r] + " "
    return rows


def _draw_start(stdscr, color, sel):
    """Draw the decorated start screen with option `sel` highlighted."""
    def pair(n):
        return curses.color_pair(n) if color else 0

    stdscr.erase()
    H, W = stdscr.getmaxyx()
    t1, t2 = _big("TERMINAL"), _big("DASH")
    bw, dw = len(t1[0]), len(t2[0])
    cx = W // 2
    options = ["Start", "Quit"]
    wstep = 5
    sw = 2 * wstep                            # staircase width

    # fallback for small terminals: plain title
    if W < max(bw + 2, sw * 2 + dw + 8) or H < 20:
        y = max(0, H // 2 - 3)
        _put(stdscr, y, cx - 6, "TERMINAL DASH", pair(C_PLAYER) | curses.A_BOLD)
        for i, o in enumerate(options):
            lab = f"▶ {o} ◀" if i == sel else f"  {o}  "
            at = pair(C_UI) | (curses.A_BOLD | curses.A_REVERSE if i == sel else 0)
            _put(stdscr, y + 2 + i, cx - len(lab) // 2, lab, at)
        stdscr.refresh()
        return

    TALL, SHORT = 6, 3                         # staircase step heights (rows)
    total = 5 + 1 + 1 + 1 + TALL + 1 + 1 + len(options) + 1 + 1
    y = max(0, (H - total) // 2)

    # "TERMINAL" up top, above the floats
    for row in t1:
        _put(stdscr, y, cx - bw // 2, row, pair(C_PLAYER) | curses.A_BOLD)
        y += 1
    y += 1

    # three equal floating platforms spanning TERMINAL's width
    seg = (bw - 4) // 3
    floats = ("▀" * seg + "  ") * 2 + "▀" * seg
    _put(stdscr, y, cx - len(floats) // 2, floats, pair(C_FLOAT) | curses.A_BOLD)
    y += 2

    # "DASH" between the staircases, its top aligned with theirs
    band_top = y
    dy = band_top
    for row in t2:
        _put(stdscr, dy, cx - len(row) // 2, row, pair(C_PLAYER) | curses.A_BOLD)
        dy += 1

    # staircases flank DASH as one centered group (ascending 1→2 step left,
    # descending 2→1 step right), rising to DASH's height on a shared ground line.
    gap = 2
    group_w = sw + gap + dw + gap + sw
    lx = cx - group_w // 2
    rx = lx + group_w - sw
    for r in range(TALL):
        from_bottom = TALL - r
        short = "█" * wstep if from_bottom <= SHORT else " " * wstep
        _put(stdscr, band_top + r, lx, short + "█" * wstep, pair(C_BLOCK) | curses.A_BOLD)
        _put(stdscr, band_top + r, rx, "█" * wstep + short, pair(C_BLOCK) | curses.A_BOLD)
    floor_y = band_top + TALL
    _put(stdscr, floor_y, lx, "▀" * (rx + sw - lx), pair(C_FLOOR))     # shared ground

    # spike pit on the ground between the staircases, beneath DASH
    spikes = "▲" * (rx - (lx + sw))
    _put(stdscr, floor_y - 1, cx - len(spikes) // 2, spikes, pair(C_SPIKE) | curses.A_BOLD)

    y = floor_y + 2
    for i, o in enumerate(options):
        lab = f"▶ {o} ◀" if i == sel else f"  {o}  "
        at = pair(C_UI) | (curses.A_BOLD | curses.A_REVERSE if i == sel else 0)
        _put(stdscr, y, cx - len(lab) // 2, lab, at)
        y += 1
    y += 1
    _put(stdscr, y, cx - 12, "↑/↓ move    ENTER select", pair(C_FLOOR))
    stdscr.refresh()


def _start_menu(stdscr, color):
    """Decorated start screen. Returns 0 for Start, -1 to quit."""
    stdscr.nodelay(False)
    curses.curs_set(0)
    sel = 0
    while True:
        _draw_start(stdscr, color, sel)
        ch = stdscr.getch()
        if ch in (curses.KEY_UP, curses.KEY_DOWN, ord("k"), ord("j"),
                  ord("w"), ord("s"), ord("W"), ord("S")):
            sel ^= 1
        elif ch in (10, 13, curses.KEY_ENTER, ord(" ")):
            return -1 if sel == 1 else 0
        elif ch in (ord("q"), ord("Q"), 27):
            return -1


def _cooldown(stdscr, seconds):
    """Swallow all input for a moment, then flush any typeahead — so keys mashed
    during a run (or right as you die) can't leak into the next menu as an
    accidental selection."""
    stdscr.nodelay(True)
    t0 = time.time()
    while time.time() - t0 < seconds:
        while stdscr.getch() != -1:            # keep draining whatever arrives
            pass
        time.sleep(0.01)
    curses.flushinp()                          # discard anything still queued
    stdscr.nodelay(False)


def _menu(stdscr, color, title_lines, options, footer="", lockout=0.0):
    """A centered vertical menu. Returns the selected index, or -1 to go back.
    `lockout` ignores input for that many seconds after the first draw."""
    def pair(n):
        return curses.color_pair(n) if color else 0

    stdscr.nodelay(False)
    curses.curs_set(0)
    sel = 0
    first = True
    while True:
        stdscr.erase()
        H, W = stdscr.getmaxyx()
        block = len(title_lines) + 1 + len(options) + (2 if footer else 0)
        top = max(1, H // 2 - block // 2)
        for i, ln in enumerate(title_lines):
            _put(stdscr, top + i, max(0, (W - len(ln)) // 2), ln,
                 pair(C_PLAYER) | curses.A_BOLD)
        oy = top + len(title_lines) + 1
        for i, opt in enumerate(options):
            label = f"▶ {opt} ◀" if i == sel else f"  {opt}  "
            attr = pair(C_UI) | (curses.A_BOLD | curses.A_REVERSE if i == sel else 0)
            _put(stdscr, oy + i, max(0, (W - len(label)) // 2), label, attr)
        if footer:
            _put(stdscr, oy + len(options) + 1, max(0, (W - len(footer)) // 2),
                 footer, pair(C_FLOOR))
        stdscr.refresh()

        if first and lockout:                  # hold off input right after death
            _cooldown(stdscr, lockout)
            first = False

        ch = stdscr.getch()
        if ch in (curses.KEY_UP, ord("k"), ord("w"), ord("W")):
            sel = (sel - 1) % len(options)
        elif ch in (curses.KEY_DOWN, ord("j"), ord("s"), ord("S")):
            sel = (sel + 1) % len(options)
        elif ch in (10, 13, curses.KEY_ENTER, ord(" ")):
            return sel
        elif ch in (ord("q"), ord("Q"), 27):
            return -1


def _pause(stdscr, color):
    """Freeze the run until any key is pressed."""
    def pair(n):
        return curses.color_pair(n) if color else 0

    stdscr.nodelay(False)
    H, W = stdscr.getmaxyx()
    msg = "  PAUSED — press any key to resume  "
    _put(stdscr, H // 2, max(0, (W - len(msg)) // 2), msg,
         pair(C_UI) | curses.A_BOLD | curses.A_REVERSE)
    stdscr.refresh()
    while True:
        ch = stdscr.getch()
        if ch == curses.KEY_RESIZE:
            continue
        return


def _death_flash(stdscr, g, color):
    """A quick red blink on the cube so a death registers before the menu."""
    mark = (curses.color_pair(C_SPIKE) if color else 0) | curses.A_BOLD | curses.A_REVERSE
    for _ in range(3):
        render(stdscr, g, color)
        _put(stdscr, int(round(g.py)), g.player_x, "✖", mark)
        stdscr.refresh()
        time.sleep(0.09)
        stdscr.erase()
        stdscr.refresh()
        time.sleep(0.05)
    render(stdscr, g, color)
    stdscr.refresh()
    time.sleep(0.15)


def _game_over(stdscr, g, color, best, is_new):
    """Returns 'retry', 'menu', or 'quit'."""
    tag = "★ NEW BEST ★" if is_new else f"best   {best}"
    title = ["G A M E   O V E R", "", g.dead_reason, f"distance   {g.cam}", tag]
    # lock out input briefly so jumps mashed as you died don't auto-pick Retry
    sel = _menu(stdscr, color, title, ["Retry", "Difficulty menu", "Quit"],
                lockout=0.4)
    return {0: "retry", 1: "menu", 2: "quit", -1: "quit"}[sel]


def _play(stdscr, color, diff_index, seed=None):
    """Run one game at the chosen difficulty. Returns the finished Game, or None
    if the player quit mid-run. diff_index == len(DIFFICULTIES) means Dynamic."""
    H, W = stdscr.getmaxyx()
    g = Game(H, W, max(5, W // 6), seed=seed)
    dynamic = diff_index == len(DIFFICULTIES)
    level = 0 if dynamic else diff_index
    g.score_key = "Dynamic" if dynamic else DIFFICULTIES[diff_index][0]

    base_delay = DIFFICULTIES[level][3]

    def apply(lvl):
        nonlocal base_delay
        g.G = DIFFICULTIES[lvl][1]
        g.JUMP_V = DIFFICULTIES[lvl][2]
        base_delay = DIFFICULTIES[lvl][3]
        g.diff_label = (f"Dynamic: {DIFFICULTIES[lvl][0]}" if dynamic
                        else DIFFICULTIES[lvl][0])
    apply(level)

    stdscr.nodelay(True)
    while g.alive:
        t0 = time.time()
        jump = False
        paused = False
        while True:                            # drain buffered input
            ch = stdscr.getch()
            if ch == -1:
                break
            if ch in (ord(" "), curses.KEY_UP, ord("w"), ord("W")):
                jump = True
            elif ch in (ord("q"), ord("Q")):
                return None
            elif ch in (ord("p"), ord("P")):
                paused = True
            elif ch == curses.KEY_RESIZE:      # keep drawing to the new size
                g.H, g.W = stdscr.getmaxyx()

        if paused:
            _pause(stdscr, color)
            stdscr.nodelay(True)
            continue                           # resume without advancing the world

        if dynamic:                            # climb a difficulty every 500
            want = min(len(DIFFICULTIES) - 1, g.cam // DYNAMIC_STEP)
            if want > level:
                level = want
                apply(level)

        g.generate()
        g.step(jump)
        render(stdscr, g, color)

        # per-difficulty scroll speed, easing ~30% faster over distance
        frame = base_delay * max(0.70, 1.0 - g.cam * 0.00018)
        time.sleep(max(0.0, frame - (time.time() - t0)))

    _death_flash(stdscr, g, color)
    return g


def _run_diff(stdscr, color, diff_index, seed=None):
    """Play/retry loop for one difficulty. Returns 'quit' or 'menu'."""
    while True:
        result = _play(stdscr, color, diff_index, seed=seed)
        if result is None:
            return "quit"                       # quit mid-run
        is_new = _save_score(result.score_key, result.cam)
        best = _load_scores().get(result.score_key, result.cam)
        choice = _game_over(stdscr, result, color, best, is_new)
        if choice == "quit":
            return "quit"
        if choice == "menu":
            return "menu"
        # 'retry' -> loop and play again (a fixed --seed replays the same level)


def main(stdscr, opts):
    curses.curs_set(0)
    color = _init_colors()
    H, W = stdscr.getmaxyx()
    if H < 12 or W < 34:
        stdscr.nodelay(False)
        stdscr.addstr(0, 0, "Terminal too small — need at least 34x12. Press any key.")
        stdscr.getch()
        return

    seed = opts.get("seed")

    # --difficulty launches straight into a run (reproducible with --seed),
    # then falls through to the normal menus afterward.
    if opts.get("diff_index") is not None:
        if _run_diff(stdscr, color, opts["diff_index"], seed) == "quit":
            return

    while True:
        if _start_menu(stdscr, color) != 0:
            return                              # Start not chosen -> quit

        while True:                             # difficulty menu loop
            choices = [d[0] for d in DIFFICULTIES] + ["Dynamic"]
            d = _menu(stdscr, color, ["SELECT DIFFICULTY"], choices,
                      "Dynamic ramps up every 500 distance   (Q = back)")
            if d == -1:
                break                           # back to start menu
            if _run_diff(stdscr, color, d) == "quit":
                return                          # else 'menu' -> difficulty menu


# ---- headless self-test (no terminal needed) -------------------------------
def _selftest(frames=4000, lives=3):
    """Runs the engine with a naive dodge-AI to prove it steps, kills, and
    resets without raising. Not a difficulty benchmark."""
    def ai_jump(g):
        """A rough robustness probe (not optimal play). Handles ground hazards,
        taller faces (which need a couple columns of lead), hopping across
        floating stepping-stones, and dipping under ceiling spikes when flipped."""
        if not g.on_ground:
            return False
        pc = g.player_col()
        if g.gdir == -1:                         # on the ceiling: dip to dodge a ▼
            cur_d = g.cblocks.get(pc, 0)         # spike, or to step onto a █ block
            face = any(g.cblocks.get(pc + k, 0) > cur_d for k in range(2, 4))
            spike = any(g.cspikes.get(pc + k) == g.CEIL for k in range(1, 4))
            return face or spike
        fr = g.floats.get(pc)
        if fr is not None:                       # standing on a floating stone
            end = pc
            while g.floats.get(end + 1) is not None:
                end += 1
            near_edge = end - pc <= 2
            more_ahead = any(g.floats.get(end + 1 + j) is not None for j in range(9))
            return near_edge and more_ahead      # hop to the next stone
        cur_h = g.blocks.get(pc, 0)
        stand = g.GR - cur_h
        face = any(g.blocks.get(pc + k, 0) > cur_h for k in range(2, 4))
        spike = any(g.spikes.get(pc + k) == stand for k in range(1, 5))
        return face or spike

    best = 0
    for life in range(lives):
        g = Game(24, 80, 13, seed=life)
        while g.alive and g.cam < frames:
            g.generate()
            g.step(jump=ai_jump(g))
        best = max(best, g.cam)
        print(f"  life {life + 1}: distance={g.cam}  ({g.dead_reason or 'reached frame cap'})")
    print(f"self-test OK — no exceptions, best distance {best}")


def _parse_args(argv):
    opts = {"difficulty": None, "seed": None, "help": False}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--difficulty" and i + 1 < len(argv):
            opts["difficulty"] = argv[i + 1]
            i += 2
        elif a == "--seed" and i + 1 < len(argv):
            try:
                opts["seed"] = int(argv[i + 1])
            except ValueError:
                opts["seed"] = None
            i += 2
        elif a in ("-h", "--help"):
            opts["help"] = True
            i += 1
        else:
            i += 1
    return opts


def _resolve_difficulty(name):
    names = [d[0] for d in DIFFICULTIES] + ["Dynamic"]
    for i, n in enumerate(names):
        if n.lower() == name.lower():
            return i
    return None


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        sys.exit(0)

    opts = _parse_args(sys.argv[1:])
    if opts["help"]:
        print(__doc__)
        sys.exit(0)

    opts["diff_index"] = None
    if opts["difficulty"] is not None:
        idx = _resolve_difficulty(opts["difficulty"])
        if idx is None:
            names = ", ".join([d[0] for d in DIFFICULTIES] + ["Dynamic"])
            print(f"unknown difficulty: {opts['difficulty']!r}", file=sys.stderr)
            print(f"choices: {names}", file=sys.stderr)
            sys.exit(2)
        opts["diff_index"] = idx

    try:
        curses.wrapper(lambda s: main(s, opts))
    except KeyboardInterrupt:
        pass
