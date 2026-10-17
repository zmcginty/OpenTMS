#!/usr/bin/env python3
"""
tms_coil_3d.py
==============

3-D depth visualization of the induced E-field of TMS coils, plus the
depth-vs-focality trade-off (Deng et al., Brain Stimulation 2013).

Needs tms_coil_fields.py in the SAME folder (it reuses its coil models and
field solver).

What it makes (opens in your web browser, also saved as HTML):
  coil_3d.html             rotatable 3-D isosurfaces of |E| under each coil
  coil_depth_focality.html field decay with depth + d_1/2 vs S_1/2 plot

Normalisation (--normalize):
  matched (default)  each coil's intensity is scaled so its peak |E| at the
                     target depth equals --threshold, the same way you would
                     dial each coil to motor threshold. Then you compare how
                     deep and how wide each coil spreads at equal effect.
  none               every coil driven with the same dI/dt.

Depth / focality metrics (measured below the target depth):
  d_1/2  distance below the target at which peak |E| falls to half the
         target value (bigger = penetrates deeper)
  V_1/2  volume below the target where |E| >= half the target value
  S_1/2  V_1/2 / d_1/2, an effective "spread" area (smaller = more focal)

Usage
-----
    pip install numpy plotly
    python tms_coil_3d.py                          # coil-design comparison
    python tms_coil_3d.py --configs fig8 fig8_overlap --overlap 3
    python tms_coil_3d.py --configs wiring --style volume
    python tms_coil_3d.py --depth 2.5 --threshold 120

Same free-space caveat as tms_coil_fields.py: head tissue boundaries are not
modelled, so absolute numbers are approximate; trends are right.
"""
from __future__ import annotations

import argparse
import math
import os

import numpy as np

from tms_coil_fields import (CONFIGS, PRESETS, CCW_COLOR, CW_COLOR,
                             a_field, add_geometry_args, build_coil,
                             expand_configs, footprint, geometry_from_args)


def e_mag(coil, pts, didt_si):
    """|E| [V/m] per point for a given dI/dt [A/s]."""
    return np.linalg.norm(a_field(coil, pts), axis=-1) * didt_si


def analyse(coil, args, grid, target_pts):
    x, y, z, pts = grid
    didt = args.didt * 1e6
    peak_target = e_mag(coil, target_pts, didt).max()
    scale = args.threshold / peak_target if args.normalize == "matched" else 1.0
    E = (e_mag(coil, pts, didt) * scale).reshape(len(x), len(y), len(z))
    E_ref = peak_target * scale

    depth_cm = -z * 100                       # positive = deeper
    layer_peak = E.max(axis=(0, 1))
    below = depth_cm >= args.depth
    dz, lp = depth_cm[below], layer_peak[below]

    hit = np.where(lp <= 0.5 * E_ref)[0]
    if hit.size == 0:
        d_half = float("nan")                 # never halves inside the grid
    elif hit[0] == 0:
        d_half = 0.0
    else:
        i = hit[0]
        d_at = dz[i - 1] + (0.5 * E_ref - lp[i - 1]) * \
            (dz[i] - dz[i - 1]) / (lp[i] - lp[i - 1])
        d_half = d_at - args.depth

    voxel = np.prod([(a[1] - a[0]) * 100 for a in (x, y, z)])
    V_half = np.count_nonzero(E[:, :, below] >= 0.5 * E_ref) * abs(voxel)
    S_half = V_half / d_half if d_half and d_half > 0 else float("nan")

    return dict(coil=coil, E=E, E_ref=E_ref, scale=scale,
                layer_peak=layer_peak, depth_cm=depth_cm,
                d_half=d_half, V_half=V_half, S_half=S_half)


def coil_traces(coil, go):
    out = []
    for w in coil.wings:
        p = w["pts"] * 100
        out.append(go.Scatter3d(
            x=p[:, 0], y=p[:, 1], z=p[:, 2] + 0.15, mode="lines",
            line=dict(color=CCW_COLOR if w["sign"] > 0 else CW_COLOR, width=4),
            showlegend=False, hoverinfo="skip"))
    return out


def plot_3d(results, args, grid, path):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    x, y, z, _ = grid
    X, Y, Z = np.meshgrid(x * 100, y * 100, z * 100, indexing="ij")
    Xf, Yf, Zf = (np.round(a.ravel(), 2).astype(np.float32) for a in (X, Y, Z))

    R = (args.threshold if args.normalize == "matched"
         else max(r["E_ref"] for r in results))
    n = len(results)
    cols = min(n, 2)
    rows = math.ceil(n / cols)
    titles = []
    for r in results:
        t = f"<b>{r['coil'].label}</b><br>d½ = {r['d_half']:.1f} cm, " \
            f"S½ = {r['S_half']:.0f} cm²"
        if args.normalize == "matched":
            t += f", intensity ×{r['scale']:.2f}"
        titles.append(t)
    fig = make_subplots(rows=rows, cols=cols, subplot_titles=titles,
                        specs=[[{"type": "scene"}] * cols] * rows,
                        horizontal_spacing=0.02, vertical_spacing=0.06)

    cbar = dict(title=dict(text="|E| [V/m]", side="right"), len=0.6)
    xs, ys = x * 100, y * 100
    for k, r in enumerate(results):
        row, col = k // cols + 1, k % cols + 1
        val = np.round(r["E"].ravel(), 1).astype(np.float32)
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
        for t in coil_traces(r["coil"], go):
            fig.add_trace(t, row=row, col=col)
        # translucent plane at the target depth
        fig.add_trace(go.Surface(
            x=[xs[0], xs[-1]], y=[ys[0], ys[-1]],
            z=np.full((2, 2), -args.depth), showscale=False, opacity=0.15,
            colorscale=[[0, "#4cc9f0"], [1, "#4cc9f0"]], hoverinfo="skip"),
            row=row, col=col)

    levels = "25 / 50 / 75 / 100 %" if args.style == "iso" else "10-150 %"
    ref = (f"threshold {args.threshold:g} V/m matched at {args.depth:g} cm"
           if args.normalize == "matched" else
           f"same dI/dt = {args.didt:g} A/µs")
    fig.update_scenes(
        aspectmode="data",
        xaxis_title="x [cm]", yaxis_title="y [cm]", zaxis_title="z [cm]",
        camera=dict(eye=dict(x=1.3, y=-1.6, z=0.8)))
    fig.update_layout(
        title=f"Induced |E| below the coil, shells at {levels} of {R:.0f} V/m "
              f"({ref}); blue plane = target depth",
        height=560 * rows, width=1500, margin=dict(l=0, r=0, t=110, b=0))
    fig.write_html(path, include_plotlyjs=True)
    return fig


def plot_depth(results, args, path):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    fig = make_subplots(rows=1, cols=2, subplot_titles=(
        "Peak |E| at each depth (relative to the target-depth value)",
        "Depth vs focality trade-off"))
    palette = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#8c564b"]
    for r, c in zip(results, palette):
        lab = r["coil"].label
        fig.add_trace(go.Scatter(x=r["depth_cm"], y=r["layer_peak"] / r["E_ref"],
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
    fig.update_xaxes(title="depth below coil [cm]", row=1, col=1)
    fig.update_xaxes(title="d½ [cm] (deeper →)", row=1, col=2)
    fig.update_yaxes(title="S½ [cm²] (more spread ↑, more focal ↓)",
                     row=1, col=2)
    fig.update_layout(height=550, width=1400,
                      title="Depth penetration and focality "
                            "(bottom-right of the right panel would be ideal)")
    fig.write_html(path, include_plotlyjs=True)
    return fig


def main():
    p = argparse.ArgumentParser(
        description="3-D depth visualization of TMS coil E-fields.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--configs", nargs="+", default=["designs"],
                   help=f"any of {list(CONFIGS)} or a preset "
                        f"{list(PRESETS)}")
    p.add_argument("--large-single", action="store_true",
                   help="also add the large round coil (single_large)")
    p.add_argument("--depth", type=float, default=2.0,
                   help="target (cortex) depth below coil [cm]")
    p.add_argument("--threshold", type=float, default=100,
                   help="target |E| for matched normalisation [V/m]")
    p.add_argument("--normalize", choices=["matched", "none"],
                   default="matched")
    p.add_argument("--didt", type=float, default=100,
                   help="coil dI/dt [A/µs] (used directly if --normalize none)")
    p.add_argument("--style", choices=["iso", "volume"], default="iso",
                   help="iso = nested shells, volume = cloud-like heat map")
    add_geometry_args(p)
    p.add_argument("--max-depth", type=float, default=7.0,
                   help="deepest point of the 3-D grid [cm]")
    p.add_argument("--res3d", type=int, default=51,
                   help="3-D grid points across x (higher = smoother, slower, "
                        "bigger HTML)")
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

    geom = geometry_from_args(args)
    coils = [build_coil(n, geom) for n in names]
    fx, fy = footprint(coils)
    half_w, half_h = fx + 0.03, fy + 0.04
    nx = args.res3d
    ny = max(3, int(nx * half_h / half_w))
    nz = max(10, int(nx * 0.7))
    x = np.linspace(-half_w, half_w, nx)
    y = np.linspace(-half_h, half_h, ny)
    z = np.linspace(-0.003, -args.max_depth / 100, nz)
    X, Y, Z = np.meshgrid(x, y, z, indexing="ij")
    pts = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()])
    grid = (x, y, z, pts)

    tx, ty = np.meshgrid(np.linspace(-half_w, half_w, 2 * nx),
                         np.linspace(-half_h, half_h, 2 * ny))
    target_pts = np.column_stack([tx.ravel(), ty.ravel(),
                                  np.full(tx.size, -args.depth / 100)])

    results = []
    for c in coils:
        print(f"computing {c.label}  ({len(pts):,} points) ...", flush=True)
        results.append(analyse(c, args, grid, target_pts))

    os.makedirs(args.out, exist_ok=True)
    f1 = os.path.join(args.out, "coil_3d.html")
    f2 = os.path.join(args.out, "coil_depth_focality.html")
    fig3d = plot_3d(results, args, grid, f1)
    figd = plot_depth(results, args, f2)

    print(f"\nTarget depth {args.depth:g} cm, normalisation: {args.normalize}")
    print(f"{'coil':38s} {'E@target':>9s} {'intensity':>10s} "
          f"{'d½ [cm]':>8s} {'V½ [cm³]':>9s} {'S½ [cm²]':>9s}")
    for r in results:
        print(f"{r['coil'].label:38s} {r['E_ref']:9.0f} {r['scale']:10.2f} "
              f"{r['d_half']:8.2f} {r['V_half']:9.1f} {r['S_half']:9.1f}")
    print("\n'intensity' = factor on dI/dt needed to reach the threshold "
          "(1.00 when --normalize none)")
    print(f"saved: {f1}\n       {f2}")

    if not args.no_show:
        fig3d.show()
        figd.show()


if __name__ == "__main__":
    main()
