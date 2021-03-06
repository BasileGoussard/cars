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

import tempfile
import os
import pickle
import pytest

import numpy as np
import rasterio as rio
import xarray as xr

from utils import absolute_data_path, temporary_dir, assert_same_datasets
from cars import preprocessing
from cars import stereo
from cars import parameters as params


@pytest.mark.unit_tests
def test_generate_epipolar_grids():
    """
    Test generate_epipolar_grids method
    """
    img1 = absolute_data_path("input/phr_ventoux/left_image.tif")
    img2 = absolute_data_path("input/phr_ventoux/right_image.tif")
    dem = absolute_data_path("input/phr_ventoux/srtm")

    left_grid, right_grid, size_x, size_y, baseline = preprocessing.generate_epipolar_grids(
        img1, img2, dem)

    assert size_x == 612
    assert size_y == 612
    assert baseline == 0.7039416432380676

    # Uncomment to update baseline
    # left_grid.to_netcdf(absolute_data_path("ref_output/left_grid.nc"))

    left_grid_ref = xr.open_dataset(
        absolute_data_path("ref_output/left_grid.nc"))
    assert_same_datasets(left_grid, left_grid_ref)

    # Uncomment to update baseline
    # right_grid.to_netcdf(absolute_data_path("ref_output/right_grid.nc"))

    right_grid_ref = xr.open_dataset(
        absolute_data_path("ref_output/right_grid.nc"))
    assert_same_datasets(right_grid, right_grid_ref)


@pytest.mark.unit_tests
def test_dataset_matching():
    """
    Test dataset_matching method
    """
    region = [200, 250, 320, 400]
    img1 = absolute_data_path("input/phr_reunion/left_image.tif")
    img2 = absolute_data_path("input/phr_reunion/right_image.tif")
    mask1 = absolute_data_path("input/phr_reunion/left_mask.tif")
    mask2 = absolute_data_path("input/phr_reunion/right_mask.tif")
    nodata1 = 0
    nodata2 = 0
    grid1 = absolute_data_path("input/preprocessing_input/left_epipolar_grid_reunion.tif")
    grid2 = absolute_data_path("input/preprocessing_input/right_epipolar_grid_reunion.tif")

    epipolar_size_x = 596
    epipolar_size_y = 596

    left = stereo.resample_image(
        img1, grid1, [
            epipolar_size_x, epipolar_size_y], region=region, nodata=nodata1, mask=mask1)
    right = stereo.resample_image(
        img2, grid2, [
            epipolar_size_x, epipolar_size_y], region=region, nodata=nodata2, mask=mask2)

    matches = preprocessing.dataset_matching(left, right)

    # Uncomment to update baseline
    # np.save(absolute_data_path("ref_output/matches.npy"), matches)

    matches_ref = np.load(absolute_data_path("ref_output/matches.npy"))
    np.testing.assert_allclose(matches, matches_ref)

    # Case with no matches
    region = [0, 0, 2, 2]

    left = stereo.resample_image(
        img1, grid1, [
            epipolar_size_x, epipolar_size_y], region=region, nodata=nodata1, mask=mask1)
    right = stereo.resample_image(
        img1, grid1, [
            epipolar_size_x, epipolar_size_y], region=region, nodata=nodata1, mask=mask1)

    matches = preprocessing.dataset_matching(left, right)

    assert matches.shape == (0, 4)


@pytest.mark.unit_tests
def test_remove_epipolar_outliers():
    """
    Test remove epipolar outliers function
    """
    matches_file = absolute_data_path(
        "input/preprocessing_input/matches_reunion.npy")

    matches = np.load(matches_file)

    matches_filtered = preprocessing.remove_epipolar_outliers(matches)

    nb_filtered_points = matches.shape[0] - matches_filtered.shape[0]
    assert nb_filtered_points == 2


@pytest.mark.unit_tests
def test_compute_disparity_range():
    """
    Test compute disparity range function
    """
    matches_file = absolute_data_path(
        "input/preprocessing_input/matches_reunion.npy")

    matches = np.load(matches_file)

    matches_filtered = preprocessing.remove_epipolar_outliers(matches)
    dispmin, dispmax = preprocessing.compute_disparity_range(matches_filtered)

    assert dispmin == -3.1239416122436525
    assert dispmax == 3.820396270751972


@pytest.mark.unit_tests
def test_correct_right_grid():
    """
    Call right grid correction method and check outputs properties
    """
    matches_file = absolute_data_path(
        "input/preprocessing_input/matches_ventoux.npy")
    grid_file = absolute_data_path(
        "input/preprocessing_input/right_epipolar_grid_uncorrected_ventoux.tif")
    origin = [0, 0]
    spacing = [30, 30]

    matches = np.load(matches_file)
    matches = np.array(matches)

    matches_filtered = preprocessing.remove_epipolar_outliers(matches)

    with rio.open(grid_file) as rio_grid:
        grid = rio_grid.read()
        grid = np.transpose(grid, (1, 2, 0))

        corrected_grid, corrected_matches, in_stats, out_stats = preprocessing.correct_right_grid(
            matches_filtered, grid, origin, spacing)

        # Uncomment to update ref
        #np.save(absolute_data_path("ref_output/corrected_right_grid.npy"), corrected_grid)
        corrected_grid_ref = np.load(
            absolute_data_path("ref_output/corrected_right_grid.npy"))
        np.testing.assert_allclose(corrected_grid, corrected_grid_ref, atol=0.05, rtol=1.0e-6)

        assert corrected_grid.shape == grid.shape

        # Assert that we improved all stats
        assert abs(
            out_stats["mean_epipolar_error"][0]) < abs(
            in_stats["mean_epipolar_error"][0])
        assert abs(
            out_stats["mean_epipolar_error"][1]) < abs(
            in_stats["mean_epipolar_error"][1])
        assert abs(
            out_stats["median_epipolar_error"][0]) < abs(
            in_stats["median_epipolar_error"][0])
        assert abs(
            out_stats["median_epipolar_error"][1]) < abs(
            in_stats["median_epipolar_error"][1])
        assert out_stats["std_epipolar_error"][0] < in_stats["std_epipolar_error"][0]
        assert out_stats["std_epipolar_error"][1] < in_stats["std_epipolar_error"][1]
        assert out_stats["rms_epipolar_error"] < in_stats["rms_epipolar_error"]
        assert out_stats["rmsd_epipolar_error"] < in_stats["rmsd_epipolar_error"]

        # Assert absolute performances
       
        assert abs(out_stats["median_epipolar_error"][0]) < 0.1
        assert abs(out_stats["median_epipolar_error"][1]) < 0.1
        
        assert abs(out_stats["mean_epipolar_error"][0]) < 0.1
        assert abs(out_stats["mean_epipolar_error"][1]) < 0.1
        assert out_stats["rms_epipolar_error"] < 0.5

        # Assert corrected matches are corrected
        assert np.fabs(
            np.mean(corrected_matches[:, 1] - corrected_matches[:, 3])) < 0.1


@pytest.mark.unit_tests
def test_image_envelope():
    """
    Test image_envelope function
    """
    img = absolute_data_path("input/phr_ventoux/left_image.tif")
    dem = absolute_data_path("input/phr_ventoux/srtm")

    with tempfile.TemporaryDirectory(dir=temporary_dir()) as directory:
        shp = os.path.join(directory, "envelope.gpkg")
        preprocessing.image_envelope(img, shp, dem)
        assert os.path.isfile(shp)


@pytest.mark.unit_tests
def test_read_lowres_dem():
    """
    Test read_lowres_dem function
    """    
    dem = absolute_data_path("input/phr_ventoux/srtm")
    startx = 5.193458
    starty = 44.206671
    sizex = 100
    sizey = 100
    
    srtm_ds = preprocessing.read_lowres_dem(dem, startx, starty, sizex, sizey)

    # Uncomment to update baseline
    #srtm_ds.to_netcdf(absolute_data_path("ref_output/srtm_xt.nc"))

    srtm_ds_ref = xr.open_dataset(
        absolute_data_path("ref_output/srtm_xt.nc"))
    assert_same_datasets(srtm_ds, srtm_ds_ref)

    print(srtm_ds)


@pytest.mark.unit_tests
def test_get_time_ground_direction():
    """
    Test the get_time_ground_direction
    """

    # Force use of DEM if test is ran standalone
    dem = absolute_data_path("input/phr_ventoux/srtm")

    img = absolute_data_path("input/phr_ventoux/left_image.tif")
    vec = preprocessing.get_time_ground_direction(img,dem)

    assert vec[0] == -0.004640789411624955
    assert vec[1] ==  0.999989231478838


@pytest.mark.unit_tests
def test_project_coordinates_on_line():
    """
    Test project_coordinates_on_line
    """
    origin=[0,0]
    vec = [0.5, 0.5]

    x = np.array([1,2,3])
    y = np.array([1,2,3])

    coords = preprocessing.project_coordinates_on_line(x,y, origin, vec)

    np.testing.assert_allclose(coords, [1.41421356, 2.82842712, 4.24264069])


@pytest.mark.unit_tests
def test_lowres_initial_dem_splines_fit():
    """
    Test lowres_initial_dem_splines_fit
    """
    lowres_dsm_from_matches = xr.open_dataset(absolute_data_path("input/splines_fit_input/lowres_dsm_from_matches.nc"))
    lowres_initial_dem = xr.open_dataset(absolute_data_path("input/splines_fit_input/lowres_initial_dem.nc"))

    origin = [float(lowres_dsm_from_matches.x[0].values), float(lowres_dsm_from_matches.y[0].values)]
    vec = [0,1]

    splines = preprocessing.lowres_initial_dem_splines_fit(lowres_dsm_from_matches,
                                                           lowres_initial_dem,
                                                           origin,
                                                           vec)


    # Uncomment to update reference
    # with open(absolute_data_path("ref_output/splines_ref.pck"),'wb') as f:
    #     pickle.dump(splines,f)

    with open(absolute_data_path("ref_output/splines_ref.pck"),'rb') as f:
        ref_splines = pickle.load(f)
        np.testing.assert_allclose(splines.get_coeffs(), ref_splines.get_coeffs())
        np.testing.assert_allclose(splines.get_knots(), ref_splines.get_knots())
