"""Common analysis grid + raster IO. Everything downstream works on one metric grid
(the ortho's CRS, default 1 m/px), so base imagery in any CRS/resolution is reprojected once."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import rasterio
from rasterio.transform import Affine, from_origin
from rasterio.warp import Resampling, reproject


@dataclass
class Grid:
    crs: object
    transform: Affine
    width: int
    height: int
    res: float

    @property
    def shape(self):
        return (self.height, self.width)


def make_grid(ortho_path: str, res: float = 1.0, max_dim: int = 2400) -> Grid:
    """Build metric analysis grid. Auto-scales resolution if the mosaic exceeds max_dim pixels
    on either axis so ordinary laptops (8-16 GB RAM) never OOM on large multi-tile orthos."""
    with rasterio.open(ortho_path) as src:
        if src.crs is None or src.crs.is_geographic:
            raise SystemExit("Ortho must be in a projected (metric) CRS, e.g. UTM. ODM writes UTM by default.")
        l, b, r, t = src.bounds
    span_x, span_y = max(r - l, 1.0), max(t - b, 1.0)
    eff_res = float(res)
    if max_dim and max(span_x / eff_res, span_y / eff_res) > max_dim:
        eff_res = round(max(span_x, span_y) / max_dim, 2)
    w = int(math.ceil(span_x / eff_res))
    h = int(math.ceil(span_y / eff_res))
    return Grid(src.crs, from_origin(l, t, eff_res, eff_res), w, h, eff_res)


def native_res_m(path: str) -> float:
    """Approximate native pixel size in metres (handles geographic CRS)."""
    with rasterio.open(path) as src:
        px = abs(src.transform.a)
        if src.crs and src.crs.is_geographic:
            lat = (src.bounds.top + src.bounds.bottom) / 2
            return px * 111_320 * math.cos(math.radians(lat))
        return px


def read_rgb(path: str, grid: Grid, resampling=Resampling.average):
    """Read bands 1-3 onto the grid (float32 HxWx3). Validity from alpha band (band 4) or full footprint."""
    with rasterio.open(path) as src:
        out = np.zeros((3, grid.height, grid.width), np.float32)
        for i in range(3):
            reproject(rasterio.band(src, i + 1), out[i], src_transform=src.transform, src_crs=src.crs,
                      dst_transform=grid.transform, dst_crs=grid.crs, resampling=resampling)
        valid = np.zeros(grid.shape, np.float32)
        if src.count >= 4:
            reproject(rasterio.band(src, 4), valid, src_transform=src.transform, src_crs=src.crs,
                      dst_transform=grid.transform, dst_crs=grid.crs, resampling=Resampling.average)
        else:
            ones = np.full((src.height, src.width), 255, np.float32)
            reproject(ones, valid, src_transform=src.transform, src_crs=src.crs,
                      dst_transform=grid.transform, dst_crs=grid.crs, resampling=Resampling.average)
        valid = valid > 127
    return out.transpose(1, 2, 0), valid


def read_band(path: str, grid: Grid, resampling=Resampling.bilinear):
    with rasterio.open(path) as src:
        out = np.full(grid.shape, np.nan, np.float32)
        reproject(rasterio.band(src, 1), out, src_transform=src.transform, src_crs=src.crs,
                  dst_transform=grid.transform, dst_crs=grid.crs, resampling=resampling,
                  src_nodata=src.nodata, dst_nodata=np.nan)
    return out
