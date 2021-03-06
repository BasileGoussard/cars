Notebooks
=========

Some notebooks are available in the ``notebooks`` directory of the cars project. They can be used to compute intermediary results and statistics using the cars API.

`Beware` : Notebooks needs preparation step's outputs that have to be generated first and inserted in the notebooks parameters at the beginning.



Compute DSM memory monitoring
-----------------------------

The ``compute_dsm_memory_monitoring.ipynb`` notebook shows how to load data and plot graph to monitor memory consumption during execution of CARS ``compute_dsm`` step with Dask.

The following parameters have to be set : 
    * ``compute_dsm_output_dir`` : The output folder of the compute DSM step 
    * ``nb_workers_per_pbs_jobs`` (Optional) : The number of workers process per pbs job (default : 2) 
    * ``nb_pbs_jobs`` : The number of pbs jobs (Number of workers divided by 'nb_workers_per_pbs_jobs')

Epipolar distributions
----------------------

The ``epipolar_distributions.ipynb`` notebook enables to visualize the distributions of the epipolar error and disparity estimated from the matches computed in the preparation step.

The following parameters have to be set : 
    * ``cars_home`` : Path to the cars folder.
    * ``content_dir`` :  Path to the directory containing the content.json file of the prepare step output.

low resolution DSM fitting
--------------------------

The ``lowres_dem_fit.ipynb`` notebook details how to estimate and apply the transform to fit A DSM to the low resolution initial DEM. This method is currently implemented in cars.

The following parameters have to be set : 
    * ``cars_home`` : Path to the cars folder.
    * ``content_dir`` : Path to the directory containing the content.json file of the prepare step output.


Step by step compute DSM
------------------------

The ``step_by_step_compute_dsm.ipynb`` notebook explains how to run step by step DSM computation with CARS, starting from the prepare step ouptut folder.

The following parameters have to be set : 
    * ``cars_home`` : Path to the cars folder.
    * ``content_dir`` : Path to the directory containing the content.json file of the prepare step output.
    * ``roi`` : ROI to process (path to a vector file, raster file or bounding box), as expected by cars_cli. 
    * ``output_dir`` : Path to output dir where to save figures and data.
