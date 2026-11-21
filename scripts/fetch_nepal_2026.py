"""Fetch REAL open disaster data for the 26 August 2026 Nepal Bhote Koshi / Trishuli flash flood
(Copernicus EMS Activation EMSR927, Rasuwa District — Syabrubesi & Timure corridor).

All sources are public, open-access humanitarian / Earth-observation datasets catalogued by
cgiovando/disaster-imagery-viewer (MIT) & Copernicus EMS / UNOSAT / OpenAerialMap / OSM.

Outputs are saved into datasets/nepal_2026_syabrubesi/ in metric UTM Zone 45N (EPSG:32645)
at 1.0 m/px so MAPPA runs completely offline on an ordinary laptop in seconds without OOM.
"""
from __future__ import annotations

import json
import math
import os
import sys
import urllib.parse
import urllib.request
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT
from rasterio.warp import Resampling, transform_bounds
import geopandas as gpd
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "datasets" / "nepal_2026_syabrubesi"
OUT.mkdir(parents=True, exist_ok=True)

# Syabrubesi (Shyaphru) corridor — epicentre of Bhote Koshi flood impact on Pasang Lhamu Highway
# Approx 2.6 km x 2.4 km window in Rasuwa District, Nepal
AOI_WGS84 = (85.323, 28.151, 85.350, 28.173)  # (west, south, east, north)
TARGET_CRS = CRS.from_epsg(32645)  # UTM Zone 45N

SOURCES = {
    "post_vantor_2026_09_06": {
        "url": "https://oin-hotosm-temp.s3.us-east-1.amazonaws.com/6a9e72a08cb6541dae9eb0af/0/6a9e72a08cb6541dae9eb0b0.tif",
        "acquired": "2026-09-06T23:43:00Z",
        "provider": "Vantor / Maxar Open Data via OpenAerialMap",
        "gsd_m": 0.39,
    },
    "post_vantor_2026_08_26": {
        "url": "https://oin-hotosm-temp.s3.us-east-1.amazonaws.com/6a90f22377e669c3b6778550/0/6a90f22377e669c3b6778551.tif",
        "acquired": "2026-08-26T22:00:00Z",
        "provider": "Vantor / Maxar Open Data via OpenAerialMap (Flood Day)",
        "gsd_m": 0.37,
    },
    "pre_vantor_2021_10_16": {
        "url": "https://oin-hotosm-temp.s3.us-east-1.amazonaws.com/6a90bc90d8f7b27f66738feb/0/6a90bc90d8f7b27f66738fec.tif",
        "acquired": "2021-10-16T03:22:00Z",
        "provider": "Vantor / Maxar Open Data via OpenAerialMap (Pre-Event)",
        "gsd_m": 0.59,
    },
    "pre_vantor_2023_09_17": {
        "url": "https://oin-hotosm-temp.s3.us-east-1.amazonaws.com/6a908044d8f7b27f667344de/0/6a908044d8f7b27f667344df.tif",
        "acquired": "2023-09-17T02:09:00Z",
        "provider": "Vantor / Maxar Open Data via OpenAerialMap (Pre-Event)",
        "gsd_m": 0.47,
    },
    "copernicus_dem_30m": {
        "url": "https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N28_00_E085_00_DEM/Copernicus_DSM_COG_10_N28_00_E085_00_DEM.tif",
        "acquired": "Copernicus GLO-30",
        "provider": "Copernicus DEM 30m AWS Open Data",
        "gsd_m": 30.0,
    },
}

COPERNICUS_ROAD_URLS = [
    "https://unosatgis.cern.ch/rapidmapping/rest/services/FL20260826NPL/Copernicus_Analysis/MapServer/33/query?where=1%3D1&outFields=*&outSR=4326&f=geojson",
    "https://unosatgis.cern.ch/rapidmapping/rest/services/FL20260826NPL/Copernicus_Analysis/MapServer/14/query?where=1%3D1&outFields=*&outSR=4326&f=geojson",
    "https://unosatgis.cern.ch/rapidmapping/rest/services/FL20260826NPL/Copernicus_Analysis/MapServer/25/query?where=1%3D1&outFields=*&outSR=4326&f=geojson",
    "https://unosatgis.cern.ch/rapidmapping/rest/services/FL20260826NPL/Copernicus_Analysis/MapServer/40/query?where=1%3D1&outFields=*&outSR=4326&f=geojson",
]

COPERNICUS_BUILDING_URLS = [
    "https://unosatgis.cern.ch/rapidmapping/rest/services/FL20260826NPL/Copernicus_Analysis/MapServer/29/query?where=1%3D1&outFields=*&outSR=4326&f=geojson",
    "https://unosatgis.cern.ch/rapidmapping/rest/services/FL20260826NPL/Copernicus_Analysis/MapServer/11/query?where=1%3D1&outFields=*&outSR=4326&f=geojson",
]

SERTIT_ROAD_URL = "https://unosatgis.cern.ch/rapidmapping/rest/services/FL20260826NPL/Sertit_Analysis/MapServer/3/query?where=1%3D1&outFields=*&outSR=4326&f=geojson"

AI4G_PRE_TILES = "https://opendata.aiforgood.ai/damage-assessment/tiles/nepal_flash_flood_2026_pre_planetscope_rgb/pre/{z}/{x}/{y}.png"
AI4G_POST_TILES = "https://opendata.aiforgood.ai/damage-assessment/tiles/nepal_flash_flood_2026_post_planetscope_rgb/post/{z}/{x}/{y}.png"


def fetch_json(url: str, timeout: int = 35) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": "MAPPA-Neo/1.0 (open-source disaster mapping)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"  [warn] fetch_json failed ({url[:80]}...): {e}")
        return None


def fetch_cog_window(url: str, dst_path: Path, aoi_utm: tuple[float, float, float, float],
                     res_m: float = 1.5, is_dem: bool = False) -> dict | None:
    """Read ONLY the AOI window from a remote Cloud-Optimized GeoTIFF via HTTP range requests."""
    l, b, r, t = aoi_utm
    width = int(round((r - l) / res_m))
    height = int(round((t - b) / res_m))
    dst_tf = from_bounds(l, b, r, t, width, height)
    env_opts = {
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff",
        "GDAL_HTTP_TIMEOUT": "30",
        "GDAL_HTTP_MAX_RETRY": "2",
        "AWS_NO_SIGN_REQUEST": "YES",
    }
    try:
        with rasterio.Env(**env_opts):
            with rasterio.open(url) as src:
                with WarpedVRT(
                    src,
                    crs=TARGET_CRS,
                    transform=dst_tf,
                    width=width,
                    height=height,
                    resampling=Resampling.bilinear if is_dem else Resampling.average,
                ) as vrt:
                    if is_dem:
                        arr = vrt.read(1).astype(np.float32)
                        valid_frac = float(np.isfinite(arr).mean())
                        profile = dict(
                            driver="GTiff", height=height, width=width, count=1, dtype="float32",
                            crs=TARGET_CRS, transform=dst_tf, compress="deflate", tiled=True, nodata=-9999.0,
                        )
                        with rasterio.open(dst_path, "w", **profile) as dst:
                            dst.write(np.nan_to_num(arr, nan=-9999.0), 1)
                        return {"width": width, "height": height, "res_m": res_m, "valid_frac": round(valid_frac, 3)}
                    else:
                        bands = min(3, vrt.count)
                        arr = vrt.read(list(range(1, bands + 1)))
                        if arr.dtype != np.uint8:
                            p2, p98 = np.percentile(arr[arr > 0], [2, 98]) if (arr > 0).any() else (0, 255)
                            arr = np.clip((arr - p2) / max(p98 - p2, 1e-3) * 255, 0, 255).astype(np.uint8)
                        nonzero = (arr.sum(axis=0) > 0)
                        valid_frac = float(nonzero.mean())
                        # Estimate bright-cloud fraction (RGB > 225)
                        bright_frac = float(((arr[0] > 225) & (arr[1] > 225) & (arr[2] > 225)).mean()) if bands == 3 else 0.0
                        alpha = (nonzero.astype(np.uint8) * 255)[None]
                        rgba = np.concatenate([arr, alpha], axis=0)
                        profile = dict(
                            driver="GTiff", height=height, width=width, count=4, dtype="uint8",
                            crs=TARGET_CRS, transform=dst_tf, compress="deflate", tiled=True,
                        )
                        with rasterio.open(dst_path, "w", **profile) as dst:
                            dst.write(rgba)
                        return {
                            "width": width, "height": height, "res_m": res_m,
                            "valid_frac": round(valid_frac, 3), "bright_cloud_est": round(bright_frac, 3),
                        }
    except Exception as e:
        print(f"  [warn] COG read failed ({url[:80]}...): {e}")
        return None


def deg2num(lat_deg: float, lon_deg: float, zoom: int) -> tuple[int, int]:
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return xtile, ytile


def num2deg(xtile: int, ytile: int, zoom: int) -> tuple[float, float]:
    n = 2.0 ** zoom
    lon_deg = xtile / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * ytile / n)))
    lat_deg = math.degrees(lat_rad)
    return lat_deg, lon_deg


def fetch_xyz_mosaic(tile_template: str, dst_path: Path, aoi_wgs84: tuple[float, float, float, float],
                     aoi_utm: tuple[float, float, float, float], zoom: int = 15, res_m: float = 2.0) -> dict | None:
    """Fallback/companion: fetch XYZ tiles (e.g., Microsoft AI for Good PlanetScope) and warp to UTM 45N."""
    w, s, e, n = aoi_wgs84
    x0, y0 = deg2num(n, w, zoom)
    x1, y1 = deg2num(s, e, zoom)
    nx, ny = (x1 - x0 + 1), (y1 - y0 + 1)
    print(f"  Fetching {nx}x{ny} = {nx*ny} XYZ tiles at z={zoom}...")
    canvas = np.zeros((ny * 256, nx * 256, 4), dtype=np.uint8)
    ok_tiles = 0
    for ty in range(y0, y1 + 1):
        for tx in range(x0, x1 + 1):
            url = tile_template.format(z=zoom, x=tx, y=ty)
            req = urllib.request.Request(url, headers={"User-Agent": "MAPPA-Neo/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    im = Image.open(BytesIO(r.read())).convert("RGBA")
                    arr = np.array(im)
                    r0, c0 = (ty - y0) * 256, (tx - x0) * 256
                    canvas[r0:r0 + 256, c0:c0 + 256] = arr
                    ok_tiles += 1
            except Exception:
                pass
    if ok_tiles == 0:
        return None
    north_lat, west_lon = num2deg(x0, y0, zoom)
    south_lat, east_lon = num2deg(x1 + 1, y1 + 1, zoom)
    src_tf = from_bounds(west_lon, south_lat, east_lon, north_lat, nx * 256, ny * 256)
    tmp_wgs = dst_path.with_suffix(".wgs84.tif")
    with rasterio.open(
        tmp_wgs, "w", driver="GTiff", height=ny * 256, width=nx * 256, count=4,
        dtype="uint8", crs="EPSG:4326", transform=src_tf, compress="deflate",
    ) as tmp:
        tmp.write(canvas.transpose(2, 0, 1))
    l, b, r, t = aoi_utm
    width = int(round((r - l) / res_m))
    height = int(round((t - b) / res_m))
    dst_tf = from_bounds(l, b, r, t, width, height)
    with rasterio.open(tmp_wgs) as src:
        with WarpedVRT(src, crs=TARGET_CRS, transform=dst_tf, width=width, height=height, resampling=Resampling.bilinear) as vrt:
            data = vrt.read()
    tmp_wgs.unlink(missing_ok=True)
    with rasterio.open(
        dst_path, "w", driver="GTiff", height=height, width=width, count=4,
        dtype="uint8", crs=TARGET_CRS, transform=dst_tf, compress="deflate", tiled=True,
    ) as dst:
        dst.write(data)
    valid_frac = float((data[3] > 127).mean())
    return {"width": width, "height": height, "res_m": res_m, "tiles_ok": ok_tiles, "valid_frac": round(valid_frac, 3)}


def fetch_overpass_osm(aoi_wgs84: tuple[float, float, float, float]) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    w, s, e, n = aoi_wgs84
    q_roads = f"""
    [out:json][timeout:30];
    (
      way["highway"~"motorway|trunk|primary|secondary|tertiary|unclassified|residential|track|path|service"]({s},{w},{n},{e});
    );
    out body; >; out skel qt;
    """
    q_fac = f"""
    [out:json][timeout:30];
    (
      node["place"~"village|hamlet|town|suburb|neighbourhood"]({s},{w},{n},{e});
      node["amenity"~"hospital|clinic|doctors|police|school|shelter|community_centre|fuel"]({s},{w},{n},{e});
      way["amenity"~"hospital|clinic|school|police"]({s},{w},{n},{e});
      node["bridge"="yes"]({s},{w},{n},{e});
    );
    out center;
    """
    roads_gdf = gpd.GeoDataFrame(columns=["name", "highway", "geometry"], crs="EPSG:4326")
    fac_gdf = gpd.GeoDataFrame(columns=["name", "category", "population_est", "geometry"], crs="EPSG:4326")

    for url in ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"):
        try:
            req = urllib.request.Request(
                url,
                data=urllib.parse.urlencode({"data": q_roads}).encode(),
                headers={"User-Agent": "MAPPA-Neo/1.0"},
            )
            with urllib.request.urlopen(req, timeout=35) as r:
                raw = json.loads(r.read().decode("utf-8"))
            nodes = {el["id"]: (el["lon"], el["lat"]) for el in raw["elements"] if el["type"] == "node"}
            from shapely.geometry import LineString
            rows = []
            for el in raw["elements"]:
                if el["type"] == "way" and len(el.get("nodes", [])) >= 2:
                    coords = [nodes[nid] for nid in el["nodes"] if nid in nodes]
                    if len(coords) >= 2:
                        tags = el.get("tags", {})
                        rows.append({
                            "osm_id": el["id"],
                            "name": tags.get("name") or tags.get("ref") or tags.get("highway", "road"),
                            "highway": tags.get("highway", "road"),
                            "geometry": LineString(coords),
                        })
            if rows:
                roads_gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
                print(f"  OSM Overpass roads fetched: {len(roads_gdf)} ways")
                break
        except Exception as exc:
            print(f"  [warn] Overpass roads ({url}): {exc}")

    for url in ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"):
        try:
            req = urllib.request.Request(
                url,
                data=urllib.parse.urlencode({"data": q_fac}).encode(),
                headers={"User-Agent": "MAPPA-Neo/1.0"},
            )
            with urllib.request.urlopen(req, timeout=35) as r:
                raw = json.loads(r.read().decode("utf-8"))
            from shapely.geometry import Point
            frows = []
            for el in raw["elements"]:
                lon = el.get("lon") or el.get("center", {}).get("lon")
                lat = el.get("lat") or el.get("center", {}).get("lat")
                if lon is None or lat is None:
                    continue
                tags = el.get("tags", {})
                cat = tags.get("amenity") or tags.get("place") or ("bridge" if tags.get("bridge") else "landmark")
                name = tags.get("name") or tags.get("name:en") or f"{cat.title()} ({el['id']})"
                pop = int(tags.get("population", 0)) if str(tags.get("population", "")).isdigit() else (
                    450 if cat in ("town", "village") else 180 if cat in ("hospital", "clinic", "school") else 90
                )
                frows.append({
                    "osm_id": el["id"],
                    "name": name,
                    "category": cat,
                    "population_est": pop,
                    "geometry": Point(lon, lat),
                })
            if frows:
                fac_gdf = gpd.GeoDataFrame(frows, crs="EPSG:4326")
                print(f"  OSM Overpass facilities fetched: {len(fac_gdf)} features")
                break
        except Exception as exc:
            print(f"  [warn] Overpass facilities ({url}): {exc}")

    return roads_gdf, fac_gdf


def main():
    print("== MAPPA-Neo: Fetching REAL 2026 Nepal Bhote Koshi Flood Data (Syabrubesi, Rasuwa) ==")
    aoi_utm = transform_bounds("EPSG:4326", TARGET_CRS, *AOI_WGS84)
    print(f"AOI WGS84: {AOI_WGS84}")
    print(f"AOI UTM45N: {[round(x, 1) for x in aoi_utm]} ({(aoi_utm[2]-aoi_utm[0])/1000:.2f} km x {(aoi_utm[3]-aoi_utm[1])/1000:.2f} km)")

    prov = {
        "event": "2026 Nepal Bhote Koshi / Trishuli Flash Flood & Debris Cascade",
        "event_date": "2026-08-26",
        "location": "Syabrubesi (Shyaphru), Rasuwa District, Nepal",
        "copernicus_activation": "EMSR927",
        "aoi_wgs84": list(AOI_WGS84),
        "aoi_utm45n": [round(x, 2) for x in aoi_utm],
        "crs": "EPSG:32645",
        "layers": {},
    }

    # 1. Fetch Copernicus EMS road grading (REAL ground truth)
    print("\n[1/5] Fetching Copernicus EMS EMSR927 road grading + SERTIT roads...")
    clip_box = box(*AOI_WGS84)
    cop_road_feats = []
    for u in COPERNICUS_ROAD_URLS:
        gj = fetch_json(u)
        if gj and gj.get("features"):
            cop_road_feats.extend(gj["features"])
    if cop_road_feats:
        g_all = gpd.GeoDataFrame.from_features(cop_road_feats, crs="EPSG:4326")
        g_aoi = g_all[g_all.intersects(clip_box)].copy()
        g_aoi.to_file(OUT / "copernicus_ems_roads.geojson", driver="GeoJSON")
        g_all.to_file(OUT / "copernicus_ems_roads_full.geojson", driver="GeoJSON")
        counts = g_aoi["damage_gra"].value_counts().to_dict() if "damage_gra" in g_aoi.columns else {}
        print(f"  Copernicus EMS roads: {len(g_all)} total, {len(g_aoi)} in Syabrubesi AOI -> {counts}")
        prov["layers"]["copernicus_ems_roads"] = {"total": len(g_all), "in_aoi": len(g_aoi), "damage_counts": counts}

    # Copernicus EMS buildings
    cop_bldg_feats = []
    for u in COPERNICUS_BUILDING_URLS:
        gj = fetch_json(u)
        if gj and gj.get("features"):
            cop_bldg_feats.extend(gj["features"])
    if cop_bldg_feats:
        gb = gpd.GeoDataFrame.from_features(cop_bldg_feats, crs="EPSG:4326")
        gb_aoi = gb[gb.intersects(clip_box)].copy()
        gb_aoi.to_file(OUT / "copernicus_ems_buildings.geojson", driver="GeoJSON")
        print(f"  Copernicus EMS buildings in Syabrubesi AOI: {len(gb_aoi)}")
        prov["layers"]["copernicus_ems_buildings"] = {"in_aoi": len(gb_aoi)}

    # SERTIT roads
    sj = fetch_json(SERTIT_ROAD_URL)
    if sj and sj.get("features"):
        gs = gpd.GeoDataFrame.from_features(sj["features"], crs="EPSG:4326")
        gs_aoi = gs[gs.intersects(clip_box)].copy()
        gs_aoi.to_file(OUT / "sertit_roads.geojson", driver="GeoJSON")
        print(f"  SERTIT Pleiades roads in AOI: {len(gs_aoi)}")

    # 2. Fetch OSM roads + facilities
    print("\n[2/5] Fetching OpenStreetMap roads & villages/facilities...")
    osm_roads, osm_fac = fetch_overpass_osm(AOI_WGS84)
    if len(osm_roads):
        osm_roads.to_file(OUT / "osm_roads.geojson", driver="GeoJSON")
        prov["layers"]["osm_roads"] = {"count": len(osm_roads)}
    if len(osm_fac):
        osm_fac.to_file(OUT / "osm_facilities.geojson", driver="GeoJSON")
        prov["layers"]["osm_facilities"] = {"count": len(osm_fac)}

    # 3. Fetch VHR Post-Flood & Pre-Flood COGs (1.5 m/px in UTM 45N -> ~1750x1600 px, fast & crisp!)
    print("\n[3/5] Fetching VHR Post-Event COG windows (Syabrubesi)...")
    best_post = None
    for key in ("post_vantor_2026_09_06", "post_vantor_2026_08_26"):
        info = SOURCES[key]
        out_p = OUT / f"{key}.tif"
        print(f"  Reading {key} ({info['acquired']})...")
        meta = fetch_cog_window(info["url"], out_p, aoi_utm, res_m=1.5)
        print(f"    -> {meta}")
        if meta and meta["valid_frac"] > 0.6:
            prov["layers"][key] = {**info, **meta}
            if best_post is None or meta["bright_cloud_est"] < best_post[1]["bright_cloud_est"]:
                best_post = (out_p, meta, key)

    print("\n[4/5] Fetching VHR Pre-Event COG windows (Syabrubesi)...")
    best_pre = None
    for key in ("pre_vantor_2021_10_16", "pre_vantor_2023_09_17"):
        info = SOURCES[key]
        out_p = OUT / f"{key}.tif"
        print(f"  Reading {key} ({info['acquired']})...")
        meta = fetch_cog_window(info["url"], out_p, aoi_utm, res_m=1.5)
        print(f"    -> {meta}")
        if meta and meta["valid_frac"] > 0.6:
            prov["layers"][key] = {**info, **meta}
            if best_pre is None or meta["bright_cloud_est"] < best_pre[1]["bright_cloud_est"]:
                best_pre = (out_p, meta, key)

    # Also fetch Microsoft AI for Good PlanetScope pre/post if VHR is missing or very cloudy
    if best_post is None or best_post[1]["bright_cloud_est"] > 0.45:
        print("  Also fetching AI for Good PlanetScope post-event mosaic...")
        m = fetch_xyz_mosaic(AI4G_POST_TILES, OUT / "post_planetscope.tif", AOI_WGS84, aoi_utm, zoom=15, res_m=2.0)
        print(f"    -> {m}")
        if m:
            prov["layers"]["post_planetscope"] = m
            if best_post is None:
                best_post = (OUT / "post_planetscope.tif", m, "post_planetscope")
    if best_pre is None or best_pre[1]["bright_cloud_est"] > 0.45:
        print("  Also fetching AI for Good PlanetScope pre-event mosaic...")
        m = fetch_xyz_mosaic(AI4G_PRE_TILES, OUT / "pre_planetscope.tif", AOI_WGS84, aoi_utm, zoom=15, res_m=2.0)
        print(f"    -> {m}")
        if m:
            prov["layers"]["pre_planetscope"] = m
            if best_pre is None:
                best_pre = (OUT / "pre_planetscope.tif", m, "pre_planetscope")

    # 5. Fetch Copernicus GLO-30 DEM
    print("\n[5/5] Fetching Copernicus GLO-30 DEM window...")
    dem_meta = fetch_cog_window(SOURCES["copernicus_dem_30m"]["url"], OUT / "copernicus_dem_30m.tif", aoi_utm, res_m=10.0, is_dem=True)
    print(f"  DEM -> {dem_meta}")
    if dem_meta:
        prov["layers"]["copernicus_dem_30m"] = {**SOURCES["copernicus_dem_30m"], **dem_meta}

    if best_post:
        prov["selected_post"] = best_post[0].name
        prov["selected_post_key"] = best_post[2]
    if best_pre:
        prov["selected_pre"] = best_pre[0].name
        prov["selected_pre_key"] = best_pre[2]

    (OUT / "provenance.json").write_text(json.dumps(prov, indent=2))
    print("\nDONE! Saved provenance to", OUT / "provenance.json")
    print("Selected POST:", prov.get("selected_post"), "| Selected PRE:", prov.get("selected_pre"))


if __name__ == "__main__":
    main()
