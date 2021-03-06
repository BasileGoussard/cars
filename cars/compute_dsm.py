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
This module contains the functions associated to the compute_dsm cars_cli sub-command
"""


# Standard imports
from __future__ import print_function

from typing import List, Tuple, Dict
import os
import logging
import errno
import math
import time
from glob import glob
import multiprocessing

# Third party imports
import numpy as np
from scipy.spatial import Delaunay, tsearch, cKDTree
from tqdm import tqdm
from json_checker import CheckerError
import dask
from osgeo import gdal, osr
from shapely.geometry import Polygon
import xarray as xr

# Cars imports
from cars import stereo
from cars import rasterization
from cars import parameters as params
from cars import configuration as static_cfg
from cars import tiling
from cars import utils
from cars import projection
from cars import readwrite
from cars import preprocessing
from cars.cluster import start_local_cluster, start_cluster, stop_cluster, ComputeDSMMemoryLogger


def region_hash_string(region):
    '''This lambda will allow to derive a key to index region in the previous dictionnary'''
    return "{}_{}_{}_{}".format(region[0],region[1],region[2],region[3])


def write_3d_points(configuration, region, corr_config, tmp_dir, config_id, **kwargs):
    '''
    Wraps the call to stereo.images_pair_to_3d_points and write down the output
    points and colors

    :param configuration: configuration values
    :param region: region to process
    :param corr_config: correlator configuration
    :param tmp_dir: temporary directory to store outputs
    :param config_id: id of the pair to process
    '''
    config_id_dir = os.path.join(tmp_dir, config_id)
    hashed_region = region_hash_string(region)
    points_dir = os.path.join(config_id_dir, "points")
    points_file = os.path.join(points_dir, "{}.nc".format(hashed_region))
    color_dir = os.path.join(config_id_dir, "color")
    color_file = os.path.join(color_dir, "{}.nc".format(hashed_region))
    # Compute 3d points and colors
    points, colors = stereo.images_pair_to_3d_points(configuration, region, corr_config, **kwargs)

    # Create output directories
    utils.safe_makedirs(points_dir)
    utils.safe_makedirs(color_dir)

    # Write to netcdf the 3d points and color, for both 'ref' and 'sec' modes
    # output paths end with '_ref.nc' or '_sec.nc' 
    out_points = {}
    out_colors = {}
    for key in points:
        outPath = os.path.join(points_dir, "{}_{}.nc".format(hashed_region,key))
        points[key].to_netcdf(outPath)
        out_points[key] = outPath
    for key in colors:
        outPath = os.path.join(color_dir, "{}_{}.nc".format(hashed_region,key))
        colors[key].to_netcdf(outPath)
        out_colors[key] = outPath
    # outputs are the temporary files paths
    return out_points, out_colors


def write_dsm_by_tile(clouds_and_colors_as_str_list, resolution, epsg, tmp_dir, nb_bands, color_dtype, output_stats, **kwargs):
    '''
    Wraps the call to rasterization_wrapper and write the dsm tile on disk

    :param clouds_and_colors_as_str_list: paths to 3d points and colors to rasterize
    :param resolution: output DSM resolution
    :param epsg: EPSG code of output DSM
    :param tmp_dir: directory to store output DSM tiles
    :param nb_bands: number of bands in color image
    :param output_stats: True if we save statistics with DSM tiles
    '''
    # replace paths by opened Xarray datasets
    xr_open_dict = lambda x: dict([(name, xr.open_dataset(value)) for name, value in x.items()])
    clouds_and_colors_as_xr_list = [(xr_open_dict(k[0]), xr_open_dict(k[1])) for k in clouds_and_colors_as_str_list]

    # kwargs contains all the keyword arguments that will be passed to rasterization_wrapper
    # we need to extract a few of them
    xstart = kwargs.get('xstart')
    ystart = kwargs.get('ystart')
    xsize = kwargs.get('xsize')
    ysize = kwargs.get('ysize')

    dsm_nodata = kwargs.get('dsm_no_data')
    color_nodata = kwargs.get('color_no_data')

    hashed_region = region_hash_string([xstart,ystart,xsize,ysize])

    # call to rasterization_wrapper
    dsm = rasterization_wrapper(clouds_and_colors_as_xr_list, resolution, epsg, **kwargs)

    # compute tile bounds
    tile_bounds = [xstart, ystart - resolution*ysize,xstart +resolution*xsize , ystart]

    # write DSM tile as geoTIFF
    readwrite.write_geotiff_dsm([dsm], tmp_dir, xsize, ysize, tile_bounds,
                                resolution, epsg, nb_bands, dsm_nodata, color_nodata, color_dtype = color_dtype, write_color=True, write_stats=output_stats, prefix=hashed_region+'_')

    return hashed_region

def rasterization_wrapper(clouds_and_colors, resolution, epsg, **kwargs):
    """
    Wrapper for rasterization step.

    This function allow to convert a list of cloud to correct EPSG and rasterize it with associated colors.

    :param clouds_and_colors: list of tuple (cloud, colors)
    :type clouds_and_colors: list of pair of xarray
    :param resolution: resolution of DSM to produce (in meter, degree... [depends on epsg code])
    :type resolution: float
    :param  epsg_code: epsg code for the CRS of the output DSM
    :type epsg_code: int
    :return: digital surface model + projected colors
    :rtype: xarray 2d tuple
    """
    # Unpack list of clouds from tuple, and project them to correct EPSG if
    # needed
    clouds = [v[0]['ref'] for v in clouds_and_colors]

    # Unpack list of colors alike
    colors = [v[1]['ref'] for v in clouds_and_colors]

    # Add clouds and colors computed from the secondary disparity map
    if 'sec' in clouds_and_colors[0][0]:
        cloud_sec = [v[0]['sec'] for v in clouds_and_colors]
        clouds.extend(cloud_sec)

        color_sec = [v[1]['sec'] for v in clouds_and_colors]
        colors.extend(color_sec)

    # Call simple_rasterization
    return rasterization.simple_rasterization_dataset(clouds, resolution, epsg, colors, **kwargs)


def run(
        in_jsons: List[params.preprocessing_content_type],
        out_dir: str,
        resolution: float=0.5,
        min_elevation_offset: float=None,
        max_elevation_offset: float=None,
        epsg: int=None,
        sigma: float=None,
        dsm_radius: int=1,
        dsm_no_data: int=-32768,
        color_no_data: int=0,
        corr_config: Dict=None,
        output_stats: bool=False,
        mode: str="local_dask",
        nb_workers: int=4,
        walltime: str="00:59:00",
        roi: Tuple[List[int], int]=None,
        use_geoid_alt: bool=False,
        use_sec_disp: bool=False,
        snap_to_img1: bool=False,
        align: bool=False,
        cloud_small_components_filter:bool=True,
        cloud_statistical_outliers_filter:bool=True,
        epi_tile_size: int=None):
    """
    Main function for the compute_dsm subcommand

    This function will compute independent tiles of the final DSM, with the following steps:

    1. Epipolar resampling (including mask)
    2. Disparity map estimation
    3. Triangulation of disparity map
    4. Rasterization to DSM

    :param in_jsons: dictionaries describing the input pair (as produced by cars_preproc tool)
    :param out_dir: directory where output raster and color images will be written
    :param resolution: resolution of DSM to produce
    :param min_elevation_offset: Override minimum disparity from prepare step with this offset in meters
    :param max_elevation_offset: Override maximum disparity from prepare step with this offset in meters
    :param epsg: epsg code for the CRS of the output DSM
    :param sigma: width of gaussian weight for rasterization
    :param dsm_radius: Radius around a cell for gathering points for rasterization
    :param dsm_no_data: No data value to use in the final DSM file
    :param color_no_data: No data value to use in the final colored image
    :param corr_config: Correlator configuration
    :param output_stats: flag, if true, outputs dsm as a geotiff file with quality statistics.
    :param mode: Parallelization mode
    :param nb_workers: Number of dask workers to use for the sift matching step
    :param walltime: Walltime of the dask workers
    :param roi: DSM ROI in final projection with the corresponding epsg code ([xmin, ymin, xmax, ymax], roi_epsg))
    (roi_epsg can be set to None if the ROI is in final projection)
    :param use_geoid_alt: Wheter altitude should be computed wrt geoid height or not.
    :param use_sec_disp: Boolean activating the use of the secondary disparity map
    :param snap_to_img1: If this is True, Lines of Sight of img2 are moved so as to cross those of img1
    :param align: If this is True, use the correction estimated during prepare to align to lowres DEM (if available)
    :param cloud_small_components_filter: Boolean activating the points cloud small components filtering. The filter's
    parameters are set in the static configuration json.
    :param cloud_statistical_outliers_filter: Boolean activating the points cloud statistical outliers filtering.
    The filter's parameters are set in the static configuration json.
    :param epi_tile_size: Force the size of epipolar tiles (None by default)
    """
    out_dir = os.path.abspath(out_dir)
    # Ensure that outdir exists
    try:
        os.makedirs(out_dir)
    except OSError as exc:
        if exc.errno == errno.EEXIST and os.path.isdir(out_dir):
            pass
        else:
            raise
    tmp_dir = os.path.join(out_dir, 'tmp')

    utils.add_log_file(out_dir, 'compute_dsm')
    logging.info(
        "Received {} stereo pairs configurations".format(len(in_jsons)))

    # Retrieve static parameters (rasterization and cloud filtering)
    static_params = static_cfg.get_cfg()

    # Initiate ouptut json dictionary
    out_json = {
        params.stereo_inputs_section_tag: [],
        params.stereo_section_tag:
        {
            params.stereo_version_tag: utils.get_version(),
            params.stereo_parameters_section_tag:
            {
                params.resolution_tag: resolution,
                params.sigma_tag: sigma,
                params.dsm_radius_tag: dsm_radius
            },
            params.static_params_tag: static_params[static_cfg.compute_dsm_tag],
            params.stereo_output_section_tag: {}
        }
    }

    if use_geoid_alt:
        geoid_data = utils.read_geoid_file()
        out_json[params.stereo_section_tag][params.stereo_output_section_tag][params.alt_reference_tag] = 'geoid'
    else:
        geoid_data = None
        out_json[params.stereo_section_tag][params.stereo_output_section_tag][
            params.alt_reference_tag] = 'ellipsoid'


    if epsg is not None:
        out_json[params.stereo_section_tag][params.stereo_parameters_section_tag][params.epsg_tag] = epsg

    roi_epsg = None
    if roi is not None:
        (roi_xmin, roi_ymin, roi_xmax, roi_ymax), roi_epsg = roi
        roi_poly = Polygon([(roi_xmin, roi_ymin), (roi_xmax, roi_ymin),
                            (roi_xmax, roi_ymax), (roi_xmin, roi_ymax), (roi_xmin, roi_ymin)])

    # set the timeout for each job in multiprocessing mode (in seconds)
    perJobTimeout = 600

    configurations_data = {}

    config_idx = 1

    ref_left_image = None

    for in_json in in_jsons:
        # Build config id
        config_id = "config_{}".format(config_idx)

        # Check configuration with respect to schema
        configuration = utils.check_json(
            in_json, params.preprocessing_content_schema)

        preprocessing_output_config = configuration[
            params.preprocessing_section_tag][params.preprocessing_output_section_tag]

        # Append input configuration to output json
        out_json[params.stereo_inputs_section_tag].append(configuration)

        configurations_data[config_id] = {}

        configurations_data[config_id]['configuration'] = configuration
        
        # Check left image and raise a warning if different left images are used along with snap_to_img1 mpode
        if ref_left_image is None:
            ref_left_image = configuration[params.input_section_tag][params.img1_tag]
        else:
            if snap_to_img1 and ref_left_image != configuration[params.input_section_tag][params.img1_tag]:
                logging.warning("--snap_to_left_image mode is used but input configurations have different images as their left image in pair. This may result in increasing registration discrepencies between pairs")

        # Get largest epipolar regions from configuration file
        largest_epipolar_region = [0,
                                   0,
                                   preprocessing_output_config[params.epipolar_size_x_tag],
                                   preprocessing_output_config[params.epipolar_size_y_tag]]

        configurations_data[config_id]['largest_epipolar_region'] = largest_epipolar_region

        disp_min = preprocessing_output_config[params.minimum_disparity_tag]
        disp_max = preprocessing_output_config[params.maximum_disparity_tag]
        disp_to_alt_ratio = preprocessing_output_config[params.disp_to_alt_ratio_tag]

        # Check if we need to override disp_min
        if min_elevation_offset is not None:
            user_disp_min = min_elevation_offset / disp_to_alt_ratio
            if user_disp_min > disp_min:
                logging.warning(
                    ('Overriden disparity minimum = {:.3f} pix. (or {:.3f} m.) is greater '
                     'than disparity minimum estimated in prepare step = {:.3f} pix. (or '
                     '{:.3f} m.) for configuration {}').format(
                        user_disp_min,
                        min_elevation_offset,
                        disp_min,
                        disp_min * disp_to_alt_ratio,
                        config_id))
                disp_min = user_disp_min

        # Check if we need to override disp_max
        if max_elevation_offset is not None:
            user_disp_max = max_elevation_offset / disp_to_alt_ratio
            if user_disp_max < disp_max:
                logging.warning(
                    ('Overriden disparity maximum = {:.3f} pix. (or {:.3f} m.) is lower '
                     'than disparity maximum estimated in prepare step = {:.3f} pix. (or '
                     '{:.3f} m.) for configuration {}').format(
                        user_disp_max,
                        max_elevation_offset,
                        disp_max,
                        disp_max * disp_to_alt_ratio,
                        config_id))
            disp_max = user_disp_max

        logging.info(
            'Disparity range for config {}: [{:.3f} pix., {:.3f} pix.] (or [{:.3f} m., {:.3f} m.])'.format(
                config_id,
                disp_min,
                disp_max,
                disp_min *
                disp_to_alt_ratio,
                disp_max *
                disp_to_alt_ratio))

        configurations_data[config_id]['disp_min'] = disp_min
        configurations_data[config_id]['disp_max'] = disp_max

        origin = [preprocessing_output_config[params.epipolar_origin_x_tag],
                  preprocessing_output_config[params.epipolar_origin_y_tag]]
        spacing = [preprocessing_output_config[params.epipolar_spacing_x_tag],
                   preprocessing_output_config[params.epipolar_spacing_y_tag]]

        configurations_data[config_id]['origin'] = origin
        configurations_data[config_id]['spacing'] = spacing

        logging.info("Size of epipolar image: {}".format(
            largest_epipolar_region))
        logging.debug("Origin of epipolar grid: {}".format(origin))
        logging.debug("Spacing of epipolar grid: {}".format(spacing))

        # Warning if align is set but correction is missing
        if align and params.lowres_dem_splines_fit_tag not in preprocessing_output_config:
            logging.warning(('Align with low resolution DSM option is set but splines correction file '
                            'is not available for configuration {}. Correction '
                            'will not be applied for this configuration').format(config_id))

        # Numpy array with corners of largest epipolar region. Order
        # does not matter here, since it will be passed to stereo.compute_epipolar_grid_min_max
        corners = np.array([[[largest_epipolar_region[0],largest_epipolar_region[1]],
                             [largest_epipolar_region[0], largest_epipolar_region[3]]], 
                            [[largest_epipolar_region[2], largest_epipolar_region[3]], 
                             [largest_epipolar_region[2], largest_epipolar_region[1]]]], dtype=np.float64)

        # get utm zone with the middle point of terrain_min if epsg is
        # None
        if epsg is None:
            # Compute terrain position of epipolar image corners for min and max disparity
            terrain_dispmin, terrain_dispmax = stereo.compute_epipolar_grid_min_max(corners, 4326, configuration, disp_min, disp_max)
            epsg = rasterization.get_utm_zone_as_epsg_code(
                *np.mean(terrain_dispmin, axis=0))
            logging.info("EPSG code: {}".format(epsg))

        # Compute terrain min and max again, this time using estimated epsg code
        terrain_dispmin, terrain_dispmax = stereo.compute_epipolar_grid_min_max(corners, epsg, configuration, disp_min, disp_max)


        if roi_epsg is not None:
            if roi_epsg != epsg:
                roi_poly = projection.polygon_projection(roi_poly, roi_epsg, epsg)

        # Compute bounds from epipolar image corners and dispmin/dispmax
        terrain_bounds = np.stack((terrain_dispmin, terrain_dispmax), axis=0)
        terrain_min = np.amin(terrain_bounds, axis=(0,1))
        terrain_max = np.amax(terrain_bounds, axis=(0,1))        

        terrain_area = (terrain_max[0]-terrain_min[0])*(terrain_max[1]-terrain_min[1])

        configurations_data[config_id]['terrain_area'] = terrain_area

        logging.info(
            "Terrain area covered: {} square meters (or square degrees)".format(terrain_area))

        # Retrieve bounding box of the ground intersection of the envelopes
        inter_poly, inter_epsg = utils.read_vector(
            preprocessing_output_config[params.envelopes_intersection_tag])

        if epsg != inter_epsg:
            inter_poly = projection.polygon_projection(inter_poly, inter_epsg, epsg)

        (inter_xmin, inter_ymin, inter_xmax, inter_ymax) = inter_poly.bounds

        # Align bounding box to integer resolution steps
        xmin, ymin, xmax, ymax = tiling.snap_to_grid(
            inter_xmin, inter_ymin, inter_xmax, inter_ymax, resolution)

        logging.info("Terrain bounding box : [{}, {}] x [{}, {}]".format(xmin,
                                                                         xmax,
                                                                         ymin,
                                                                         ymax))

        configurations_data[config_id]['terrain_bounding_box'] = [
            xmin, ymin, xmax, ymax]

        if roi is not None:
            if not roi_poly.intersects(inter_poly):
                logging.warning("The pair composed of {} and {} does not intersect the requested ROI".format(
                    configuration[params.input_section_tag][params.img1_tag],
                    configuration[params.input_section_tag][params.img2_tag]
                ))

        # Get optimal tile size
        if epi_tile_size is not None:
            opt_epipolar_tile_size = epi_tile_size
        else:
            opt_epipolar_tile_size = stereo.optimal_tile_size(
                disp_min,
                disp_max)
        logging.info(
            "Optimal tile size for epipolar regions: {}x{} pixels".format(
                opt_epipolar_tile_size,
                opt_epipolar_tile_size))

        configurations_data[config_id]['opt_epipolar_tile_size'] = opt_epipolar_tile_size

        # Split epipolar image in pieces
        epipolar_regions = tiling.split(0,
                                        0,
                                        preprocessing_output_config[params.epipolar_size_x_tag],
                                        preprocessing_output_config[params.epipolar_size_y_tag],
                                        opt_epipolar_tile_size,
                                        opt_epipolar_tile_size)
        epipolar_regions_grid = tiling.grid(0,
                                            0,
                                            preprocessing_output_config[params.epipolar_size_x_tag],
                                            preprocessing_output_config[params.epipolar_size_y_tag],
                                            opt_epipolar_tile_size,
                                            opt_epipolar_tile_size)
        
        configurations_data[config_id]['epipolar_regions'] = epipolar_regions
        configurations_data[config_id]['epipolar_regions_grid'] = epipolar_regions_grid

        logging.info("Epipolar image will be processed in {} splits".format(
            len(epipolar_regions)))

        # Increment config index
        config_idx += 1

    xmin, ymin, xmax, ymax = tiling.union(
        [conf['terrain_bounding_box'] for config_id, conf in configurations_data.items()])

    if roi is not None:
        # terrain bounding box polygon
        terrain_poly = Polygon(
            [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax), (xmin, ymin)])

        if not roi_poly.intersects(terrain_poly):
            raise Exception(
                'None of the input pairs intersect the requested ROI')
        else:
            logging.info('Setting terrain bounding box to the requested ROI')
            xmin, ymin, xmax, ymax = roi_poly.bounds
            xmin, ymin, xmax, ymax = tiling.snap_to_grid(
                xmin, ymin, xmax, ymax, resolution)

    logging.info(
        "Total terrain bounding box : [{}, {}] x [{}, {}]".format(
            xmin, xmax, ymin, ymax))

    # Compute optimal terrain tile size
    optimal_terrain_tile_widths = []

    for config_id, conf in configurations_data.items():
        # Compute terrain area covered by a single epipolar tile
        terrain_area_covered_by_epipolar_tile = conf["terrain_area"] / len(
            epipolar_regions)

        # Compute tile width in pixels
        optimal_terrain_tile_widths.append(
            math.sqrt(terrain_area_covered_by_epipolar_tile))

    # In case of multiple json configuration, take the average optimal size,
    # and align to multiple of resolution
    optimal_terrain_tile_width = int(math.ceil(
        np.mean(optimal_terrain_tile_widths) / resolution)) * resolution

    logging.info("Optimal terrain tile size: {}x{} pixels".format(int(
        optimal_terrain_tile_width / resolution), int(optimal_terrain_tile_width / resolution)))

    # Split terrain bounding box in pieces
    terrain_grid = tiling.grid(
        xmin,
        ymin,
        xmax,
        ymax,
        optimal_terrain_tile_width,
        optimal_terrain_tile_width)
    number_of_terrain_splits = (
        terrain_grid.shape[0] - 1) * (terrain_grid.shape[1] - 1)

    logging.info("Terrain bounding box will be processed in {} splits".format(
        number_of_terrain_splits))

    # Start dask cluster
    cluster = None
    client = None

    # Use dask
    use_dask = {"local_dask":True, "pbs_dask":True, "mp":False}
    if mode not in use_dask.keys():
        raise NotImplementedError('{} mode is not implemented'.format(mode))

    if use_dask[mode]:
        if mode == "local_dask":
            cluster, client = start_local_cluster(nb_workers)
        else:
            cluster, client = start_cluster(nb_workers, walltime, out_dir)

        # Add plugin to monitor memory of workers
        plugin = ComputeDSMMemoryLogger(out_dir)
        client.register_worker_plugin(plugin)

        geoid_data_futures = None
        if geoid_data is not None:
            # Broadcast geoid data to all dask workers
            geoid_data_futures = client.scatter(geoid_data,broadcast=True)

    # Retrieve the epsg code which will be used for the triangulation's output points clouds
    # (ecef if filters are activated)
    if cloud_small_components_filter or cloud_statistical_outliers_filter:
        stereo_out_epsg = 4978
    else:
        stereo_out_epsg = epsg

    # Submit all epipolar regions to be processed as delayed tasks, and
    # project terrain grid to epipolar
    for config_id, conf in configurations_data.items():
        # This list will hold the different epipolar tiles to be
        # processed as points cloud
        delayed_point_clouds = []

        if use_dask[mode]:
            # Use Dask delayed
            for region in conf['epipolar_regions']:
                delayed_point_clouds.append(dask.delayed(stereo.images_pair_to_3d_points)(
                    conf['configuration'], region, corr_config, disp_min=conf[
                        'disp_min'], disp_max=conf['disp_max'],
                    geoid_data=geoid_data_futures, out_epsg=stereo_out_epsg,
                    use_sec_disp=use_sec_disp, snap_to_img1 = snap_to_img1, align=align))
            logging.info(
                "Submitted {} epipolar delayed tasks to dask for stereo configuration {}".format(
                len(delayed_point_clouds), config_id))
        else:
            # Use multiprocessing module

            # create progress bar with an update callback
            pbar = tqdm(total=len(conf['epipolar_regions']))
            def update(args): pbar.update()

            # create a thread pool 
            pool = multiprocessing.Pool(nb_workers)

            # launch several 'write_3d_points()' to process each epipolar region
            for region in conf['epipolar_regions']:
                delayed_point_clouds.append(pool.apply_async(write_3d_points,
                    args=(conf['configuration'], region, corr_config,
                          tmp_dir, config_id),
                    kwds={'disp_min':conf['disp_min'],
                          'disp_max':conf['disp_max'],
                          'geoid_data':geoid_data,
                          'out_epsg':stereo_out_epsg,
                          'use_sec_disp':use_sec_disp},
                    callback=update))

            # Wait computation results (timeout in seconds) and replace the
            # async objects by the actual output of write_3d_points(), meaning
            # the paths to cloud files
            delayed_point_clouds = [delayed_pc.get(timeout=perJobTimeout) for delayed_pc in delayed_point_clouds]

            # closing thread pool when computation is done
            pool.close()
            pool.join()

        configurations_data[config_id]['delayed_point_clouds'] = delayed_point_clouds

        # build list of epipolar region hashes
        configurations_data[config_id]['epipolar_regions_hash'] = [
            region_hash_string(k) for k in conf['epipolar_regions']]

        # Compute disp_min and disp_max location for epipolar grid
        epipolar_grid_min, epipolar_grid_max = stereo.compute_epipolar_grid_min_max(conf['epipolar_regions_grid'], epsg, conf["configuration"],conf['disp_min'], conf['disp_max'])

        epipolar_regions_grid_flat = conf['epipolar_regions_grid'].reshape(-1, conf['epipolar_regions_grid'].shape[-1])

        # in the following code a factor is used to increase the precision
        spatial_ref = osr.SpatialReference()
        spatial_ref.ImportFromEPSG(epsg)
        if spatial_ref.IsGeographic():
            precision_factor = 1000.0
        else:
            precision_factor = 1.0

        # Build delaunay_triangulation
        delaunay_min = Delaunay(epipolar_grid_min*precision_factor)
        delaunay_max = Delaunay(epipolar_grid_max*precision_factor)

        # Build kdtrees
        tree_min = cKDTree(epipolar_grid_min*precision_factor)
        tree_max = cKDTree(epipolar_grid_max*precision_factor)
        
        # Look-up terrain_grid with Delaunay
        s_min = tsearch(delaunay_min, terrain_grid*precision_factor)
        s_max = tsearch(delaunay_max, terrain_grid*precision_factor)

        points_disp_min = epipolar_regions_grid_flat[delaunay_min.simplices[s_min]]
        points_disp_max = epipolar_regions_grid_flat[delaunay_max.simplices[s_max]]
        nn_disp_min = epipolar_regions_grid_flat[tree_min.query(terrain_grid*precision_factor)[1]]
        nn_disp_max = epipolar_regions_grid_flat[tree_max.query(terrain_grid*precision_factor)[1]]

        points_disp_min_min = np.min(points_disp_min, axis=2)
        points_disp_min_max = np.max(points_disp_min, axis=2)
        points_disp_max_min = np.min(points_disp_max, axis=2)
        points_disp_max_max = np.max(points_disp_max, axis=2)

        # Use either Delaunay search or NN search if delaunay search fails (point outside triangles)
        points_disp_min_min = np.where(np.stack((s_min, s_min), axis=-1) != -1, points_disp_min_min, nn_disp_min)
        points_disp_min_max = np.where(np.stack((s_min, s_min), axis=-1) != -1, points_disp_min_max, nn_disp_min)
        points_disp_max_min = np.where(np.stack((s_max, s_max), axis=-1) != -1, points_disp_max_min, nn_disp_max)
        points_disp_max_max = np.where(np.stack((s_max, s_max), axis=-1) != -1, points_disp_max_max, nn_disp_max)
        
        points = np.stack((points_disp_min_min, points_disp_min_max,
                                 points_disp_max_min, points_disp_max_max), axis=0)
        
        points_min = np.min(points, axis=0)
        points_max = np.max(points, axis=0)

        configurations_data[config_id]['epipolar_points_min'] = points_min
        configurations_data[config_id]['epipolar_points_max'] = points_max

    # Retrieve number of bands
    if params.color1_tag in configuration[params.input_section_tag]:
        nb_bands = utils.rasterio_get_nb_bands(
            configuration[params.input_section_tag][params.color1_tag])
    else:
        logging.info('No color image has been given in input, {} will be used as the color image'.
                     format(configuration[params.input_section_tag][params.img1_tag]))
        nb_bands = utils.rasterio_get_nb_bands(
            configuration[params.input_section_tag][params.img1_tag])
    logging.info("Number of bands in color image: {}".format(nb_bands))

    rank = []

    # This list will contained the different raster tiles to be written by cars
    delayed_dsm_tiles = []
    number_of_epipolar_tiles_per_terrain_tiles = []

    if not use_dask[mode]:
        # create progress bar with update callback
        pbar = tqdm(
            total=number_of_terrain_splits,
            desc="Finding correspondences between terrain and epipolar tiles")
        def update(args): pbar.update()
        # initialize a thread pool for multiprocessing mode
        pool = multiprocessing.Pool(nb_workers)

    # Loop on terrain regions and derive dependency to epipolar regions
    for terrain_region_dix in tqdm(range(number_of_terrain_splits),
                                   total=number_of_terrain_splits,
                                   desc="Delaunay look-up"):

        j = int(terrain_region_dix / (terrain_grid.shape[1] - 1))
        i = terrain_region_dix % (terrain_grid.shape[1] - 1)

        logging.debug(
            "Processing tile located at {},{} in tile grid".format(i, j))

        terrain_region = [terrain_grid[j, i, 0], terrain_grid[j, i, 1],
                          terrain_grid[j + 1, i + 1, 0], terrain_grid[j + 1, i + 1, 1]]

        logging.debug(
            "Corresponding terrain region: {}".format(terrain_region))

        # This list will hold the required points clouds for this terrain tile
        required_point_clouds = []

        # For each stereo configuration
        for config_id, conf in configurations_data.items():

            epipolar_points_min = conf['epipolar_points_min']
            epipolar_points_max = conf['epipolar_points_max']

            tile_min = np.minimum(np.minimum(np.minimum(epipolar_points_min[j,i], epipolar_points_min[j+1 ,i]),
                                             np.minimum(epipolar_points_min[j+1,i+1], epipolar_points_min[j ,i+1])),
                                  np.minimum(np.minimum(epipolar_points_max[j,i], epipolar_points_max[j+1 ,i]),
                                             np.minimum(epipolar_points_max[j+1,i+1], epipolar_points_max[j ,i+1])))

            tile_max = np.maximum(np.maximum(np.maximum(epipolar_points_min[j,i], epipolar_points_min[j+1 ,i]),
                                             np.maximum(epipolar_points_min[j+1,i+1], epipolar_points_min[j ,i+1])),
                                  np.maximum(np.maximum(epipolar_points_max[j,i], epipolar_points_max[j+1 ,i]),
                                             np.maximum(epipolar_points_max[j+1,i+1], epipolar_points_max[j ,i+1])))


            # Bouding region of corresponding cell
            epipolar_region_minx = tile_min[0]
            epipolar_region_miny = tile_min[1]
            epipolar_region_maxx = tile_max[0]
            epipolar_region_maxy = tile_max[1]

            # This mimics the previous code that was using
            # transform_terrain_region_to_epipolar
            epipolar_region = [epipolar_region_minx, epipolar_region_miny,
                               epipolar_region_maxx, epipolar_region_maxy]

            # Crop epipolar region to largest region
            epipolar_region = tiling.crop(
                epipolar_region, conf['largest_epipolar_region'])

            logging.debug(
                "Corresponding epipolar region: {}".format(epipolar_region))

            # Check if the epipolar region contains any pixels to process
            if tiling.empty(epipolar_region):
                logging.debug(
                    "Skipping terrain region because corresponding epipolar region is empty")
            else:

                # Loop on all epipolar tiles covered by epipolar region
                for epipolar_tile in tiling.list_tiles(
                        epipolar_region,
                        conf['largest_epipolar_region'],
                        conf['opt_epipolar_tile_size']):

                    cur_hash = region_hash_string(epipolar_tile)

                    # Look for corresponding hash in delayed point clouds
                    # dictionnary
                    if cur_hash in conf['epipolar_regions_hash']:

                        # If hash can be found, append it to the required
                        # clouds to compute for this terrain tile
                        pos = conf['epipolar_regions_hash'].index(cur_hash)
                        required_point_clouds.append(
                            conf['delayed_point_clouds'][pos])


        # start and size parameters for the rasterization function
        xstart, ystart, xsize, ysize = tiling.roi_to_start_and_size(
            terrain_region, resolution)

        # cloud filtering params
        if cloud_small_components_filter:
            small_cpn_filter_params = static_cfg.get_small_components_filter_params()
        else:
            small_cpn_filter_params = None

        if cloud_statistical_outliers_filter:
            statistical_filter_params = static_cfg.get_statistical_outliers_filter_params()
        else:
            statistical_filter_params = None

        # rasterization grid division factor
        rasterization_params = static_cfg.get_rasterization_params()
        grid_points_division_factor = getattr(rasterization_params, static_cfg.grid_points_division_factor_tag)

        if len(required_point_clouds) > 0:
            logging.debug(
                "Number of clouds to process for this terrain tile: {}".format(
                    len(required_point_clouds)))

            if use_dask[mode]:
                # Delayed call to rasterization operations using all required
                # point clouds
                rasterized = dask.delayed(rasterization_wrapper)(
                    required_point_clouds, resolution, epsg, xstart=xstart,
                    ystart=ystart, xsize=xsize, ysize=ysize,
                    radius=dsm_radius, sigma=sigma, dsm_no_data=dsm_no_data,
                    color_no_data=color_no_data, small_cpn_filter_params=small_cpn_filter_params,
                    statistical_filter_params=statistical_filter_params,
                    grid_points_division_factor=grid_points_division_factor
                )

                # Keep track of delayed raster tiles
                delayed_dsm_tiles.append(rasterized)
                rank.append(i*i+j*j)

            else:
                # Launch asynchrone job for write_dsm_by_tile()
                delayed_dsm_tiles.append(pool.apply_async(write_dsm_by_tile,
                    args=(required_point_clouds,resolution,epsg,tmp_dir, nb_bands,
                          static_cfg.get_color_image_encoding(), output_stats),
                          kwds={'xstart':xstart, 'ystart':ystart, 'xsize': xsize, 'ysize': ysize, 'radius':dsm_radius,
                                'sigma':sigma, 'dsm_no_data':dsm_no_data, 'color_no_data':color_no_data,
                                'small_cpn_filter_params':small_cpn_filter_params,
                                'statistical_filter_params': statistical_filter_params,
                                'grid_points_division_factor': grid_points_division_factor},
                    callback=update))

            number_of_epipolar_tiles_per_terrain_tiles.append(
                len(required_point_clouds))


    logging.info("Average number of epipolar tiles for each terrain tile: {}".format(
        int(np.round(np.mean(number_of_epipolar_tiles_per_terrain_tiles)))))
    logging.info("Max number of epipolar tiles for each terrain tile: {}".format(
        np.max(number_of_epipolar_tiles_per_terrain_tiles)))

    bounds = (xmin, ymin, xmax, ymax)
    # Derive output image files parameters to pass to rasterio
    xsize, ysize = tiling.roi_to_start_and_size(
        [xmin, ymin, xmax, ymax], resolution)[2:]

    out_dsm = os.path.join(out_dir, "dsm.tif")
    out_clr = os.path.join(out_dir, "clr.tif")
    out_dsm_mean = os.path.join(out_dir, "dsm_mean.tif")
    out_dsm_std = os.path.join(out_dir, "dsm_std.tif")
    out_dsm_n_pts = os.path.join(out_dir, "dsm_n_pts.tif")
    out_dsm_points_in_cell  = os.path.join(out_dir, "dsm_pts_in_cell.tif")

    if use_dask[mode]:
        # Sort tiles according to rank
        delayed_dsm_tiles = [delayed for _, delayed in sorted(zip(rank,delayed_dsm_tiles), key=lambda pair: pair[0])]


        logging.info("Submitting {} tasks to dask".format(len(delayed_dsm_tiles)))
        # Transform all delayed raster tiles to futures (computation starts
        # immediatly on workers, assynchronously)
        future_dsm_tiles = client.compute(delayed_dsm_tiles)
    
        logging.info("DSM output image size: {}x{} pixels".format(xsize, ysize))

        readwrite.write_geotiff_dsm(future_dsm_tiles, out_dir, xsize, ysize,
                                    bounds, resolution, epsg, nb_bands, dsm_no_data,
                                    color_no_data, color_dtype = static_cfg.get_color_image_encoding(),
                                    write_color=True, write_stats=output_stats)

        # stop cluster
        stop_cluster(cluster, client)

    else:
        logging.info("Computing DSM tiles ...")
        # Wait for asynchrone jobs (timeout in seconds) and replace them by
        # write_dsm_by_tile() output
        delayed_dsm_tiles = [delayed_tile.get(timeout=perJobTimeout) for delayed_tile in delayed_dsm_tiles]

        # closing the tread pool after computation
        pool.close()
        pool.join()

        # vrt to tif
        logging.info("Building VRT")
        vrt_options = gdal.BuildVRTOptions(resampleAlg='nearest')

        def vrt_mosaic(tiles_glob, vrt_name, vrt_options, output):
            vrt_file = os.path.join(out_dir, vrt_name)
            tiles_list = glob(os.path.join(out_dir, 'tmp', tiles_glob))
            vrt = gdal.BuildVRT(vrt_file, tiles_list, options=vrt_options)
            vrt = None
            ds = gdal.Open(vrt_file)
            ds = gdal.Translate(output, ds)
            ds = None

        vrt_mosaic('*_dsm.tif', 'dsm.vrt', vrt_options, out_dsm)
        vrt_mosaic('*_clr.tif', 'clr.vrt', vrt_options, out_clr)

        if output_stats:
            vrt_mosaic('*_dsm_mean.tif', 'dsm_mean.vrt', vrt_options, out_dsm_mean)
            vrt_mosaic('*_dsm_std.tif', 'dsm_std.vrt', vrt_options, out_dsm_std)
            vrt_mosaic('*_dsm_n_pts.tif', 'dsm_n_pts.vrt', vrt_options, out_dsm_n_pts)
            vrt_mosaic('*_pts_in_cell.tif', 'dsm_pts_in_cell.vrt', vrt_options, out_dsm_points_in_cell)

    # Fill output json file
    out_json[params.stereo_section_tag][params.stereo_output_section_tag][params.epsg_tag] = epsg
    out_json[params.stereo_section_tag][params.stereo_output_section_tag][
    params.dsm_tag] = out_dsm
    out_json[params.stereo_section_tag][params.stereo_output_section_tag][
        params.dsm_no_data_tag] = float(dsm_no_data)
    out_json[params.stereo_section_tag][params.stereo_output_section_tag][
        params.color_no_data_tag] = float(color_no_data)
    out_json[params.stereo_section_tag][params.stereo_output_section_tag][
        params.color_tag] = out_clr

    if output_stats:
        out_json[params.stereo_section_tag][params.stereo_output_section_tag][
            params.dsm_mean_tag] = out_dsm_mean
        out_json[params.stereo_section_tag][params.stereo_output_section_tag][
            params.dsm_std_tag] = out_dsm_std
        out_json[params.stereo_section_tag][params.stereo_output_section_tag][
            params.dsm_n_pts_tag] = out_dsm_n_pts
        out_json[params.stereo_section_tag][params.stereo_output_section_tag][
            params.dsm_points_in_cell_tag] = out_dsm_points_in_cell

    # Write the output json
    out_json_path = os.path.join(out_dir, "content.json")

    try:
        utils.check_json(out_json, params.stereo_content_schema)
    except CheckerError as e:
        logging.warning(
            "content.json does not comply with schema: {}".format(e))

    params.write_stereo_content_file(out_json, out_json_path)
