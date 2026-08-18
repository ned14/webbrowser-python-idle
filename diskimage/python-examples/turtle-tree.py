"""
photorealistic_tree.py
======================
A stylized-photorealistic tree scene drawn with Python's built-in Turtle
graphics, using a recursive fractal algorithm for the tree.

Realism tricks (2D vector, painterly style):
  * Banded vertical sky and ground gradients
  * Atmospheric haze band at the horizon
  * Yellow sun with a warm radial glow, clouds with shaded undersides
  * Distant hills + muted treeline for depth
  * Tapered recursive branches with a lit side and a shadow side
    (shading follows a global sun direction)
  * Layered foliage: dark canopy mass -> mid-tone core -> bright
    leaf clusters at every branch tip, biased toward the sun
  * Cast shadow blob, dirt path, grass blades, flowers, birds

Fast: everything is buffered with tracer(0) and painted in one pass
with a final screen.update().

Run:
    python3 photorealistic_tree.py

Standard library only (Python 3.6+). On Linux you may need:
    sudo apt install python3-tk
"""

import math
import random
import turtle

# ------------------------------------------------------------ canvas setup
WIDTH, HEIGHT = 1000, 750
HORIZON = 170                       # y of the sky/ground line
SUN_X, SUN_Y = 815, 595
SUN_DIR = (0.62, 0.79)              # ~unit vector pointing toward the sun
TREE_X, TREE_Y = 480, 150           # base of the trunk

SKY_TOP = (64, 128, 198)
SKY_BOTTOM = (196, 224, 242)
GROUND_TOP = (110, 152, 74)
GROUND_BOTTOM = (38, 82, 40)

MAX_DEPTH = 6                       # recursion depth of the fractal tree

screen = turtle.Screen()
screen.title("Photorealistic Tree - Turtle Fractal")
screen.setup(WIDTH, HEIGHT)
# Pin (0,0) to the BOTTOM-LEFT corner so the 0-based scene coordinates
# map exactly onto the window (turtle's default origin is the center).
screen.setworldcoordinates(0, 0, WIDTH, HEIGHT)
screen.colormode(255)
screen.bgcolor((120, 170, 220))
screen.tracer(0)                    # buffer all drawing => fast
screen.delay(0)

t = turtle.Turtle()
t.hideturtle()
t.speed(0)

random.seed(20240817)               # fixed seed => deterministic, nice layout

# --------------------------------------------------------------- helpers

def mix(c1, c2, f):
    """Blend two RGB tuples; f=0 gives c1, f=1 gives c2."""
    return (int(c1[0] + (c2[0] - c1[0]) * f),
            int(c1[1] + (c2[1] - c1[1]) * f),
            int(c1[2] + (c2[2] - c1[2]) * f))


def jitter(c, amt=8):
    """Randomly nudge a colour (clamped to 0..255)."""
    def r(v):
        return max(0, min(255, v + random.randint(-amt, amt)))
    return (r(c[0]), r(c[1]), r(c[2]))


def rect(x, y, w, h, color):
    """Axis-aligned filled rectangle, bottom-left corner at (x, y)."""
    t.penup(); t.goto(x, y); t.setheading(0); t.pendown()
    t.color(color)
    t.begin_fill()
    for _ in range(2):
        t.forward(w); t.left(90)
        t.forward(h); t.left(90)
    t.end_fill()


def fill_poly(points, color):
    """Fill a closed polygon from a list of (x, y) vertices."""
    t.penup(); t.goto(points[0]); t.pendown()
    t.color(color)
    t.begin_fill()
    for p in points[1:]:
        t.goto(p)
    t.end_fill()


def fill_circle(x, y, r, color, steps=18):
    """Filled circle centred at (x, y) with a low vertex count for speed."""
    t.penup(); t.goto(x, y - r); t.setheading(0); t.pendown()
    t.color(color)
    t.begin_fill()
    t.circle(r, 360, steps)
    t.end_fill()


def tapered_part(x, y, ang, length, w0, w1, f0a, f0b, f1a, f1b, color):
    """
    Fill the quadrilateral of a branch section between perpendicular
    offsets f0a->f0b at the base and f1a->f1b at the tip. Offsets are
    fractions of the half-width: +1 = full left edge, -1 = full right
    edge, 0 = the centre line ('left' = 90 deg CCW of travel).
    Used for the whole branch and for the lit/shadow side strips.
    """
    rad = math.radians(ang)
    dx, dy = math.cos(rad), math.sin(rad)
    px, py = -dy, dx                                # unit 'left' vector
    x2, y2 = x + dx * length, y + dy * length
    def pt(cx, cy, hw, f):
        return (cx + px * hw * f, cy + py * hw * f)
    t.penup(); t.goto(pt(x, y, w0 / 2.0, f0a)); t.pendown()
    t.color(color)
    t.begin_fill()
    t.goto(pt(x, y, w0 / 2.0, f0b))
    t.goto(pt(x2, y2, w1 / 2.0, f1b))
    t.goto(pt(x2, y2, w1 / 2.0, f1a))
    t.end_fill()

# ------------------------------------------------------------- environment

def sky():
    n = 40
    h = (HEIGHT - HORIZON) / n
    for i in range(n):
        ybot = HORIZON + i * h
        f = (ybot + h / 2 - HORIZON) / (HEIGHT - HORIZON)
        rect(-5, ybot, WIDTH + 10, h + 0.5, mix(SKY_BOTTOM, SKY_TOP, f))


def sun():
    f = (SUN_Y - HORIZON) / (HEIGHT - HORIZON)
    sky_c = mix(SKY_BOTTOM, SKY_TOP, f)
    core = (255, 208, 58)               # golden-yellow disk
    glow = (255, 229, 143)              # warm amber halo
    for r, p in ((160, 0.08), (120, 0.20), (88, 0.40), (62, 0.75)):
        fill_circle(SUN_X, SUN_Y, r, mix(sky_c, glow, p), 36)
    fill_circle(SUN_X, SUN_Y, 46, glow, 36)
    fill_circle(SUN_X, SUN_Y, 33, core, 36)


def cloud(x, y, s):
    puffs = [(-44, -2, 24), (-16, -12, 32), (16, -6, 28),
             (44, 0, 20), (-2, 6, 26), (26, 12, 20)]
    for dx, dy, r in puffs:                          # shaded underside
        fill_circle(x + dx * s, y + (dy - 7) * s, r * s, (221, 228, 238), 16)
    for dx, dy, r in puffs:                          # bright top
        fill_circle(x + dx * s, y + dy * s, r * s, (247, 250, 253), 16)


def bird(x, y, s):
    t.penup(); t.goto(x, y); t.pendown()
    t.width(1.6)
    t.pencolor((44, 48, 56))
    t.goto(x - 11 * s, y + 5 * s)
    t.goto(x, y)
    t.goto(x + 11 * s, y + 5 * s)
    t.penup(); t.width(1)


def hill(cx, half, height, color):
    """Smooth parabolic dome sitting on the horizon."""
    steps = 26
    pts = [(cx - half, HORIZON)]
    for i in range(1, steps + 1):
        x = cx - half + 2 * half * i / steps
        u = (x - cx) / half
        pts.append((x, HORIZON + height * (1 - u * u)))
    pts.append((cx + half, HORIZON))
    fill_poly(pts, color)


def treeline():
    x = 25
    while x < WIDTH - 20:
        fill_circle(x, HORIZON - 3, random.uniform(6, 13),
                    mix((60, 94, 64), (86, 120, 84), random.random()), 12)
        x += random.uniform(18, 42)


def fog():
    """A band of atmospheric haze that fades into the sky colour."""
    haze = (206, 224, 234)
    sky_c = mix(SKY_BOTTOM, SKY_TOP, 48 / (HEIGHT - HORIZON))
    for i in range(6):
        rect(-5, HORIZON + i * 8, WIDTH + 10, 8.5, mix(haze, sky_c, i / 5.0))


def ground():
    n = 34
    h = HORIZON / n
    for i in range(n):
        ytop = HORIZON - i * h
        f = (HORIZON - (ytop - h / 2)) / HORIZON
        rect(-5, ytop - h, WIDTH + 10, h + 0.5, mix(GROUND_TOP, GROUND_BOTTOM, f))


def grass():
    greens = [(66, 112, 54), (82, 128, 62), (98, 142, 70)]
    for _ in range(70):
        t.penup()
        t.goto(random.uniform(10, WIDTH - 10), random.uniform(8, 160))
        t.setheading(random.uniform(80, 100))
        t.pendown()
        t.width(random.uniform(1.0, 2.0))
        t.pencolor(random.choice(greens))
        t.forward(random.uniform(3, 8))
    t.width(1)


def flowers():
    cols = [(240, 240, 245), (238, 122, 130), (244, 214, 110), (158, 168, 232)]
    for _ in range(22):
        x = (random.uniform(20, 340) if random.random() < 0.5
             else random.uniform(620, 980))
        t.penup()
        t.goto(x, random.uniform(15, 150))
        t.pendown()
        t.dot(2.4, random.choice(cols))


def path():
    pts = [(372, 0), (444, 52), (464, 92), (474, 122),
           (498, 122), (508, 92), (538, 52), (588, 0)]
    fill_poly(pts, (172, 146, 104))
    for sx, sy, sr in ((430, 40, 4.0), (500, 62, 3.4), (470, 100, 3.0),
                       (521, 30, 2.6), (452, 74, 2.8)):
        fill_circle(sx, sy, sr, (146, 140, 130), 12)


def tree_shadow():
    """Soft two-layer cast shadow, angled away from the sun."""
    cx, cy, phi = 385, 138, math.radians(-7)
    cp, sp = math.cos(phi), math.sin(phi)
    def blob(rx, ry, color):
        pts = []
        for i in range(26):
            th = 2 * math.pi * i / 26
            ex, ey = rx * math.cos(th), ry * math.sin(th)
            pts.append((cx + ex * cp - ey * sp, cy + ex * sp + ey * cp))
        fill_poly(pts, color)
    blob(175, 30, (64, 104, 54))
    blob(125, 21, (48, 88, 44))

# ------------------------------------------------------------------ the tree

def branch_color(depth):
    f = depth / MAX_DEPTH                       # 0 = trunk ... 1 = twigs
    return jitter(mix((94, 66, 44), (146, 122, 96), f * 0.85), 6)


def leaf_cluster(x, y, L):
    """A small 3-layer foliage puff at a branch tip, biased to the sun."""
    R = L * 1.25 * random.uniform(0.85, 1.3)
    dark = mix((36, 76, 36), (22, 48, 24), random.random())
    mid = mix((54, 102, 48), (74, 126, 58), random.random())
    light = mix((92, 146, 66), (118, 172, 84), random.random())
    fill_circle(x - R * 0.08, y - R * 0.12, R, dark, 16)
    fill_circle(x + R * 0.10, y + R * 0.10, R * 0.82, mid, 16)
    fill_circle(x + R * 0.28, y + R * 0.30, R * 0.55, light, 14)
    if random.random() < 0.5:
        fill_circle(x + R * 0.42, y + R * 0.46, R * 0.32,
                    mix(light, (172, 208, 112), 0.5), 12)


def branch(x, y, ang, length, width, depth):
    """Recursive fractal: one tapered, shaded section, then 2-3 children."""
    color = branch_color(depth)
    w1 = width * 0.62                           # width at the tip
    tapered_part(x, y, ang, length, width, w1, -1, 1, -1, 1, color)

    if depth <= 3:                              # cylindrical shading
        shadow_c = mix(color, (26, 17, 10), 0.55)
        lit_c = mix(color, (216, 186, 142), 0.40)
        rad = math.radians(ang)
        px, py = -math.sin(rad), math.cos(rad)  # 'left' of travel
        if px * SUN_DIR[0] + py * SUN_DIR[1] >= 0:
            # left side faces the sun
            tapered_part(x, y, ang, length, width, w1, -1, -0.45, -1, -0.45, shadow_c)
            tapered_part(x, y, ang, length, width, w1, 0.18, 1, 0.18, 1, lit_c)
        else:
            # right side faces the sun
            tapered_part(x, y, ang, length, width, w1, 0.18, 1, 0.18, 1, shadow_c)
            tapered_part(x, y, ang, length, width, w1, -1, -0.45, -1, -0.45, lit_c)

    tx = x + math.cos(math.radians(ang)) * length
    ty = y + math.sin(math.radians(ang)) * length

    if depth <= 0 or width < 1.2:               # terminal: foliage
        leaf_cluster(tx, ty, length)
        return

    spread = random.uniform(15, 27)
    if random.random() < 0.35 and depth >= 2:
        angles = [ang - spread, ang + random.uniform(-5, 5), ang + spread]
        lens = [length * 0.66, length * 0.74, length * 0.66]
        widths = [w1 * 0.88, w1 * 1.05, w1 * 0.88]
    else:
        angles = [ang - spread, ang + spread]
        lens = [length * 0.70, length * 0.70]
        widths = [w1, w1]
    for a, ln, wd in zip(angles, lens, widths):
        branch(tx, ty, a + random.uniform(-4, 4), ln, wd, depth - 1)


def canopy_mass():
    """Dark backdrop of foliage behind the branches, unifies the silhouette."""
    cx, cy = 468, 385
    for _ in range(16):
        a = random.uniform(0, 2 * math.pi)
        rr = random.uniform(35, 105)
        fill_circle(cx + math.cos(a) * rr * 1.15, cy + math.sin(a) * rr * 0.80,
                    random.uniform(55, 100),
                    mix((34, 68, 34), (52, 94, 46), random.random()), 24)
    fill_circle(cx, cy, 118,
                mix((38, 74, 36), (54, 96, 48), random.random()), 28)
    for _ in range(9):                           # mid-tone core
        a = random.uniform(0, 2 * math.pi)
        rr = random.uniform(10, 75)
        fill_circle(cx + math.cos(a) * rr * 1.10, cy + math.sin(a) * rr * 0.75,
                    random.uniform(38, 64),
                    mix((46, 90, 42), (62, 108, 52), random.random()), 20)


def trunk_texture():
    for _ in range(7):
        t.penup()
        t.goto(random.uniform(471, 487), random.uniform(158, 232))
        t.setheading(90)
        t.pendown()
        t.width(random.uniform(1.4, 2.4))
        t.pencolor((54, 37, 25))
        t.forward(random.uniform(10, 22))
    t.width(1)

# -------------------------------------------------------------------- main

def main():
    # Far background (painter's order: back to front)
    sky()
    sun()
    cloud(175, 645, 1.0)
    cloud(430, 695, 0.7)
    cloud(635, 585, 0.85)
    bird(760, 480, 1.0)
    bird(805, 512, 0.8)
    bird(720, 445, 0.6)
    hill(560, 520, 88, (128, 158, 138))
    hill(150, 430, 118, (116, 148, 128))
    hill(940, 420, 128, (108, 140, 122))
    treeline()
    fog()
    # Near ground
    ground()
    grass()
    flowers()
    path()
    tree_shadow()
    screen.update()                     # show environment, then the tree
    canopy_mass()
    branch(TREE_X, TREE_Y, 89, 95, 26, MAX_DEPTH)
    trunk_texture()
    screen.update()
    screen.mainloop()


if __name__ == "__main__":
    main()
    