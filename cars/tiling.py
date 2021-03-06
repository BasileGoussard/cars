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
This module contains functions related to regions and tiles management
"""

import math
import numpy as np
from osgeo import osr
from scipy import interpolate
from cars import utils, projection


def grid(xmin, ymin, xmax, ymax, xsplit, ysplit):
    """
    Generate grid of positions by splitting [xmin, xmax]x[ymin,ymax] in splits of xsplit x ysplit size

    :param xmin : xmin of the bounding box of the region to split
    :type xmin: float
    :param ymin : ymin of the bounding box of the region to split
    :type ymin: float
    :param xmax : xmax of the bounding box of the region to split
    :type xmax: float
    :param ymax : ymax of the bounding box of the region to split
    :type ymax: float
    :param xsplit: width of splits
    :type xsplit: int
    :param ysplit: height of splits
    :type ysplit: int
    :returns: A tuple with output grid, number of splits if first direction (n), number of splits in second direction (m)
    :type ndarray of shape (n+1,m+1,2)
    """
    nb_xsplits = math.ceil((xmax - xmin) / xsplit)
    nb_ysplits = math.ceil((ymax - ymin) / ysplit)

    res = np.ndarray(shape=(nb_ysplits + 1, nb_xsplits + 1, 2), dtype=float)

    for i in range(0, nb_xsplits + 1):
        for j in range(0, nb_ysplits + 1):
            res[j, i, 0] = min(xmax, xmin + i * xsplit)
            res[j, i, 1] = min(ymax, ymin + j * ysplit)

    return res


def split(xmin, ymin, xmax, ymax, xsplit, ysplit):
    """
    Split a region defined by [xmin, xmax]x[ymin,ymax] in splits of xsplit x ysplit size

    :param xmin : xmin of the bounding box of the region to split
    :type xmin: float
    :param ymin : ymin of the bounding box of the region to split
    :type ymin: float
    :param xmax : xmax of the bounding box of the region to split
    :type xmax: float
    :param ymax : ymax of the bounding box of the region to split
    :type ymax: float
    :param xsplit: width of splits
    :type xsplit: int
    :param ysplit: height of splits
    :type ysplit: int
    :returns: A list of splits represented by arrays of 4 elements [xmin, ymin, xmax, ymax]
    :type list of 4 float
    """
    nb_xsplits = math.ceil((xmax - xmin) / xsplit)
    nb_ysplits = math.ceil((ymax - ymin) / ysplit)

    terrain_regions = []

    for i in range(0, nb_xsplits):
        for j in range(0, nb_ysplits):
            region = [xmin + i * xsplit,
                      ymin + j * ysplit,
                      xmin + (i + 1) * xsplit,
                      ymin + (j + 1) * ysplit]

            # Crop to largest region
            region = crop(region, [xmin, ymin, xmax, ymax])

            terrain_regions.append(region)

    return terrain_regions


def crop(region1, region2):
    """
    Crop a region by another one

    :param region1: The region to crop as an array [xmin, ymin, xmax, ymax]
    :type region1: list of four float
    :param region2: The region used for cropping as an array [xmin, ymin, xmax, ymax]
    :type region2: list of four float
    :returns: The cropped regiona as an array [xmin, ymin, xmax, ymax]. If region1 is outside region2, might result in inconsistent region
    :rtype: list of four float
    """
    out = region1[:]
    out[0] = min(region2[2], max(region2[0], region1[0]))
    out[2] = min(region2[2], max(region2[0], region1[2]))
    out[1] = min(region2[3], max(region2[1], region1[1]))
    out[3] = min(region2[3], max(region2[1], region1[3]))

    return out


def pad(region, margins):
    """
    Pad region according to a margin

    :param region: The region to pad
    :type region: list of four floats
    :param margins: Margin to add
    :type margins: list of four floats
    :returns: padded region
    :rtype: list of four float
    """
    out = region[:]
    out[0] -= margins[0]
    out[1] -= margins[1]
    out[2] += margins[2]
    out[3] += margins[3]

    return out


def empty(region):
    """
    Check if a region is empty or inconsistent

    :param region: region as an array [xmin, ymin, xmax, ymax]
    :type region: list of four float
    :returns: True if the region is considered empty (no pixels inside), False otherwise
    :rtype: bool
"""
    return region[0] >= region[2] or region[1] >= region[3]


def union(regions):
    """
    Returns union of all regions

    :param regions: list of region as an array [xmin, ymin, xmax, ymax]
    :type regions: list of list of four float
    :returns: xmin, ymin, xmax, ymax
    :rtype: list of 4 float
    """

    xmin = min([r[0] for r in regions])
    xmax = max([r[2] for r in regions])
    ymin = min([r[1] for r in regions])
    ymax = max([r[3] for r in regions])

    return xmin, ymin, xmax, ymax


def list_tiles(region, largest_region, tile_size, margin=1):
    """
    Given a region, cut largest_region into tiles of size tile_size
    and return tiles that intersect region within margin pixels.

    :param region: The region to list intersecting tiles
    :type region: list of four float
    :param largest_region: The region to split
    :type largest_region: list of four float
    :param tile_size: Width of tiles for splitting (squared tiles)
    :type tile_size: int
    :param margin: Also include margin neighboring tiles
    :type margin: int
    :returns: A list of tiles as arrays of [xmin, ymin, xmax, ymax]
    :rtype: list of 4 float
    """
    # Find tile indices covered by region
    min_tile_idx_x = int(math.floor(region[0] / tile_size))
    max_tile_idx_x = int(math.ceil(region[2] / tile_size))
    min_tile_idx_y = int(math.floor(region[1] / tile_size))
    max_tile_idx_y = int(math.ceil(region[3] / tile_size))

    # Include additional tiles
    min_tile_idx_x -= margin
    min_tile_idx_y -= margin
    max_tile_idx_x += margin
    max_tile_idx_y += margin

    out = []

    # Loop on tile idx
    for x in range(min_tile_idx_x, max_tile_idx_x):
        for y in range(min_tile_idx_y, max_tile_idx_y):

            # Derive tile coordinates
            tile = [x * tile_size, y * tile_size,
                    (x + 1) * tile_size, (y + 1) * tile_size]

            # Crop to largest region
            tile = crop(tile, largest_region)

            # Check if tile is emtpy
            if not empty(tile):
                out.append(tile)

    return out


def roi_to_start_and_size(region, resolution):
    """
    Convert roi as array of [xmin, ymin, xmax, ymax] to xmin, ymin, xsize, ysize given a resolution

    Beware that a negative spacing is considered for y axis, and thus returned ystart is in fact ymax

    :param region: The region to convert
    :type region: list of four float
    :param resolution: The resolution to use to determine sizes
    :type resolution: float
    :returns: xmin, ymin, xsize, ysize tuple
    :rtype: list of two float + two int
    """
    xstart = region[0]
    ystart = region[3]
    xsize = math.ceil((region[2] - region[0]) / resolution)
    ysize = math.ceil((region[3] - region[1]) / resolution)

    return xstart, ystart, xsize, ysize


def ground_polygon_from_envelopes(
        poly_envelope1,
        poly_envelope2,
        epsg1,
        epsg2,
        tgt_epsg=4326):
    """
    compute the ground polygon of the intersection of two envelopes

    :raise: Exception when the envelopes don't intersect one to each other

    :param poly_envelope1: path to the first envelope
    :type poly_envelope1: Polygon
    :param poly_envelope2: path to the second envelope
    :type poly_envelope2: Polygon
    :param epsg1: EPSG code of poly_envelope1
    :type epsg1: int
    :param epsg2: EPSG code of poly_envelope2
    :type epsg2: int
    :param tgt_epsg: EPSG code of the new projection (default value is set to 4326)
    :type tgt_epsg: int
    :return: a tuple with the shapely polygon of the intersection and the intersection's bounding box (described by a
    tuple (minx, miny, maxx, maxy))
    :rtype: Tuple[polygon, Tuple[int, int, int, int]]
    """
    # project to the correct epsg if necessary
    if epsg1 != tgt_epsg:
        poly_envelope1 = projection.polygon_projection(
            poly_envelope1, epsg1, tgt_epsg)

    if epsg2 != tgt_epsg:
        poly_envelope2 = projection.polygon_projection(
            poly_envelope2, epsg2, tgt_epsg)

    # intersect both envelopes
    if poly_envelope1.intersects(poly_envelope2):
        inter = poly_envelope1.intersection(poly_envelope2)
    else:
        raise Exception('The two envelopes do not intersect one another')

    return inter, inter.bounds


def snap_to_grid(xmin, ymin, xmax, ymax, resolution):
    """
    Given a roi as xmin, ymin, xmax, ymax, snap values to entire step
    of resolution

    :param xmin: xmin of the roi
    :type xmin: float
    :param ymin: ymin of the roi
    :type ymin: float
    :param xmax: xmax of the roi
    :type xmax: float
    :param ymax: ymax of the roi
    :type ymax: float
    :param resolution: size of cells for snapping
    :type resolution: float
    :returns: xmin, ymin, xmax, ymax snapped tuple
    :type: list of four float
    """
    xmin = math.floor(xmin / resolution) * resolution
    xmax = math.ceil(xmax / resolution) * resolution
    ymin = math.floor(ymin / resolution) * resolution
    ymax = math.ceil(ymax / resolution) * resolution

    return xmin, ymin, xmax, ymax
