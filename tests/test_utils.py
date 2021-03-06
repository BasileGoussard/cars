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

import os
import tempfile
import pytest

import xarray as xr
import fiona
from shapely.geometry import Polygon, shape

from cars import utils
from utils import absolute_data_path, temporary_dir


@pytest.mark.unit_tests
def test_ncdf_can_open():
    fake_path = '/here/im/not.nc'
    assert(not utils.ncdf_can_open(fake_path))


@pytest.mark.unit_tests
def test_rasterio_can_open():
    """
    """
    existing = absolute_data_path("input/phr_ventoux/left_image.tif")
    not_existing = "/stuff/dummy_file.doe"
    assert utils.rasterio_can_open(existing)
    assert not utils.rasterio_can_open(not_existing)


@pytest.mark.unit_tests
def test_otb_can_open():
    # existing
    existing_with_geom = absolute_data_path("input/phr_ventoux/left_image.tif")
    # existing with no geom file
    existing_no_geom = absolute_data_path("input/utils_input/im1.tif")
    not_existing = "/stuff/dummy_file.doe"

    assert utils.otb_can_open(existing_with_geom)
    assert not utils.otb_can_open(existing_no_geom)
    assert not utils.otb_can_open(not_existing)


@pytest.mark.unit_tests
def test_fix_shapely():
    poly, _ = utils.read_vector(absolute_data_path("input/utils_input/poly.gpkg"))
    assert poly.is_valid is False
    poly = poly.buffer(0)
    assert poly.is_valid is True


@pytest.mark.unit_tests
def test_read_vector():

    path_to_shapefile = absolute_data_path(
        "input/utils_input/left_envelope.shp")

    poly, epsg = utils.read_vector(path_to_shapefile)

    assert epsg == 4326
    assert isinstance(poly, Polygon)
    assert list(
        poly.exterior.coords) == [
        (5.193406138843349,
         44.20805805252155),
        (5.1965650939582435,
         44.20809526197842),
        (5.196654349708835,
         44.205901416036546),
        (5.193485218293437,
         44.205842790578764),
        (5.193406138843349,
         44.20805805252155)]

    # test exception
    with pytest.raises(Exception) as e:
        utils.read_vector('test.shp')
        assert str(e) == 'Impossible to read test.shp shapefile'


@pytest.mark.unit_tests
def test_write_vector():
    polys = [Polygon([(1.0, 1.0), (1.0, 2.0), (2.0, 2.0), (2.0, 1.0)]),
             Polygon([(2.0, 2.0), (2.0, 3.0), (3.0, 3.0), (3.0, 2.0)])]

    with tempfile.TemporaryDirectory(dir=temporary_dir()) as directory:
        path_to_file = os.path.join(directory, 'test.gpkg')
        utils.write_vector(polys, path_to_file, 4326)

        assert os.path.exists(path_to_file)

        nb_feat = 0
        for feat in fiona.open(path_to_file):
            poly = shape(feat['geometry'])
            nb_feat += 1
            assert poly in polys

        assert nb_feat == 2


@pytest.mark.unit_tests
def test_write_ply():
    """
    Test write ply file
    """
    points = xr.open_dataset(absolute_data_path(
        "input/intermediate_results/points_ref.nc"))
    utils.write_ply(os.path.join(temporary_dir(), 'test.ply'), points)
