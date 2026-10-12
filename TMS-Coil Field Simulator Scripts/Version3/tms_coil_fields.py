#!/usr/bin/env python3
"""
tms_coil_fields.py
==================

Visualize the magnetic field (B) and the induced electric field (E) of TMS
coils. This file is also the coil/field library used by tms_coil_3d.py.

Coils
-----
  single, single_large           round planar coils
  fig8, fig8_small, fig8_large   figure-8 coils
  fig8_overlap                   figure-8 with overlapping wings
  fig8_rev_right / _left / _both figure-8 wiring variants
  double_cone                    wings angled to wrap around the head
                                 (3-D script only: needs the curved head)
  array_orient                   steering array: two stacked, crossed
                                 figure-8s; rotates the E-field direction
  array_grid                     steering array: 3x3 small coils; moves the
                                 hotspot to a chosen target

Steering arrays get their per-coil currents ("weights") solved
automatically for the target set by --steer-x / --steer-y / --steer-angle:
  matched  maximise E at the target for a given total current
  focal    E at the target while suppressing E on two rings around it
           (sharper spot, needs bigger opposing currents)

Physics model
-------------
* Each wing is a flat spiral built from short straight wire segments. The
  head is below the coil (z < 0).
* B: exact Biot-Savart solution for finite straight segments.
* A: exact closed-form vector potential for straight segments; the induced
  electric field is E = -dA/dt. This is the "primary" field in free space,
  ignoring charge build-up at tissue boundaries, so absolute numbers are
  approximate (~10-30 %); patterns and trends are right.
* Lead wires / return paths are not modelled.

Sign conventions
----------------
* "CCW" / "CW" = current circulation viewed from above (+z).
* Standard figure-8: left wing CCW, right wing CW, so currents under the
  junction run the same way (+y) and add.
* Polarity -1 = that wing's leads swapped. Array labels show each coil's
  signed current weight.

Usage
-----
    python tms_coil_fields.py                       # wiring comparison
    python tms_coil_fields.py --configs designs     # coil designs
    python tms_coil_fields.py --configs steering --steer-x 2 --steer-y 1
    python tms_coil_fields.py --configs array_grid --array-mode matched

Presets for --configs: wiring, designs, steering, advanced, all

Requirements: numpy, matplotlib  (pip install numpy matplotlib)
"""
from __future__ import annotations

import argparse
import itertools
import os
from dataclasses import dataclass, replace

import numpy as np

MU0 = 4e-7 * np.pi  # vacuum permeability [T*m/A]

# --------------------------------------------------------------------------
# Coil configurations
#   geom: optional per-design size override (r_in / r_out in cm, turns)
# --------------------------------------------------------------------------
CONFIGS = {
    "single":         dict(kind="single",  pol=(+1,),
                           title="Single planar coil"),
    "single_large":   dict(kind="single",  pol=(+1,),
                           geom=dict(r_in=3.0, r_out=6.0, turns=14),
                           title="Large single coil (12 cm)"),
    "fig8":           dict(kind="figure8", pol=(+1, +1),
                           title="Figure-8: standard wiring"),
    "fig8_rev_right": dict(kind="figure8", pol=(+1, -1),
                           title="Figure-8: right wing reversed"),
    "fig8_rev_left":  dict(kind="figure8", pol=(-1, +1),
                           title="Figure-8: left wing reversed"),
    "fig8_rev_both":  dict(kind="figure8", pol=(-1, -1),
                           title="Figure-8: both wings reversed"),
    "fig8_overlap":   dict(kind="figure8", pol=(+1, +1), overlap=True,
                           title="Figure-8: overlapping wings"),
    "fig8_small":     dict(kind="figure8", pol=(+1, +1),
                           geom=dict(r_in=1.0, r_out=2.0, turns=8),
                           title="Small figure-8 (4 cm wings)"),
    "fig8_large":     dict(kind="figure8", pol=(+1, +1),
                           geom=dict(r_in=3.0, r_out=6.0, turns=12),
                           title="Large figure-8 (12 cm wings)"),
    "double_cone":    dict(kind="double_cone", pol=(+1, +1),
                           geom=dict(r_in=2.5, r_out=5.5, turns=10),
                           title="Double-cone coil"),
    "array_orient":   dict(kind="array_orient",
                           title="Steering array: 2 crossed figure-8s"),
    "array_grid":     dict(kind="array_grid",
                           geom=dict(r_in=0.6, r_out=1.9, turns=6,
                                     n_per_turn=48),
                           title="Steering array: 3x3 coils"),
}
PRESETS = {
    "wiring":   ["single", "fig8", "fig8_rev_right", "fig8_rev_both"],
    "designs":  ["single", "single_large", "fig8", "fig8_small",
                 "fig8_large", "fig8_overlap"],
    "steering": ["array_orient", "array_grid"],
    "advanced": ["fig8", "fig8_large", "double_cone", "array_orient",
                 "array_grid"],
    "all":      list(CONFIGS),
}
DEFAULT_CONFIGS = PRESETS["wiring"]
NEEDS_CURVED_HEAD = {"double_cone"}

CCW_COLOR = "#1f77b4"   # blue  = counter-clockwise (viewed from above)
CW_COLOR = "#d62728"    # red   = clockwise


def expand_configs(names):
    """Expand presets and keep order without duplicates."""
    out = []
    for n in names:
        for m in PRESETS.get(n, [n]):
            if m not in out:
                out.append(m)
    return out


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------
@dataclass
class WingGeometry:
    """Default planar spiral wing. Lengths in metres."""
    r_in: float = 0.028        # inner winding radius
    r_out: float = 0.0435      # outer winding radius
    turns: float = 9.0
    gap: float = 0.002         # gap between figure-8 wings
    overlap: float = 0.02      # wing overlap for fig8_overlap
    overlap_dz: float = 0.006  # height of a stacked wing / coil layer
    cone_angle: float = 100.0  # angle between double-cone wings [deg]
    cone_clearance: float = 0.005  # wing-to-scalp clearance [m]
    n_per_turn: int = 72       # straight segments per turn


@dataclass
class Coil:
    label: str
    a: np.ndarray        # (K,3) segment start points
    b: np.ndarray        # (K,3) segment end points
    sign: np.ndarray     # (K,) current weight per segment
    wings: list          # per-wing info for drawing


@dataclass
class Steer:
    """Where a steering array should aim."""
    target: np.ndarray     # target point (3,) [m]
    direction: np.ndarray  # desired E direction at the target (unit, 3)
    penalty: np.ndarray    # points where E should be small (focal mode)
    mode: str              # "matched" or "focal"
    desc: str
    angle: float
    depth_cm: float
    head_R: float | None


def rot_z(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])


def rot_y(b):
    c, s = np.cos(b), np.sin(b)
    return np.array([[c, 0, s], [0, 1.0, 0], [-s, 0, c]])


def spiral_polyline(cx, cy, cz, r_in, r_out, turns, n_per_turn):
    """Counter-clockwise (viewed from +z) Archimedean spiral, inner -> outer."""
    n = int(round(turns * n_per_turn))
    t = np.linspace(0.0, 2 * np.pi * turns, n + 1)
    r = r_in + (r_out - r_in) * t / t[-1]
    return np.column_stack([cx + r * np.cos(t), cy + r * np.sin(t),
                            np.full_like(t, cz)])


def make_wing(g, sense, pol=1, R=None, t=(0.0, 0.0, 0.0)):
    """A spiral wing, rotated by R about its own centre, then moved to t."""
    R = np.eye(3) if R is None else R
    t = np.asarray(t, float)
    pts = spiral_polyline(0, 0, 0, g.r_in, g.r_out, g.turns,
                          g.n_per_turn) @ R.T + t
    return dict(pts=pts, sense=sense, pol=pol, weight=1.0, center=t,
                cz=t[2], r_in=g.r_in, r_out=g.r_out,
                n_per_turn=g.n_per_turn, label=None)


def figure8_wings(g, pol=(1, 1), offset=None, dz_right=0.0, R=None,
                  t=(0.0, 0.0, 0.0)):
    c = g.r_out + g.gap / 2 if offset is None else offset
    R = np.eye(3) if R is None else R
    t = np.asarray(t, float)
    return [make_wing(g, +1, pol[0], R=R, t=R @ [-c, 0, 0] + t),
            make_wing(g, -1, pol[1], R=R, t=R @ [c, 0, dz_right] + t)]


def coil_from_wings(label, wings):
    A, B, S = [], [], []
    for w in wings:
        w["sign"] = w["sense"] * w["pol"] * w["weight"]
        p = w["pts"]
        A.append(p[:-1])
        B.append(p[1:])
        S.append(np.full(len(p) - 1, float(w["sign"])))
    return Coil(label, np.vstack(A), np.vstack(B), np.concatenate(S), wings)


def make_steer(x_cm, y_cm, angle_deg, depth_cm, mode, head_R=None):
    """Target at the given depth below (x, y); E direction angle in the
    horizontal plane (0 deg = +x, 90 deg = +y)."""
    d = depth_cm / 100

    def onto(xx, yy):
        if head_R is None:
            return np.column_stack([xx, yy, np.full_like(xx, -d)])
        r = head_R - d
        zz = -head_R + np.sqrt(np.maximum(r * r - xx ** 2 - yy ** 2, 0))
        return np.column_stack([xx, yy, zz])

    x, y = x_cm / 100, y_cm / 100
    target = onto(np.array([x]), np.array([y]))[0]
    a = np.radians(angle_deg)
    dirv = np.array([np.cos(a), np.sin(a), 0.0])
    if head_R is not None:                      # keep it tangential
        nrm = target - np.array([0, 0, -head_R])
        nrm /= np.linalg.norm(nrm)
        dirv = dirv - (dirv @ nrm) * nrm
        dirv /= np.linalg.norm(dirv)
    rings = []
    for rr, n in ((0.02, 16), (0.035, 24)):
        th = np.linspace(0, 2 * np.pi, n, endpoint=False)
        rings.append(onto(x + rr * np.cos(th), y + rr * np.sin(th)))
    desc = f"target ({x_cm:g}, {y_cm:g}) cm, E at {angle_deg:g}°"
    return Steer(target, dirv, np.vstack(rings), mode, desc, angle_deg,
                 depth_cm, head_R)


def solve_weights(elements, steer, mode):
    """Current weight per array element (largest = +-1)."""
    # E = -dA/dt, so to push E along `direction`, A must point against it
    t = np.array([-(a_field(c, steer.target[None])[0] @ steer.direction)
                  for c in elements])
    n = len(t)
    if mode == "focal" and n > 2:
        G = np.column_stack([a_field(c, steer.penalty).ravel()
                             for c in elements])
        M = G.T @ G
        lam = 1e-2 * np.trace(M) / n
        w = np.linalg.solve(M + lam * np.eye(n), t)
    else:
        w = t.copy()
    return w / np.abs(w).max()


def build_coil(cfg_name: str, geom: WingGeometry, steer: Steer | None = None,
               head_R: float | None = None) -> Coil:
    cfg = CONFIGS[cfg_name]
    g = geom
    if "geom" in cfg:
        o = cfg["geom"]
        g = replace(geom, r_in=o["r_in"] / 100, r_out=o["r_out"] / 100,
                    turns=o["turns"],
                    n_per_turn=o.get("n_per_turn", geom.n_per_turn))
    kind, label = cfg["kind"], cfg["title"]

    if kind == "single":
        wings = [make_wing(g, +1, cfg["pol"][0])]

    elif kind == "figure8":
        if cfg.get("overlap"):
            wings = figure8_wings(g, cfg["pol"],
                                  offset=g.r_out - g.overlap / 2,
                                  dz_right=g.overlap_dz)
        else:
            wings = figure8_wings(g, cfg["pol"])

    elif kind == "double_cone":
        if head_R is None:
            raise ValueError("double_cone needs a curved head: run it with "
                             "tms_coil_3d.py (--head sphere)")
        a = np.radians((180 - g.cone_angle) / 2)   # tilt of each wing
        # each wing plane is tangent to a sphere of radius head_R+clearance;
        # the junction ridge sits above the top of the head
        Rc = head_R + g.cone_clearance
        h = Rc / np.cos(a) - head_R
        s = g.r_out + g.gap / 2
        zc = h - s * np.sin(a)
        wings = [make_wing(g, +1, R=rot_y(-a), t=(-s * np.cos(a), 0, zc)),
                 make_wing(g, -1, R=rot_y(a), t=(s * np.cos(a), 0, zc))]
        label += f" ({g.cone_angle:g}° between wings)"

    elif kind in ("array_orient", "array_grid"):
        if steer is None:
            steer = make_steer(0, 0, 90, 2.0, "matched", head_R)
        if kind == "array_orient":
            # orientation steering is about the centre spot
            steer = make_steer(0, 0, steer.angle, steer.depth_cm, "matched",
                               steer.head_R)
            elements = [figure8_wings(g),
                        figure8_wings(g, R=rot_z(np.pi / 2),
                                      t=(0, 0, g.overlap_dz))]
            mode = "matched"
        else:
            p = 2 * g.r_out + g.gap
            elements = [[make_wing(g, +1, t=(i * p, j * p, 0))]
                        for j in (-1, 0, 1) for i in (-1, 0, 1)]
            mode = steer.mode
        w = solve_weights([coil_from_wings("", e) for e in elements],
                          steer, mode)
        wings = []
        for e, wi in zip(elements, w):
            for wing in e:
                wing["weight"] = wi
                wing["label"] = f"{wi:+.2f}"
                wings.append(wing)
        label += "\n" + steer.desc + (f", {mode}" if kind == "array_grid"
                                      else "")
    else:
        raise ValueError(kind)

    return coil_from_wings(label, wings)


def footprint(coils):
    """Largest |x| and |y| reached by any winding [m]."""
    pts = np.vstack([c.a for c in coils])
    return np.abs(pts[:, 0]).max(), np.abs(pts[:, 1]).max()


# --------------------------------------------------------------------------
# Field solvers (vectorised, chunked to bound memory)
# --------------------------------------------------------------------------
def b_field(coil: Coil, P: np.ndarray, current: float = 1.0,
            budget: int = 1_000_000) -> np.ndarray:
    """Exact Biot-Savart B [T] of all straight segments at points P (N,3)."""
    K = len(coil.a)
    step = max(1, budget // K)
    out = np.empty((len(P), 3))
    for s in range(0, len(P), step):
        p = P[s:s + step, None, :]
        r1 = p - coil.a[None]
        r2 = p - coil.b[None]
        n1 = np.linalg.norm(r1, axis=-1)
        n2 = np.linalg.norm(r2, axis=-1)
        den = n1 * n2 * (n1 * n2 + np.einsum("ijk,ijk->ij", r1, r2))
        f = coil.sign * (n1 + n2) / np.where(den > 1e-30, den, np.inf)
        out[s:s + step] = np.einsum("ij,ijk->ik", f, np.cross(r1, r2))
    return out * MU0 * current / (4 * np.pi)


def a_field(coil: Coil, P: np.ndarray, current: float = 1.0,
            budget: int = 1_000_000) -> np.ndarray:
    """Exact vector potential A [T*m] of all straight segments at P (N,3)."""
    d = coil.b - coil.a
    L = np.linalg.norm(d, axis=-1)
    u = d / L[:, None]
    K = len(coil.a)
    step = max(1, budget // K)
    out = np.empty((len(P), 3))
    for s in range(0, len(P), step):
        p = P[s:s + step, None, :]
        n1 = np.linalg.norm(p - coil.a[None], axis=-1)
        n2 = np.linalg.norm(p - coil.b[None], axis=-1)
        s12 = n1 + n2
        ln = np.log((s12 + L) / np.maximum(s12 - L, 1e-15))
        out[s:s + step] = (ln * coil.sign) @ u
    return out * MU0 * current / (4 * np.pi)


# --------------------------------------------------------------------------
# Sampling planes
# --------------------------------------------------------------------------
@dataclass
class Plane:
    u: np.ndarray        # 1-D horizontal axis [m]
    v: np.ndarray        # 1-D vertical axis [m]
    pts: np.ndarray      # (N,3)
    shape: tuple


def side_plane(xlim, zlim, nx, nz) -> Plane:
    """Vertical xz slice through y = 0 (looking along +y)."""
    x = np.linspace(*xlim, nx)
    z = np.linspace(*zlim, nz)
    X, Z = np.meshgrid(x, z)
    pts = np.column_stack([X.ravel(), np.zeros(X.size), Z.ravel()])
    return Plane(x, z, pts, X.shape)


def depth_plane(xlim, ylim, nx, ny, depth) -> Plane:
    """Horizontal xy slice at z = -depth (inside the head)."""
    x = np.linspace(*xlim, nx)
    y = np.linspace(*ylim, ny)
    X, Y = np.meshgrid(x, y)
    pts = np.column_stack([X.ravel(), Y.ravel(), np.full(X.size, -depth)])
    return Plane(x, y, pts, X.shape)


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------
def analyse(coil, args, side, plane, decay_depths, coarse):
    I = args.current
    didt = args.didt * 1e6  # A/us -> A/s
    Bs = b_field(coil, side.pts, I).reshape(side.shape + (3,))
    Bd = b_field(coil, plane.pts, I).reshape(plane.shape + (3,))
    Ed = (-didt * a_field(coil, plane.pts)).reshape(plane.shape + (3,))
    Emag = np.linalg.norm(Ed, axis=-1)

    decay = []
    for d in decay_depths:
        P = coarse.pts.copy()
        P[:, 2] = -d
        decay.append(np.linalg.norm(didt * a_field(coil, P), axis=-1).max())

    dx = (plane.u[1] - plane.u[0]) * 100
    dy = (plane.v[1] - plane.v[0]) * 100
    j, i = np.unravel_index(np.argmax(Emag), Emag.shape)
    jc = np.argmin(np.abs(plane.v))
    ic = np.argmin(np.abs(plane.u))
    metrics = dict(
        E_peak=Emag.max(),
        E_peak_xy=(plane.u[i] * 100, plane.v[j] * 100),
        E_center=Emag[jc, ic],
        half_area=np.count_nonzero(Emag >= 0.5 * Emag.max()) * dx * dy,
        B_peak=np.linalg.norm(Bd, axis=-1).max(),
        Bz_center=Bd[jc, ic, 2],
    )
    return dict(coil=coil, Bs=Bs, Bs_mag=np.linalg.norm(Bs, axis=-1),
                Bd=Bd, Ed=Ed, Emag=Emag, decay=np.array(decay),
                metrics=metrics)


# --------------------------------------------------------------------------
# Drawing helpers
# --------------------------------------------------------------------------
def draw_coil_top(ax, coil, geom=None, arrows=True, lw=0.7, alpha=1.0,
                  mono=None, labels=True):
    for w in coil.wings:
        sgn = w["sign"]
        col = mono or (CCW_COLOR if sgn > 0 else CW_COLOR)
        p = w["pts"] * 100
        width = lw * (0.4 + 0.8 * min(abs(sgn), 1.0))
        ax.plot(p[:, 0], p[:, 1], color=col, lw=width, alpha=alpha)
        cx, cy = np.asarray(w["center"])[:2] * 100
        if arrows and abs(sgn) > 0.05:
            n = w["n_per_turn"]
            for k in np.arange(len(p) - 1 - n, len(p) - 1, max(1, n // 8)):
                d = (p[k + 1] - p[k])[:2] * np.sign(sgn)
                if np.linalg.norm(d) == 0:
                    continue
                d /= np.linalg.norm(d)
                base = np.array([cx, cy]) + (p[k, :2] - [cx, cy]) * 1.13
                ax.annotate("", xy=base + d * 1.0, xytext=base,
                            arrowprops=dict(arrowstyle="-|>", color=col,
                                            lw=1.2, mutation_scale=11))
        if labels:
            if w.get("label"):
                txt = w["label"]
            else:
                txt = "CCW" if sgn > 0 else "CW"
                if len(coil.wings) > 1:
                    txt += "\n(reversed)" if w["pol"] < 0 else "\n(normal)"
            ax.text(cx, cy, txt, ha="center", va="center", fontsize=8,
                    color=col, fontweight="bold")


def draw_coil_side(ax, coil, geom=None):
    """Where the windings cross the y = 0 slice, and their current direction
    (x = into the page, o = out of the page)."""
    for w in coil.wings:
        if w["sign"] == 0:
            continue
        p = w["pts"]
        y0, y1 = p[:-1, 1], p[1:, 1]
        idx = np.where((y0 < 0) != (y1 < 0))[0]
        if idx.size == 0:
            continue
        f = y0[idx] / (y0[idx] - y1[idx])
        cr = (p[idx] + f[:, None] * (p[idx + 1] - p[idx])) * 100
        into = (y1[idx] - y0[idx]) * w["sign"] > 0
        ax.scatter(cr[into, 0], cr[into, 2], marker="x", s=12, c="cyan",
                   lw=0.9, zorder=5)
        ax.scatter(cr[~into, 0], cr[~into, 2], marker="o", s=10,
                   facecolors="none", edgecolors="lime", lw=0.9, zorder=5)


# --------------------------------------------------------------------------
# Figures
# --------------------------------------------------------------------------
def plot_comparison(results, args, geom, side, plane, path):
    import matplotlib.pyplot as plt
    from matplotlib.colors import LogNorm

    n = len(results)
    fig, axes = plt.subplots(n, 4, figsize=(21, 4.3 * n),
                             constrained_layout=True, squeeze=False)

    Bs_max = max(np.percentile(r["Bs_mag"], 99.0) for r in results) * 1e3
    Bz_max = max(np.abs(r["Bd"][..., 2]).max() for r in results) * 1e3
    E_max = max(r["Emag"].max() for r in results)

    xs, zs = side.u * 100, side.v * 100
    xp, yp = plane.u * 100, plane.v * 100
    k = max(1, len(xp) // 20)
    spacing = (xp[k] - xp[0])
    depth_cm = args.depth

    for row, r in zip(axes, results):
        coil = r["coil"]
        a0, a1, a2, a3 = row

        draw_coil_top(a0, coil)
        a0.set_title(coil.label, loc="left", fontweight="bold", fontsize=10)
        a0.set_xlim(xp[0], xp[-1]); a0.set_ylim(yp[0], yp[-1])

        Bm = np.clip(r["Bs_mag"] * 1e3, Bs_max * 1e-3, None)
        im1 = a1.pcolormesh(xs, zs, Bm, cmap="magma", shading="auto",
                            norm=LogNorm(vmin=Bs_max * 1e-3, vmax=Bs_max))
        a1.streamplot(xs, zs, r["Bs"][..., 0], r["Bs"][..., 2], color="w",
                      linewidth=0.5, density=1.4, arrowsize=0.7)
        draw_coil_side(a1, coil)
        a1.axhline(-depth_cm, color="cyan", ls="--", lw=1)
        a1.text(xs[0] + 0.5, -depth_cm - 0.7, f"{depth_cm:g} cm depth",
                color="cyan", fontsize=8)
        a1.set_xlim(xs[0], xs[-1]); a1.set_ylim(zs[0], zs[-1])

        Bz = r["Bd"][..., 2] * 1e3
        im2 = a2.pcolormesh(xp, yp, Bz, cmap="RdBu_r", shading="auto",
                            vmin=-Bz_max, vmax=Bz_max)
        if Bz.min() < 0 < Bz.max():
            a2.contour(xp, yp, Bz, levels=[0], colors="k", linewidths=0.8)
        draw_coil_top(a2, coil, arrows=False, lw=0.4, alpha=0.35,
                      mono="0.2", labels=False)

        E = r["Emag"]
        im3 = a3.pcolormesh(xp, yp, E, cmap="inferno", shading="auto",
                            vmin=0, vmax=E_max)
        a3.contour(xp, yp, E, levels=[0.5 * E.max()], colors="w",
                   linestyles="--", linewidths=1)
        a3.quiver(xp[::k], yp[::k], r["Ed"][::k, ::k, 0],
                  r["Ed"][::k, ::k, 1], color="w", angles="xy",
                  scale_units="xy", scale=E_max / (1.3 * spacing),
                  width=0.004, alpha=0.9)
        draw_coil_top(a3, coil, arrows=False, lw=0.4, alpha=0.3,
                      mono="0.8", labels=False)
        m = r["metrics"]
        a3.text(0.02, 0.97,
                f"peak |E| = {m['E_peak']:.0f} V/m\n"
                f"at ({m['E_peak_xy'][0]:+.1f}, {m['E_peak_xy'][1]:+.1f}) cm\n"
                f"half-max area = {m['half_area']:.0f} cm²",
                transform=a3.transAxes, va="top", fontsize=8, color="w",
                bbox=dict(fc="k", alpha=0.5, lw=0))

        for ax in row:
            ax.set_aspect("equal")
            ax.set_xlabel("x [cm]")
        a0.set_ylabel("y [cm]"); a1.set_ylabel("z [cm]")
        a2.set_ylabel("y [cm]"); a3.set_ylabel("y [cm]")

    top = axes[0]
    top[1].set_title("Side view (y = 0): |B| and field lines\n"
                     "x = current into page, o = out of page")
    top[2].set_title(f"Bz at {depth_cm:g} cm depth (red = up, blue = down)")
    top[3].set_title(f"Induced |E| at {depth_cm:g} cm depth "
                     "(arrows = E direction)")

    fig.colorbar(im1, ax=axes[:, 1], location="bottom", shrink=0.85,
                 label=f"|B| [mT] at I = {args.current:g} A (log scale)")
    fig.colorbar(im2, ax=axes[:, 2], location="bottom", shrink=0.85,
                 label=f"Bz [mT] at I = {args.current:g} A")
    fig.colorbar(im3, ax=axes[:, 3], location="bottom", shrink=0.85,
                 label=f"|E| [V/m] at dI/dt = {args.didt:g} A/µs")
    fig.suptitle("TMS coil fields: comparison (shared colour scales, "
                 "same peak current per coil)", fontsize=14,
                 fontweight="bold")
    fig.savefig(path, dpi=130)
    return fig


def plot_profiles(results, args, plane, decay_depths, path):
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(2, 2, figsize=(13, 9), constrained_layout=True)
    xp = plane.u * 100
    j0 = np.argmin(np.abs(plane.v))
    styles = itertools.cycle(["-", "--", "-.", ":", (0, (5, 1, 1, 1))])
    for r, ls in zip(results, styles):
        lab = r["coil"].label.split("\n")[0]
        ax[0, 0].plot(xp, r["Emag"][j0], ls=ls, lw=2, label=lab)
        ax[0, 1].plot(xp, r["Ed"][j0, :, 1], ls=ls, lw=2, label=lab)
        ax[1, 0].plot(xp, r["Bd"][j0, :, 2] * 1e3, ls=ls, lw=2, label=lab)
        ax[1, 1].semilogy(decay_depths * 100, r["decay"], ls=ls, lw=2,
                          label=lab)

    d = args.depth
    ax[0, 0].set(title=f"|E| along x-axis (y = 0) at {d:g} cm depth",
                 xlabel="x [cm]", ylabel="|E| [V/m]")
    ax[0, 1].set(title=f"Signed Ey along x-axis at {d:g} cm "
                       "(sign = direction of induced current)",
                 xlabel="x [cm]", ylabel="Ey [V/m]")
    ax[1, 0].set(title=f"Bz along x-axis at {d:g} cm depth",
                 xlabel="x [cm]", ylabel="Bz [mT]")
    ax[1, 1].set(title="Peak |E| vs depth below coil",
                 xlabel="depth [cm]", ylabel="peak |E| [V/m]")
    for a in ax.ravel():
        a.grid(alpha=0.3)
        a.axhline(0, color="k", lw=0.5)
    ax[0, 0].legend(fontsize=9)
    fig.suptitle(f"Line profiles (I = {args.current:g} A, "
                 f"dI/dt = {args.didt:g} A/µs)", fontweight="bold")
    fig.savefig(path, dpi=130)
    return fig


def print_summary(results, args):
    print(f"\nSummary at {args.depth:g} cm depth  "
          f"(I = {args.current:g} A, dI/dt = {args.didt:g} A/µs)")
    hdr = (f"{'configuration':34s} {'peak|E|':>8s} {'at (x,y) cm':>14s} "
           f"{'|E|ctr':>7s} {'½-max area':>11s} {'peak|B|':>8s} {'Bz ctr':>8s}")
    print(hdr)
    print(f"{'':34s} {'V/m':>8s} {'':>14s} {'V/m':>7s} {'cm²':>11s} "
          f"{'mT':>8s} {'mT':>8s}")
    print("-" * len(hdr))
    for r in results:
        m = r["metrics"]
        xy = f"({m['E_peak_xy'][0]:+.1f},{m['E_peak_xy'][1]:+.1f})"
        lab = r["coil"].label.split("\n")[0]
        print(f"{lab:34s} {m['E_peak']:8.0f} {xy:>14s} "
              f"{m['E_center']:7.0f} {m['half_area']:11.1f} "
              f"{m['B_peak'] * 1e3:8.0f} {m['Bz_center'] * 1e3:+8.0f}")
    print()


# --------------------------------------------------------------------------
# CLI helpers (shared with tms_coil_3d.py)
# --------------------------------------------------------------------------
def add_geometry_args(p):
    p.add_argument("--r-in", type=float, default=2.8,
                   help="default inner radius [cm]")
    p.add_argument("--r-out", type=float, default=4.35,
                   help="default outer radius [cm]")
    p.add_argument("--turns", type=float, default=9,
                   help="default turns per wing")
    p.add_argument("--gap", type=float, default=0.2,
                   help="gap between figure-8 wings [cm]")
    p.add_argument("--overlap", type=float, default=2.0,
                   help="wing overlap for fig8_overlap [cm]")
    p.add_argument("--overlap-dz", type=float, default=0.6,
                   help="height of stacked wings / array layers [cm]")
    p.add_argument("--cone-angle", type=float, default=100,
                   help="angle between double-cone wings [deg] "
                        "(180 = flat figure-8)")


def add_steer_args(p):
    p.add_argument("--steer-x", type=float, default=2.0,
                   help="steering target x [cm] (array_grid)")
    p.add_argument("--steer-y", type=float, default=1.0,
                   help="steering target y [cm] (array_grid)")
    p.add_argument("--steer-angle", type=float, default=45,
                   help="desired E direction at the target [deg, 0 = +x]")
    p.add_argument("--array-mode", choices=["focal", "matched"],
                   default="focal",
                   help="how array_grid weights are solved")


def geometry_from_args(args):
    return WingGeometry(r_in=args.r_in / 100, r_out=args.r_out / 100,
                        turns=args.turns, gap=args.gap / 100,
                        overlap=args.overlap / 100,
                        overlap_dz=args.overlap_dz / 100,
                        cone_angle=args.cone_angle)


def main():
    p = argparse.ArgumentParser(
        description="Visualize B and induced E fields of TMS coils.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--configs", nargs="+", default=DEFAULT_CONFIGS,
                   help=f"any of {list(CONFIGS)} or a preset "
                        f"{list(PRESETS)}")
    p.add_argument("--depth", type=float, default=2.0,
                   help="evaluation depth below coil [cm] (cortex ~1.5-2.5)")
    p.add_argument("--current", type=float, default=5000,
                   help="peak coil current for B maps [A]")
    p.add_argument("--didt", type=float, default=100,
                   help="rate of change of current for E maps [A/µs]")
    add_geometry_args(p)
    add_steer_args(p)
    p.add_argument("--res", type=int, default=181,
                   help="grid points across x (higher = sharper, slower)")
    p.add_argument("--out", default="tms_output", help="output folder")
    p.add_argument("--no-show", action="store_true",
                   help="save figures without opening windows")
    args = p.parse_args()

    if args.no_show:
        import matplotlib
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    names = expand_configs(args.configs)
    bad = [n for n in names if n not in CONFIGS]
    if bad:
        p.error(f"unknown config(s) {bad}; choose from {list(CONFIGS)} "
                f"or {list(PRESETS)}")
    for n in [n for n in names if n in NEEDS_CURVED_HEAD]:
        print(f"skipping {n}: it wraps around the head, so it needs the "
              f"curved head in tms_coil_3d.py")
    names = [n for n in names if n not in NEEDS_CURVED_HEAD]
    if not names:
        p.error("nothing left to plot")

    geom = geometry_from_args(args)
    steer = make_steer(args.steer_x, args.steer_y, args.steer_angle,
                       args.depth, args.array_mode)
    coils = [build_coil(n, geom, steer) for n in names]

    fx, fy = footprint(coils)
    half_w, half_h = fx + 0.03, fy + 0.04
    nx = args.res
    ny = int(nx * half_h / half_w) | 1          # odd -> includes y = 0
    side = side_plane((-half_w, half_w), (-0.08, 0.03), nx,
                      int(nx * 0.11 / (2 * half_w)) + 1)
    plane = depth_plane((-half_w, half_w), (-half_h, half_h), nx, ny,
                        args.depth / 100)
    coarse = depth_plane((-half_w, half_w), (-half_h, half_h), 49, 37, 0.0)
    decay_depths = np.linspace(0.005, 0.06, 23)

    results = []
    for coil in coils:
        print(f"computing {coil.label.splitlines()[0]} ...", flush=True)
        results.append(analyse(coil, args, side, plane, decay_depths, coarse))

    os.makedirs(args.out, exist_ok=True)
    f1 = os.path.join(args.out, "coil_comparison.png")
    f2 = os.path.join(args.out, "coil_profiles.png")
    plot_comparison(results, args, geom, side, plane, f1)
    plot_profiles(results, args, plane, decay_depths, f2)
    print_summary(results, args)
    print(f"saved: {f1}\n       {f2}")
    if not args.no_show:
        plt.show()


if __name__ == "__main__":
    main()
