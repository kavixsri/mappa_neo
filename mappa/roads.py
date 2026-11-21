"""Blocked-road scoring: split OSM road lines into short segments, buffer, and measure how much of each
buffer falls inside the change mask (and, if available, how large the height anomaly is)."""
from __future__ import annotations

import math

import geopandas as gpd
import numpy as np
from rasterio.features import geometry_mask
from rasterio.windows import Window, transform as win_transform
from shapely.geometry import LineString, MultiLineString
from shapely.ops import substring

BLOCKED, UNCERTAIN, CLEAR, NODATA = "BLOCKED", "UNCERTAIN", "CLEAR", "NO_DATA"

DEFAULTS = dict(seg_len_m=20.0, buffer_m=5.0, blocked_frac=0.40, uncertain_frac=0.15,
                blocked_height_m=0.5, blocked_frac_no_dsm=0.60, min_valid=0.5)


def load_roads(path: str, crs) -> gpd.GeoDataFrame:
    g = gpd.read_file(path)
    if g.crs is None:
        g = g.set_crs(4326)
    g = g.to_crs(crs)
    g = g[g.geometry.notna() & g.geometry.geom_type.isin(["LineString", "MultiLineString"])].explode(index_parts=False)
    g = g.reset_index(drop=True)
    g["road_id"] = g.index
    if "name" not in g.columns:
        g["name"] = None
    g["name"] = g["name"].fillna(g["highway"] if "highway" in g.columns else "road").astype(str)
    return g


def segments(roads: gpd.GeoDataFrame, seg_len: float) -> gpd.GeoDataFrame:
    rows = []
    for _, r in roads.iterrows():
        line: LineString = r.geometry
        n = max(1, int(math.ceil(line.length / seg_len)))
        step = line.length / n
        for i in range(n):
            seg = substring(line, i * step, (i + 1) * step)
            if seg.length > 0:
                rows.append(dict(road_id=int(r.road_id), name=r["name"], seq=i, length_m=seg.length, geometry=seg))
    return gpd.GeoDataFrame(rows, crs=roads.crs)


def score(segs: gpd.GeoDataFrame, mask: np.ndarray, anomaly, valid: np.ndarray, grid, **kw) -> gpd.GeoDataFrame:
    p = {**DEFAULTS, **kw}
    have_h = anomaly is not None
    H, W = mask.shape
    out = []
    for _, s in segs.iterrows():
        buf = s.geometry.buffer(p["buffer_m"], cap_style=2)
        l, b, r, t = buf.bounds
        inv = ~grid.transform
        c0, r1 = inv * (l, b)
        c1, r0 = inv * (r, t)
        c0, c1 = int(max(0, math.floor(c0))), int(min(W, math.ceil(c1)))
        r0, r1 = int(max(0, math.floor(r0))), int(min(H, math.ceil(r1)))
        status, frac, hmean, vfrac = NODATA, np.nan, np.nan, 0.0
        if c1 > c0 and r1 > r0:
            win = Window(c0, r0, c1 - c0, r1 - r0)
            inside = ~geometry_mask([buf], out_shape=(r1 - r0, c1 - c0), transform=win_transform(win, grid.transform))
            n = int(inside.sum())
            if n:
                v = valid[r0:r1, c0:c1][inside]
                vfrac = float(v.mean())
                if vfrac >= p["min_valid"]:
                    frac = float(mask[r0:r1, c0:c1][inside][v].mean())
                    if have_h:
                        a = np.abs(anomaly[r0:r1, c0:c1][inside][v])
                        hmean = float(np.nanmean(a)) if np.isfinite(a).any() else np.nan
                    status = classify(frac, hmean, have_h, p)
        out.append(dict(road_id=s.road_id, name=s["name"], seq=s.seq, length_m=round(s.length_m, 1),
                        changed_frac=round(frac, 3) if frac == frac else None,
                        height_anom_m=round(hmean, 2) if hmean == hmean else None,
                        valid_frac=round(vfrac, 2), status=status, geometry=s.geometry))
    return gpd.GeoDataFrame(out, crs=segs.crs)


def classify(frac, hmean, have_h, p):
    if have_h and hmean == hmean:
        if frac >= p["blocked_frac"] and hmean >= p["blocked_height_m"]:
            return BLOCKED
        if frac >= p["uncertain_frac"]:
            return UNCERTAIN
        return CLEAR
    if frac >= p["blocked_frac_no_dsm"]:
        return BLOCKED
    if frac >= p["uncertain_frac"]:
        return UNCERTAIN
    return CLEAR


def stretches(scored: gpd.GeoDataFrame, status: str = BLOCKED) -> list[dict]:
    """Merge consecutive same-status segments on one road into reportable stretches (WGS84 centroid)."""
    res = []
    for rid, g in scored.sort_values(["road_id", "seq"]).groupby("road_id"):
        run = []
        for _, s in g.iterrows():
            if s.status == status and (not run or s.seq == run[-1].seq + 1):
                run.append(s)
            else:
                if run:
                    res.append(_pack(run, status))
                run = [s] if s.status == status else []
        if run:
            res.append(_pack(run, status))
    return sorted(res, key=lambda r: -r["length_m"])


def _pack(run, status):
    geom = MultiLineString([r.geometry for r in run])
    length_m = round(sum(r.length_m for r in run), 1)
    mean_changed = round(float(np.nanmean([r.changed_frac for r in run])), 2)
    heights = [r.height_anom_m for r in run if r.height_anom_m is not None and r.height_anom_m == r.height_anom_m]
    mean_h = round(float(np.mean(heights)), 2) if heights else None
    # Estimated debris volume (m3) for PWD/BRO excavator planning:
    # length * road_width(6 m) * depth (measured height anomaly if DSM-DEM exists, else conservative 1.2 m proxy)
    est_depth = mean_h if (mean_h is not None and mean_h > 0.2) else (1.2 if status == BLOCKED else 0.4)
    debris_m3 = round(length_m * 6.0 * max(mean_changed, 0.25) * est_depth, 0)
    return dict(
        road=run[0]["name"],
        road_id=int(run[0].road_id),
        status=status,
        length_m=length_m,
        mean_changed_frac=mean_changed,
        mean_height_anom_m=mean_h,
        est_debris_vol_m3=int(debris_m3),
        debris_vol_source="dsm_minus_dem" if mean_h is not None else "optical_proxy_1.2m",
        _centroid_xy=(geom.centroid.x, geom.centroid.y),
    )


def evaluate_against_copernicus(scored: gpd.GeoDataFrame, copernicus_path: str, buffer_m: float = 15.0) -> dict:
    """Compare MAPPA automated road flags against official Copernicus EMS photo-interpreted road grading."""
    cop = gpd.read_file(copernicus_path)
    if cop.crs is None:
        cop = cop.set_crs(4326)
    cop = cop.to_crs(scored.crs)
    if "damage_gra" not in cop.columns or len(cop) == 0:
        return {"available": False, "reason": "No damage_gra column in Copernicus file"}
    damaged = cop[cop["damage_gra"].isin(["Destroyed", "Damaged", "Possibly damaged"])]
    undamaged = cop[cop["damage_gra"].isin(["No visible damage"])]
    if len(damaged) == 0:
        return {"available": False, "reason": "No damaged segments in AOI"}
    dam_union = damaged.geometry.buffer(buffer_m).union_all()
    undam_union = undamaged.geometry.buffer(buffer_m).union_all() if len(undamaged) else None

    valid_segs = scored[scored["status"] != NODATA].copy()
    if len(valid_segs) == 0:
        return {"available": False, "reason": "No valid scored segments"}

    flagged = valid_segs[valid_segs["status"].isin([BLOCKED, UNCERTAIN])]
    blocked_only = valid_segs[valid_segs["status"] == BLOCKED]
    hits_any = flagged[flagged.geometry.intersects(dam_union)]
    hits_blocked = blocked_only[blocked_only.geometry.intersects(dam_union)]
    gt_in_scored = valid_segs[valid_segs.geometry.intersects(dam_union)]

    flagged_m = float(flagged["length_m"].sum())
    hit_any_m = float(hits_any["length_m"].sum())
    gt_m = float(gt_in_scored["length_m"].sum())
    recall_any = round(hit_any_m / max(gt_m, 1.0), 3)
    precision_any = round(hit_any_m / max(flagged_m, 1.0), 3)

    clear_segs = valid_segs[valid_segs["status"] == CLEAR]
    clear_on_undam_m = float(clear_segs[clear_segs.geometry.intersects(undam_union)]["length_m"].sum()) if undam_union is not None else 0.0
    undam_total_m = float(valid_segs[valid_segs.geometry.intersects(undam_union)]["length_m"].sum()) if undam_union is not None else 0.0
    specificity_undam = round(clear_on_undam_m / max(undam_total_m, 1.0), 3) if undam_total_m > 0 else None

    return {
        "available": True,
        "reference": "Copernicus EMS Activation EMSR927 (photo-interpreted road grading)",
        "copernicus_segments_in_aoi": int(len(cop)),
        "copernicus_damaged_segments": int(len(damaged)),
        "copernicus_undamaged_segments": int(len(undamaged)),
        "matched_damaged_road_m": round(hit_any_m, 1),
        "matched_blocked_strict_m": round(float(hits_blocked["length_m"].sum()), 1),
        "total_gt_damaged_in_footprint_m": round(gt_m, 1),
        "total_mappa_flagged_m": round(flagged_m, 1),
        "recall_vs_copernicus_damaged": recall_any,
        "precision_vs_copernicus_damaged": precision_any,
        "clear_agreement_on_undamaged": specificity_undam,
        "note": "Honest optical-only comparison (no post-flood DSM available in open satellite data; cross-sensor seasonal variance & steep Himalayan shadow affect precision).",
    }
