#!/usr/bin/env python
# coding: utf8
#
# Copyright (c) 2020 Centre National d'Etudes Spatiales (CNES).
#
# This file is part of CARS
# (see https://github.com/CNES/cars).
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""
This module contains all the reading/writing functions used in main.py
"""

# Standard imports
import logging
from contextlib import contextmanager

# Third party imports
import os
import numpy as np
from affine import Affine
from tqdm import tqdm
import rasterio as rio
import xarray as xr
from dask.distributed import as_completed


def compute_output_window(tile, full_bounds, resolution):
    """
    Computes destination indices slice of a given tile.

    :param tile: raster tile
    :type tile: xarray.Dataset
    :param full_bounds: bounds of the full output image ordered as the
        following: x_min, y_min, x_max, y_max.
    :type full_bounds: tuple(float, float, float, float).
    :param resolution: output resolution.
    :type resolution: float.
    :return: slices indices as i_xmin, i_ymin, i_xmax, i_ymax.
    :rtype: tuple(int, int, int, int)
    """
    x_min, y_min, x_max, y_max = full_bounds
    x_0 = int((np.min(tile.coords["x"]) - x_min) / resolution - 0.5)
    y_0 = int((y_max - np.max(tile.coords["y"])) / resolution - 0.5)
    x_1 = int((np.max(tile.coords["x"]) - x_min) / resolution - 0.5)
    y_1 = int((y_max - np.min(tile.coords["y"])) / resolution - 0.5)

    return (x_0, y_0, x_1, y_1)


@contextmanager
def rasterio_handles(names, files, params, nodata_values, nb_bands):
    """
    Open a context containing a series of rasterio handles. All input
    lists. Must have the same length.

    :param names: List of names to index the output dictionnary
    :type names: list
    :param files: List of path to files
    :type files: List
    :param params: List of rasterio parameters as dictionaries
    :type params: List
    :param nodata_values: List of nodata values
    :type nodata_values: List
    :param nb_bands: List of number of bands
    :type nb_bands: List
    :return: A dicionary of rasterio handles that can be used as a context manager, indexed by names
    :rtype: Dict
    """
    file_handles = {}
    for name, f, p, nd, nb in zip(names, files, params, nodata_values, nb_bands):
        file_handles[name] = rio.open(f, 'w',count=nb, nodata=nd, **p)
    try:
        yield file_handles
    finally:
        for handle in file_handles.values():
            handle.close()


def write_geotiff_dsm(future_dsm, output_dir, x_size, y_size, bounds,
                      resolution, epsg, nb_bands, dsm_no_data, color_no_data, write_color = True, 
                      color_dtype = np.float32, write_stats = False, prefix=''):
    """
    Writes result tiles to GTiff file(s).

    :param future_dsm: iterable containing future output tiles.
    :type future_dsm: list(dask.future)
    :param dsm_file: dsm GeoTiff filename.
    :type dsm_file: str
    :param clr_file: color GeoTiff filename.
    :type clr_file: str
    :param x_size: full output x size.
    :type x_size: int
    :param y_size: full output y size.
    :type y_size: int
    :param bounds: geographic bounds of the tile (xmin, ymin, xmax, ymax).
    :type bounds: tuple(float, float, float, float)
    :param resolution: resolution of the tiles.
    :type resolution: float
    :param epsg: epsg numeric code of the output tiles.
    :type epsg: int
    :param nb_bands: number of band in the color layer.
    :type nb_bands: int
    :param dsm_no_data: value to fill no data in height layer.
    :type dsm_no_data: float
    :param color_no_data: value to fill no data in color layer(s).
    :type color_no_data: float
    """
    geotransform = (bounds[0], resolution, 0.0, bounds[3], 0.0, -resolution)
    transform = Affine.from_gdal(*geotransform)

    # common parameters for rasterio output
    dsm_rio_params = dict(
        height=y_size, width=x_size, driver='GTiff', dtype=np.float32,
        transform=transform, crs='EPSG:{}'.format(epsg), tiled=True
    )
    clr_rio_params = dict(
        height=y_size, width=x_size, driver='GTiff', dtype=color_dtype,
        transform=transform, crs='EPSG:{}'.format(epsg), tiled=True
    )

    dsm_rio_params_uint16 = dict(
        height=y_size, width=x_size, driver='GTiff', dtype=np.uint16,
        transform=transform, crs='EPSG:{}'.format(epsg), tiled=True
    )


    dsm_file = os.path.join(output_dir, prefix+'dsm.tif')

    # Prepare values for file handles
    names = ['dsm']
    files = [dsm_file]
    params = [dsm_rio_params]
    nodata_values = [dsm_no_data]
    nb_bands_to_write = [1]

    if write_color:
        names.append('clr')
        clr_file = os.path.join(output_dir, prefix+'clr.tif')
        files.append(clr_file)
        params.append(clr_rio_params)
        nodata_values.append(color_no_data)
        nb_bands_to_write.append(nb_bands)

    if write_stats:
        names.append('dsm_mean')
        dsm_mean_file = os.path.join(output_dir, prefix+'dsm_mean.tif')
        files.append(dsm_mean_file)
        params.append(dsm_rio_params)
        nodata_values.append(dsm_no_data)
        nb_bands_to_write.append(1)
        names.append('dsm_std')
        dsm_std_file = os.path.join(output_dir, prefix+'dsm_std.tif')
        files.append(dsm_std_file)
        params.append(dsm_rio_params)
        nodata_values.append(dsm_no_data)
        nb_bands_to_write.append(1)

        names.append('dsm_n_pts')
        dsm_n_pts_file = os.path.join(output_dir, prefix+'dsm_n_pts.tif')
        files.append(dsm_n_pts_file)
        params.append(dsm_rio_params_uint16)
        nodata_values.append(0)
        nb_bands_to_write.append(1)
        names.append('dsm_pts_in_cell')
        dsm_pts_in_cell_file = os.path.join(output_dir, prefix+'dsm_pts_in_cell.tif')
        files.append(dsm_pts_in_cell_file)
        params.append(dsm_rio_params_uint16)
        nodata_values.append(0)
        nb_bands_to_write.append(1)

    # detect if we deal with dask.future or plain datasets
    hasDatasets = True
    for tile in future_dsm:
        if tile is None:
            continue
        hasDatasets = hasDatasets and isinstance(tile, xr.Dataset)

    # get file handle(s) with optional color file.
    with rasterio_handles(names, files, params, nodata_values, nb_bands_to_write) as rio_handles:

        # Use inner function for the writing of tiles
        def write(raster_tile):

            # Skip empty tile
            if raster_tile is None:
                logging.debug('Ignoring empty tile')
                return

            x0, y0, x1, y1 = compute_output_window(raster_tile, bounds,
                                                   resolution)

            logging.debug(
                "Writing tile of size [{}, {}] at index [{}, {}]"
                    .format(x1 - x0 + 1, y1 - y0 + 1, x0, y0)
            )

            # window is speficied as origin & size
            window = rio.windows.Window(x0, y0, x1 - x0 + 1, y1 - y0 + 1)

            rio_handles['dsm'].write_band(1, raster_tile['hgt'].values, window=window)
            
            if write_color:
                rio_handles['clr'].write(raster_tile['img'].values.astype(color_dtype), window=window)

            if write_stats:
                rio_handles['dsm_mean'].write_band(1, raster_tile['hgt_mean'].values, window=window)
                rio_handles['dsm_std'].write_band(1, raster_tile['hgt_stdev'].values, window=window)
                rio_handles['dsm_n_pts'].write_band(1, raster_tile['n_pts'].values, window=window)
                rio_handles['dsm_pts_in_cell'].write_band(1, raster_tile['pts_in_cell'].values, window=window)

        # Multiprocessing mode
        if hasDatasets:
            for raster_tile in future_dsm:
                write(raster_tile)

        # dask mode
        else:
            for future, raster_tile in tqdm(
                    as_completed(future_dsm, with_results=True),
                    total=len(future_dsm), desc="Writing output tif file"):

                write(raster_tile)

                logging.debug('Waiting for next tile')
                if future is not None:
                    future.cancel()
