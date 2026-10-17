#!/usr/bin/env python3
"""
tms_coil_3d.py
==============

3-D depth visualization of the induced E-field of TMS coils, plus the
depth-vs-focality trade-off (Deng et al., Brain Stimulation 2013).

Needs tms_coil_fields.py in the SAME folder (coil models + field solver).

Head model (--head)
  sphere (default)  spherical head, radius --head-radius, top of the scalp
                    at z = 0 directly under the coil centre. Depth is
                    measured inward from the scalp. Needed for double_cone.
  flat              flat head surface at z = 0 (as in tms_coil_fields.py)

Outputs (open in your browser, also saved as HTML):
  coil_3d.html             rotatable 3-D isosurfaces of |E| inside the head
  coil_depth_focality.html field decay with depth + d_1/2 vs S_1/2 plot

Normalisation (--normalize)
  matched (default)  each coil is scaled so its peak |E| at the target depth
                     equals --threshold (like dialling each coil to motor
                     threshold)
  none               every coil driven with the same dI/dt

Metrics (measured below the target depth)
  d_1/2  distance below the target at which peak |E| falls to half the
         target value (bigger = deeper)
  V_1/2  volume below the target where |E| >= half the target value
  S_1/2  V_1/2 / d_1/2, effective spread (smaller = more focal)

Usage
-----
    pip install numpy plotly
    python tms_coil_3d.py                               # coil designs
    python tms_coil_3d.py --configs advanced            # + double cone, arrays
    python tms_coil_3d.py --configs fig8 double_cone --cone-angle 120
    python tms_coil_3d.py --configs steering --steer-x 2 --steer-y 1 --steer-angle 90
    python tms_coil_3d.py --configs array_grid --array-mode matched

Same free-space caveat as tms_coil_fields.py: tissue boundaries are not
modelled, so absolute numbers are approximate; trends are right.
"""
from __future__ import annotations

import argparse
import math
import os

import numpy as np

from tms_coil_fields import (CONFIGS, PRESETS, CCW_COLOR, CW_COLOR,
                             NEEDS_CURVED_HEAD, a_field, add_geometry_args,
                             add_steer_args, build_coil, expand_configs,
                             footprint, geometry_from_args, make_steer)


# --------------------------------------------------------------------------
# Head geometry
# --------------------------------------------------------------------------
def head_depth(pts, args):
    """Depth below the scalp [m] (negative = outside the head)."""
    if args.head == "flat":
        return -pts[:, 2]
    R = args.head_radius / 100
    return R - np.linalg.norm(pts - np.array([0, 0, -R]), axis=1)


def target_points(args, half_w, half_h, n=80):
    d = args.depth / 100
    if args.head == "flat":
        X, Y = np.meshgrid(np.linspace(-half_w, half_w, n),
                           np.linspace(-half_h, half_h, n))
        return np.column_stack([X.ravel(), Y.ravel(), np.full(X.size, -d)])
    R = args.head_radius / 100
    th, ph = np.meshgrid(np.radians(np.linspace(0, 80, n // 2)),
                         np.radians(np.linspace(0, 360, n, endpoint=False)))
    r = R - d
    return np.column_stack([r * np.sin(th).ravel() * np.cos(ph).ravel(),
                            r * np.sin(th).ravel() * np.sin(ph).ravel(),
                            -R + r * np.cos(th).ravel()])


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------
def analyse(coil, args, pts, depth, voxel_cm3, target_pts):
    didt = args.didt * 1e6
    peak_target = np.linalg.norm(a_field(coil, target_pts), axis=-1).max() \
        * didt
    scale = args.threshold / peak_target if args.normalize == "matched" else 1.0
    E_ref = peak_target * scale

    inside = depth > 0
    E = np.zeros(len(pts))
    E[inside] = np.linalg.norm(a_field(coil, pts[inside]), axis=-1) \
        * didt * scale

    # peak |E| in depth bins
    dcm = depth * 100
    bw = args.bin_width
    edges = np.arange(0, args.max_depth + bw, bw)
    centers = 0.5 * (edges[:-1] + edges[1:])
    idx = np.digitize(dcm[inside], edges) - 1
    ok = (idx >= 0) & (idx < len(centers))
    layer_peak = np.zeros(len(centers))
    np.maximum.at(layer_peak, idx[ok], E[inside][ok])

    sel = (centers >= args.depth) & (layer_peak > 0)
    dz, lp = centers[sel], layer_peak[sel]
    hit = np.where(lp <= 0.5 * E_ref)[0]
    if hit.size == 0:
        d_half = float("nan")
    elif hit[0] == 0:
        d_half = max(dz[0] - args.depth, 0.0)
    else:
        i = hit[0]
        d_at = dz[i - 1] + (0.5 * E_ref - lp[i - 1]) * \
            (dz[i] - dz[i - 1]) / (lp[i] - lp[i - 1])
        d_half = d_at - args.depth

    below = dcm >= args.depth
    V_half = np.count_nonzero(below & (E >= 0.5 * E_ref)) * voxel_cm3
    S_half = V_half / d_half if d_half and d_half > 0 else float("nan")

    return dict(coil=coil, E=E, E_ref=E_ref, scale=scale,
                layer_peak=layer_peak, centers=centers,
                d_half=d_half, V_half=V_half, S_half=S_half)


# --------------------------------------------------------------------------
# Plots
# --------------------------------------------------------------------------
def coil_traces(coil, go):
    out = []
    for w in coil.wings:
        if w["sign"] == 0:
            continue
        p = w["pts"] * 100
        out.append(go.Scatter3d(
            x=p[:, 0], y=p[:, 1], z=p[:, 2] + 0.15, mode="lines",
            line=dict(color=CCW_COLOR if w["sign"] > 0 else CW_COLOR,
                      width=1.5 + 4 * min(abs(w["sign"]), 1.0)),
            showlegend=False, hoverinfo="skip"))
    return out


def head_traces(args, half_w, half_h, go):
    out = []
    d = args.depth
    if args.head == "flat":
        out.append(go.Surface(
            x=[-half_w * 100, half_w * 100], y=[-half_h * 100, half_h * 100],
            z=np.full((2, 2), -d), showscale=False, opacity=0.15,
            colorscale=[[0, "#4cc9f0"], [1, "#4cc9f0"]], hoverinfo="skip"))
        return out
    R = args.head_radius
    th, ph = np.meshgrid(np.radians(np.linspace(0, 70, 25)),
                         np.radians(np.linspace(0, 360, 49)))
    for r, col, op in ((R, "#bbbbbb", 0.07), (R - d, "#4cc9f0", 0.15)):
        out.append(go.Surface(
            x=r * np.sin(th) * np.cos(ph), y=r * np.sin(th) * np.sin(ph),
            z=-R + r * np.cos(th), showscale=False, opacity=op,
            colorscale=[[0, col], [1, col]], hoverinfo="skip"))
    return out


def plot_3d(results, args, pts, half_w, half_h, path):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    Xf, Yf, Zf = (np.round(pts[:, i] * 100, 2).astype(np.float32)
                  for i in range(3))
    R = (args.threshold if args.normalize == "matched"
         else max(r["E_ref"] for r in results))
    n = len(results)
    cols = min(n, 2)
    rows = math.ceil(n / cols)
    titles = []
    for r in results:
        name = r["coil"].label.replace("\n", "<br>")
        t = f"<b>{name}</b><br>d½ = {r['d_half']:.1f} cm, " \
            f"S½ = {r['S_half']:.0f} cm²"
        if args.normalize == "matched":
            t += f", intensity ×{r['scale']:.2f}"
        titles.append(t)
    fig = make_subplots(rows=rows, cols=cols, subplot_titles=titles,
                        specs=[[{"type": "scene"}] * cols] * rows,
                        horizontal_spacing=0.02, vertical_spacing=0.07)

    cbar = dict(title=dict(text="|E| [V/m]", side="right"), len=0.6)
    for k, r in enumerate(results):
        row, col = k // cols + 1, k % cols + 1
        val = np.round(r["E"], 1).astype(np.float32)
        common = dict(x=Xf, y=Yf, z=Zf, value=val, colorscale="Inferno",
                      cmin=0.25 * R if args.style == "iso" else 0.1 * R,
                      cmax=R if args.style == "iso" else 1.5 * R,
                      showscale=(k == 0), colorbar=cbar,
                      caps=dict(x_show=False, y_show=False, z_show=False),
                      hovertemplate="x %{x} cm<br>y %{y} cm<br>z %{z} cm"
                                    "<br>|E| %{value:.0f} V/m<extra></extra>")
        if args.style == "iso":
            tr = go.Isosurface(isomin=0.25 * R, isomax=R, surface_count=4,
                               opacity=0.3, **common)
        else:
            tr = go.Volume(isomin=0.1 * R, isomax=1.5 * R, surface_count=18,
                           opacity=0.08, **common)
        fig.add_trace(tr, row=row, col=col)
        for t in coil_traces(r["coil"], go) + head_traces(args, half_w,
                                                          half_h, go):
            fig.add_trace(t, row=row, col=col)

    levels = "25 / 50 / 75 / 100 %" if args.style == "iso" else "10-150 %"
    ref = (f"threshold {args.threshold:g} V/m matched at {args.depth:g} cm"
           if args.normalize == "matched" else
           f"same dI/dt = {args.didt:g} A/µs")
    surf = ("blue shell = target depth, grey = scalp"
            if args.head == "sphere" else "blue plane = target depth")
    fig.update_scenes(
        aspectmode="data",
        xaxis_title="x [cm]", yaxis_title="y [cm]", zaxis_title="z [cm]",
        camera=dict(eye=dict(x=1.3, y=-1.6, z=0.8)))
    fig.update_layout(
        title=f"Induced |E| inside the head: shells at {levels} of "
              f"{R:.0f} V/m ({ref}); {surf}",
        height=600 * rows, width=1500, margin=dict(l=0, r=0, t=110, b=0))
    fig.write_html(path, include_plotlyjs=True)
    return fig


def plot_depth(results, args, path):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    fig = make_subplots(rows=1, cols=2, subplot_titles=(
        "Peak |E| at each depth (relative to the target-depth value)",
        "Depth vs focality trade-off"))
    palette = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e",
               "#8c564b", "#e377c2", "#17becf"]
    for k, r in enumerate(results):
        c = palette[k % len(palette)]
        lab = r["coil"].label.split("\n")[0]
        m = r["layer_peak"] > 0
        fig.add_trace(go.Scatter(x=r["centers"][m],
                                 y=r["layer_peak"][m] / r["E_ref"],
                                 name=lab, line=dict(color=c, width=2.5)),
                      row=1, col=1)
        fig.add_trace(go.Scatter(x=[r["d_half"]], y=[r["S_half"]],
                                 mode="markers+text", text=[lab],
                                 textposition="top center", showlegend=False,
                                 marker=dict(size=14, color=c)),
                      row=1, col=2)
    fig.add_vline(x=args.depth, line_dash="dash", line_color="gray",
                  annotation_text="target depth", row=1, col=1)
    fig.add_hline(y=0.5, line_dash="dot", line_color="gray", row=1, col=1)
    fig.update_yaxes(type="log", title="|E| / |E| at target", row=1, col=1)
    fig.update_xaxes(title="depth below scalp [cm]", row=1, col=1)
    fig.update_xaxes(title="d½ [cm] (deeper →)", row=1, col=2)
    fig.update_yaxes(title="S½ [cm²] (more spread ↑, more focal ↓)",
                     row=1, col=2)
    fig.update_layout(height=550, width=1400,
                      title="Depth penetration and focality "
                            "(bottom-right of the right panel would be ideal)")
    fig.write_html(path, include_plotlyjs=True)
    return fig


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(
        description="3-D depth visualization of TMS coil E-fields.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--configs", nargs="+", default=["designs"],
                   help=f"any of {list(CONFIGS)} or a preset "
                        f"{list(PRESETS)}")
    p.add_argument("--large-single", action="store_true",
                   help="also add the large round coil (single_large)")
    p.add_argument("--head", choices=["sphere", "flat"], default="sphere")
    p.add_argument("--head-radius", type=float, default=8.5,
                   help="scalp radius of the spherical head [cm]")
    p.add_argument("--depth", type=float, default=2.0,
                   help="target (cortex) depth below the scalp [cm]")
    p.add_argument("--threshold", type=float, default=100,
                   help="target |E| for matched normalisation [V/m]")
    p.add_argument("--normalize", choices=["matched", "none"],
                   default="matched")
    p.add_argument("--didt", type=float, default=100,
                   help="coil dI/dt [A/µs] (used directly if --normalize none)")
    p.add_argument("--style", choices=["iso", "volume"], default="iso",
                   help="iso = nested shells, volume = cloud-like heat map")
    add_geometry_args(p)
    add_steer_args(p)
    p.add_argument("--max-depth", type=float, default=7.0,
                   help="deepest point of the 3-D grid [cm]")
    p.add_argument("--res3d", type=int, default=51,
                   help="3-D grid points across x (higher = smoother, slower, "
                        "bigger HTML)")
    p.add_argument("--bin-width", type=float, default=0.25,
                   help="depth bin width for the decay curves [cm]")
    p.add_argument("--out", default="tms_output")
    p.add_argument("--no-show", action="store_true",
                   help="only save HTML, don't open the browser")
    args = p.parse_args()

    names = expand_configs(args.configs +
                           (["single_large"] if args.large_single else []))
    bad = [n for n in names if n not in CONFIGS]
    if bad:
        p.error(f"unknown config(s) {bad}; choose from {list(CONFIGS)} "
                f"or {list(PRESETS)}")
    if args.head == "flat" and any(n in NEEDS_CURVED_HEAD for n in names):
        p.error("double_cone needs --head sphere")

    head_R = args.head_radius / 100 if args.head == "sphere" else None
    geom = geometry_from_args(args)
    steer = make_steer(args.steer_x, args.steer_y, args.steer_angle,
                       args.depth, args.array_mode, head_R)
    coils = [build_coil(n, geom, steer, head_R) for n in names]

    fx, fy = footprint(coils)
    half_w, half_h = fx + 0.03, fy + 0.04
    if head_R is not None:                       # no need to look past the head
        half_w, half_h = min(half_w, head_R), min(half_h, head_R)

    nx = args.res3d
    ny = max(3, int(nx * half_h / half_w))
    nz = max(30, int(nx * 0.7))
    x = np.linspace(-half_w, half_w, nx)
    y = np.linspace(-half_h, half_h, ny)
    z = np.linspace(-0.003, -args.max_depth / 100, nz)
    X, Y, Z = np.meshgrid(x, y, z, indexing="ij")
    pts = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()])
    depth = head_depth(pts, args)
    voxel_cm3 = abs((x[1] - x[0]) * (y[1] - y[0]) * (z[1] - z[0])) * 1e6
    tpts = target_points(args, half_w, half_h)

    results = []
    n_in = int((depth > 0).sum())
    for c in coils:
        print(f"computing {c.label.splitlines()[0]}  ({n_in:,} points) ...",
              flush=True)
        results.append(analyse(c, args, pts, depth, voxel_cm3, tpts))

    os.makedirs(args.out, exist_ok=True)
    f1 = os.path.join(args.out, "coil_3d.html")
    f2 = os.path.join(args.out, "coil_depth_focality.html")
    fig3d = plot_3d(results, args, pts, half_w, half_h, f1)
    figd = plot_depth(results, args, f2)

    print(f"\nHead: {args.head}, target depth {args.depth:g} cm, "
          f"normalisation: {args.normalize}")
    print(f"{'coil':38s} {'E@target':>9s} {'intensity':>10s} "
          f"{'d½ [cm]':>8s} {'V½ [cm³]':>9s} {'S½ [cm²]':>9s}")
    for r in results:
        lab = r["coil"].label.splitlines()[0]
        print(f"{lab:38s} {r['E_ref']:9.0f} {r['scale']:10.2f} "
              f"{r['d_half']:8.2f} {r['V_half']:9.1f} {r['S_half']:9.1f}")
    for r in results:
        if len(r["coil"].label.splitlines()) > 1:
            print(f"  {r['coil'].label.splitlines()[0]}: "
                  f"{r['coil'].label.splitlines()[1]}")
    print("\n'intensity' = factor on dI/dt needed to reach the threshold "
          "(1.00 when --normalize none)")
    print(f"saved: {f1}\n       {f2}")

    if not args.no_show:
        fig3d.show()
        figd.show()


if __name__ == "__main__":
    main()
