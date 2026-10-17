#!/usr/bin/env python3
"""
tms_coil_fields.py
==================

Visualize the magnetic field (B) and the induced electric field (E) of TMS
coils:

  * single flat (planar, spiral-wound) coils, normal and large
  * figure-8 (double planar) coils: standard, small, large, and with
    OVERLAPPING wings
  * figure-8 wiring variants: reverse one wing or both

Physics model
-------------
* Each wing is a flat spiral built from short straight wire segments. The
  head is below the coil (z < 0).
* B is computed with the exact Biot-Savart solution for finite straight
  segments.
* The magnetic vector potential A is computed with the exact closed-form
  solution for straight segments. The induced electric field is
        E = -dA/dt = -(A / I) * dI/dt
  This is the "primary" induced field in free space. It ignores the
  secondary field from charge build-up at tissue boundaries (scalp, skull,
  CSF), which in a real head removes the component normal to the surface
  and changes magnitudes by roughly 10-30 %. Patterns and trends are right;
  absolute numbers are approximate.
* Lead wires / return paths are not modelled.

Sign conventions
----------------
* "CCW" / "CW" = current circulation viewed from ABOVE the coil (+z).
* Figure-8 "standard" wiring: left wing CCW, right wing CW, so the currents
  under the junction run in the SAME direction (+y) and their fields add.
* Polarity +1 = wing wired normally, -1 = that wing's leads swapped.
* Overlapping figure-8: the right wing sits on top of the left one
  (raised by --overlap-dz) and the wings overlap by --overlap.

Usage examples
--------------
    python tms_coil_fields.py                       # wiring comparison
    python tms_coil_fields.py --configs designs     # coil-design comparison
    python tms_coil_fields.py --configs fig8 fig8_overlap --overlap 3
    python tms_coil_fields.py --configs all --no-show

Presets for --configs: wiring, designs, all

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
}
PRESETS = {
    "wiring":  ["single", "fig8", "fig8_rev_right", "fig8_rev_both"],
    "designs": ["single", "single_large", "fig8", "fig8_small",
                "fig8_large", "fig8_overlap"],
    "all":     list(CONFIGS),
}
DEFAULT_CONFIGS = PRESETS["wiring"]

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
    r_in: float = 0.028       # inner winding radius
    r_out: float = 0.0435     # outer winding radius
    turns: float = 9.0
    gap: float = 0.002        # gap between figure-8 wings
    overlap: float = 0.02     # wing overlap for fig8_overlap
    overlap_dz: float = 0.006 # height of the upper wing for fig8_overlap
    n_per_turn: int = 72      # straight segments per turn (5 deg each)


@dataclass
class Coil:
    label: str
    a: np.ndarray        # (K,3) segment start points
    b: np.ndarray        # (K,3) segment end points
    sign: np.ndarray     # (K,) current multiplier (+1/-1) per segment
    wings: list          # per-wing info for drawing


def spiral_polyline(cx, cy, cz, r_in, r_out, turns, n_per_turn):
    """Counter-clockwise (viewed from +z) Archimedean spiral, inner -> outer."""
    n = int(round(turns * n_per_turn))
    t = np.linspace(0.0, 2 * np.pi * turns, n + 1)
    r = r_in + (r_out - r_in) * t / t[-1]
    return np.column_stack([cx + r * np.cos(t), cy + r * np.sin(t),
                            np.full_like(t, cz)])


def build_coil(cfg_name: str, geom: WingGeometry) -> Coil:
    cfg = CONFIGS[cfg_name]
    g = geom
    if "geom" in cfg:
        o = cfg["geom"]
        g = replace(geom, r_in=o["r_in"] / 100, r_out=o["r_out"] / 100,
                    turns=o["turns"])

    if cfg["kind"] == "single":
        placements, base_sense = [(0.0, 0.0, 0.0)], [+1]
    else:
        if cfg.get("overlap"):
            c = g.r_out - g.overlap / 2
            placements = [(-c, 0.0, 0.0), (c, 0.0, g.overlap_dz)]
        else:
            c = g.r_out + g.gap / 2
            placements = [(-c, 0.0, 0.0), (c, 0.0, 0.0)]
        # standard figure-8: left CCW, right CW -> junction currents add
        base_sense = [+1, -1]

    A, B, S, wings = [], [], [], []
    for (cx, cy, cz), sense, pol in zip(placements, base_sense, cfg["pol"]):
        pts = spiral_polyline(cx, cy, cz, g.r_in, g.r_out, g.turns,
                              g.n_per_turn)
        s = sense * pol
        A.append(pts[:-1])
        B.append(pts[1:])
        S.append(np.full(len(pts) - 1, float(s)))
        wings.append(dict(pts=pts, sign=s, pol=pol, center=(cx, cy), cz=cz,
                          r_in=g.r_in, r_out=g.r_out,
                          n_per_turn=g.n_per_turn))
    return Coil(cfg["title"], np.vstack(A), np.vstack(B), np.concatenate(S),
                wings)


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

    # peak |E| versus depth (coarse grid, fast)
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
        col = mono or (CCW_COLOR if w["sign"] > 0 else CW_COLOR)
        p = w["pts"] * 100
        ax.plot(p[:, 0], p[:, 1], color=col, lw=lw, alpha=alpha)
        cx, cy = np.array(w["center"]) * 100
        if arrows:
            n = w["n_per_turn"]
            for k in np.arange(len(p) - 1 - n, len(p) - 1, n // 8):
                d = (p[k + 1] - p[k])[:2] * w["sign"]
                d /= np.linalg.norm(d)
                base = np.array([cx, cy]) + (p[k, :2] - [cx, cy]) * 1.13
                ax.annotate("", xy=base + d * 1.3, xytext=base,
                            arrowprops=dict(arrowstyle="-|>", color=col,
                                            lw=1.4, mutation_scale=13))
        if labels:
            txt = ("CCW" if w["sign"] > 0 else "CW")
            if len(coil.wings) > 1:
                txt += "\n(reversed)" if w["pol"] < 0 else "\n(normal)"
            ax.text(cx, cy, txt, ha="center", va="center", fontsize=9,
                    color=col, fontweight="bold")


def draw_coil_side(ax, coil, geom=None):
    """Winding cross-sections in the xz slice, with current direction."""
    for w in coil.wings:
        cx = w["center"][0]
        zc = w["cz"] * 100
        rm = 0.5 * (w["r_in"] + w["r_out"])
        for side in (+1, -1):
            x0 = (cx + side * w["r_in"]) * 100
            x1 = (cx + side * w["r_out"]) * 100
            ax.plot([x0, x1], [zc, zc], color="0.85", lw=5,
                    solid_capstyle="butt", zorder=4)
            # CCW current at +x side of a loop flows +y (into the page here)
            into = (side * w["sign"]) > 0
            xm = (cx + side * rm) * 100
            ax.scatter([xm], [zc + 0.9], s=90, facecolor="white",
                       edgecolor="k", zorder=5, lw=1)
            ax.scatter([xm], [zc + 0.9], s=40 if into else 12,
                       marker="x" if into else "o", color="k", zorder=6,
                       lw=1.4)


# --------------------------------------------------------------------------
# Figures
# --------------------------------------------------------------------------
def plot_comparison(results, args, geom, side, plane, path):
    import matplotlib.pyplot as plt
    from matplotlib.colors import LogNorm

    n = len(results)
    fig, axes = plt.subplots(n, 4, figsize=(21, 4.3 * n),
                             constrained_layout=True, squeeze=False)

    # Shared colour scales so rows are directly comparable
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

        # (1) geometry
        draw_coil_top(a0, coil)
        a0.set_title(coil.label, loc="left", fontweight="bold")
        a0.set_xlim(xp[0], xp[-1]); a0.set_ylim(yp[0], yp[-1])

        # (2) side view |B| + field lines
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

        # (3) Bz at depth
        Bz = r["Bd"][..., 2] * 1e3
        im2 = a2.pcolormesh(xp, yp, Bz, cmap="RdBu_r", shading="auto",
                            vmin=-Bz_max, vmax=Bz_max)
        if Bz.min() < 0 < Bz.max():
            a2.contour(xp, yp, Bz, levels=[0], colors="k", linewidths=0.8)
        draw_coil_top(a2, coil, arrows=False, lw=0.4, alpha=0.35,
                      mono="0.2", labels=False)

        # (4) |E| at depth + direction arrows + half-max contour
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
                f"|E| at centre = {m['E_center']:.0f} V/m\n"
                f"half-max area = {m['half_area']:.0f} cm²",
                transform=a3.transAxes, va="top", fontsize=8, color="w",
                bbox=dict(fc="k", alpha=0.5, lw=0))

        for ax in row:
            ax.set_aspect("equal")
            ax.set_xlabel("x [cm]")
        a0.set_ylabel("y [cm]"); a1.set_ylabel("z [cm]")
        a2.set_ylabel("y [cm]"); a3.set_ylabel("y [cm]")

    top = axes[0]
    top[1].set_title("Side view (y = 0): |B| and field lines")
    top[2].set_title(f"Bz at {depth_cm:g} cm depth (red = up, blue = down)")
    top[3].set_title(f"Induced |E| at {depth_cm:g} cm depth (arrows = E direction)")
    top[0].set_title(results[0]["coil"].label + "\n(top view, arrows = current)",
                     loc="left", fontweight="bold")

    fig.colorbar(im1, ax=axes[:, 1], location="bottom", shrink=0.85,
                 label=f"|B| [mT] at I = {args.current:g} A (log scale)")
    fig.colorbar(im2, ax=axes[:, 2], location="bottom", shrink=0.85,
                 label=f"Bz [mT] at I = {args.current:g} A")
    fig.colorbar(im3, ax=axes[:, 3], location="bottom", shrink=0.85,
                 label=f"|E| [V/m] at dI/dt = {args.didt:g} A/µs")
    fig.suptitle("TMS coil fields: comparison (shared colour scales, "
                 "same current for every coil)", fontsize=14,
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
        lab = r["coil"].label
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
        print(f"{r['coil'].label:34s} {m['E_peak']:8.0f} {xy:>14s} "
              f"{m['E_center']:7.0f} {m['half_area']:11.1f} "
              f"{m['B_peak'] * 1e3:8.0f} {m['Bz_center'] * 1e3:+8.0f}")
    print()


# --------------------------------------------------------------------------
# CLI
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
                   help="height of the upper wing for fig8_overlap [cm]")


def geometry_from_args(args):
    return WingGeometry(r_in=args.r_in / 100, r_out=args.r_out / 100,
                        turns=args.turns, gap=args.gap / 100,
                        overlap=args.overlap / 100,
                        overlap_dz=args.overlap_dz / 100)


def main():
    p = argparse.ArgumentParser(
        description="Visualize B and induced E fields of planar and "
                    "figure-8 TMS coils.",
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

    geom = geometry_from_args(args)
    coils = [build_coil(n, geom) for n in names]

    # Field of view sized to the largest coil so all rows match
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
        print(f"computing {coil.label} ...", flush=True)
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
