# 1) General introduction to the workflow.
Version: 3

Maintained version: https://github.com/As337N/Cellprofiler_production_MAINCE.git

Data should be stored using the following three paths, all of them defined in the .env file:

- /workspace/ The path where all the files related to the container are stored, defined in the .env as WORKSPACE_PATH.
- /output/ The path where all output files are stored, defined in the .env as OUTPUT_PATH.
- /workspace_images/ The path where all images are stored, defined in the .env as IMAGES_PATH.

All configuration parameters can be modified in ".env" located at "/workspace/.env".

This workflow follows the following steps:

## I. Calculate illumination correction files per plate.
The first step is to adapt and run "/workspace/Pipelines/template_illum.cppipe" to calculate the correction files for the 5 main channels (DNA, RNA, ER, Mito, AGP) for each plate in the running batch.

This pipeline downscales the images to speed up the calculation of the correction function and later it upscales to the original size. The final files are saved as "PLATE_CHANNEL_illum.npy" in the path "/output/Illum_files/"

## II. Run CellPose cell segmentation on corrected images. [Optional]
First, we apply the illumination correction function on the RNA channel and after that we segment those corrected images with Cellpose-SAM. The pipelines in charge of these steps are "/workspace/Pipelines/template_illum_seg.cppipe" and the code is in "/workspace/Code/II_cellpose_seg.py". If CellProfiler segmentation is selected through the .env file, this module can be skipped to save computing.

The segmentation masks are stored in "/output/Cellpose_segmentations" as "IMAGENAME.png", where IMAGENAME is the name of the respective RNA tif/tiff file. This step should be run in a GPU cluster to reduce the computing time, otherwise it can take significantly longer to process.

## III. Run quality control (QC) on plate images and segmentations.
In this step, we run the QC CellProfiler pipeline (the template defined in the .env file) to create five images (one per channel) that visualize the output of the microscope per plate and the quality of the segmentations. All output data from this step is stored in "/output/QC"

## IV. Generate the QC report and plate collages.
First, the QC output is aggregated, and after that we generate an interactive HTML report (with the plate collages and overviews embedded) and a PDF summary to evaluate cell counts, toxicity or technical noise. The code for this step is in "/workspace/Code/qc/" and it uses the plate map of the cohort ("/workspace_images/platemap_COHORT.csv") to annotate the perturbations. The report files are stored in "/output/QC_collages":

- "COHORT_QC_report.html": self-contained interactive report.
- "COHORT_QC_summary.pdf": executive summary (skipped with "--no-pdf").
- "COHORT_flagged_sites.parquet": only the sites that fail any QC criterion with the default thresholds.

The QC code reads the dataset configuration from the .env (COHORT, CHANNEL_DICT, FILENAME_REGEX, WELLS_PER_PLATE). Main command-line options: "-i/--input", "-o/--output", "--qc", "--platemap" (if omitted, the first "platemap_*.csv" in the input folder is used), "--sites" (sites per well, default 9), "--n-sigma" (default 3.0, width of the adaptive MAD band used for the non-robust metrics), "--web-scale" and "--no-pdf".

The HTML report classifies every imaging site (and therefore every well) as passing or failing a set of quality criteria:

- **Criteria.** Blur (PowerLogLogSlope), MedianIntensity, Debris (PercentMaximal), Low cell density (Ratio P10/P90), Cells/Nuclei ratio and Cell count. A site fails when it breaks any *active* criterion; a well fails when any of its sites fails (or when its summed cell count is below the per-well minimum).
- **Robust z-scores use same-perturbation wells only.** For Blur and MedianIntensity, z = (x − median) / MAD is computed on *site* values against the sites of wells that share the same perturbation (compound in the platemap): within the same plate (plate z) and across all plates of the cohort (cohort z). A site fails if |z plate| **or** |z cohort| exceeds the fail threshold. A reference group needs at least 9 sites (one complete well, so *n* technical replicates give 9·*n* values); smaller groups produce no z-score and that half of the check is skipped (the report states how many wells lack a reference). Without a platemap, all wells are treated as a single perturbation.
- **Editable thresholds.** The *Cohort Summary* section has a panel to switch each criterion on/off and to edit its warning and fail thresholds. Defaults are defined in "qc/stats/thresholds.py" and can be restored with *Reset thresholds to defaults*. Changes update the summary, platemaps, plate overview and failed-sites list immediately. Warning thresholds only change colour; they never mark a failure.

| Criterion | Default fail rule |
|---|---|
| Blur (PowerLogLogSlope) | \|z\| > 3 |
| MedianIntensity | \|z\| > 5 |
| Debris (PercentMaximal) | > 1 (warning > 0.1) |
| Low cell density (Ratio P10/P90) | > 0.5 (warning > 0.4) |
| Cells / Nuclei ratio | < 0.95 |
| Cell count | < 70 per site; < 700 per well (summed sites) |

- **Failed sites for downstream steps.** The *Download failed sites (CSV)* button in the *Cohort Summary* exports one row per failed plate-well-site under the thresholds and criteria currently selected (columns "Metadata_Plate, Metadata_Well, Metadata_Field, Perturbation, failed_criteria"), so those sites can be filtered out in later steps. *Settings (JSON)* downloads the thresholds and active criteria that produced the list. The parquet file written by the pipeline has the same rows for the default thresholds, plus the site measurements and a "fail" flag.
- **Limitation.** The site montage of a well is embedded in the report only if the well is flagged with the default thresholds. A well that fails only under custom thresholds still appears in the summary, the overview and the downloaded list, but it has no montage image.

## V. Calculate features per plate.
If the data visualized in the QC step looks good (ask a biologist if unsure), then the features can be calculated based on the obtained images. Here we used the "/workspace/Pipelines/template_analysis.cppipe" pipeline. The per cell morphological profiles output from this module are stored in "/output/Profiles/Raw_profiles/".

## VI. Feature aggregation, normalization and reduction.
Once the raw morphological profiles are calculated, the next step is to produce compact and statistically representative profiles. For this step, we use the pipeline in "/workspace/Code/VI_feat_processing.py". This script is based on a PyCytoMiner workflow to 1) Aggregate the cell level profiles using the median into well level, 2) Normalize the well level profiles with the robust Z-score, where the MAD and the median used came from the DMSO wells in the plate, 3) Reduce the number of features from the morphological profiles. The output of each of those three steps are stored in "/output/Profiles/Treated_profiles/"

## VII. Clustering and visualization of profiles.
This step is in charge of running clustering (t-SNE, PCA and UMAP) on the input profiles, with all the normalized features or the reduced version, and visualize those clusters on reduced dimensions. The pipeline is in "/workspace/Code/VII_Clustering.py" and the output is stored in "/output/Clusters/".

## VIII. Evaluate the reproducibility of the morphological profiles.
Once the profiles are created, we measure how well the replicates of a compound resemble each other compared with the rest of the compounds in the batch. The script "/workspace/Code/VIII_Reproducibility.py" searches recursively the profiling output folder ("/output/Profiles/") for the whole-plate normalized profiles, and analyzes each set independently. If several files are found, they are concatenated keeping only the features they share. Other profile sets, such as the negative-control normalized ones, can be analyzed by changing the search patterns with "--norm-glob" and "--reduced-glob". This module needs per-well profiles with at least two replicates per compound, labeled in the "Metadata_Perturbation" column.

Five metrics are computed for every compound:
- **Percent Replicating:** the median pairwise Spearman correlation among the replicates of a compound is compared with the 95th percentile of a null distribution built from random sets of profiles of other compounds (5,000 permutations by default, "--null-reps"). A compound replicates if its observed value is above that threshold.
- **Mean Average Precision (mAP):** for each profile, the other replicates of the same compound should be ranked, by cosine distance, ahead of the profiles of other compounds. The average precision (AP) values are averaged per compound, and a p-value is estimated by permutation (10,000 by default, "--map-null-size") and corrected with Benjamini-Hochberg; a compound is significant if its corrected p-value is below 0.05 ("--map-threshold"). The implementation is equivalent to copairs and can use a GPU through CuPy. The pair design can be changed with "--map-pos-diffby", "--map-neg-sameby" and "--map-neg-diffby"; for example, "--map-pos-diffby Metadata_Plate --map-neg-sameby Metadata_Plate" requires the replicates to come from different plates and compares them against profiles of the same plate.
- **Cosine similarity and Spearman correlation:** mean and standard deviation between the replicates of each compound, computed on standardized features.
- **Cohen's d:** effect size separating the similarity distributions of same-compound pairs and cross-compound pairs, for both cosine and Spearman.

The results are stored in "/output/Reproducibility/combined_red/" (feature-selected profiles) and "/output/Reproducibility/combined_norm/" (normalized profiles). Each folder contains "COHORT_reproducibility_report.csv" with all the metrics per compound, "COHORT_map_per_sample.csv" with the AP of every profile and "COHORT_cohens_d.csv" with the effect size per similarity metric, where COHORT is the name given with "-c". All the random procedures use a fixed seed (42), so repeated runs give the same results. Negative controls (e.g. DMSO) are treated as one more compound, so they also appear in the report.

## IX. Subprofile analysis [Optional]
Work in progress

## X. Morphological map.
Work in progress

## XI. Random forest.
Work in progress

# 2) Technical details about the container.

## - Input data.
The "/workspace_images/" should be organized as follows:

```
Cohort_A/
├── Plate_Pxx_/
│   └── untreated_data/        # Folder with images (must be named exactly "untreated_data")
|   └── Any other metadata files...
├── Plate_Pyy_/
│   └── untreated_data/
├── Plate_Pzz_/
│   └── untreated_data/
└── ...
```

The name of the plates should contain "_Pxx_" (i.e. an uppercase "P" followed by 2 digits "xx" and surrounded by underscores "_"), so a regex can be applied to extract the XX number of the plate (e.g. 01, 09, 11, etc.).

## - Output data.
The output is organized as follows:

```
Cohort_A/
│
├── Vxx/                          # xx = version of the workflow
│   │
│   ├── CellProfiler_files/   # All files required to run CellProfiler pipelines
│   │   ├── Batch_files/      # Files required to run CellProfiler in Batch mode
│   │   ├── Cellpose_seg/     # Cellpose segmentation masks
│   │   ├── CSVs/             # Metadata required by the Load_data module (illumination pipeline)
│   │   ├── Illum_files/      # Illumination correction functions
│   │   ├── Pipelines/        # Feature extraction pipeline for the dataset
│   │   └── MP/               # Morphological profiles per mini-batch*
│   │
│   ├── QC/
│   |   ├── Images/           # QC images per mini-batch* for the five channels
│   |   ├── Reports/          # QC reports with cell count, nucleus and cell dimensions [Obsolete]
│   |   └── Collages/         # COHORT_QC_report.html, COHORT_QC_summary.pdf and COHORT_flagged_sites.parquet (sites failing the default QC thresholds)
│   │
│   ├── Profiles/
│   │   └── Treated_profiles/     # Output from PyCytoMiner (aggregation, normalization, reduction)
│   │
│   └── Clustering/               # Cluster images from morphological profiles
│
└── ...
```

* A mini-batch is defined here as the split of the images made by CellProfiler to parallelize the pipeline.

## - Code.
The container is organized as follows:

```
MAINCE_container/
│
├── CellProfiler_pipeline/            # Docker container for CellProfiler (excludes CellPose)
│   ├── Code/
│   │   ├── bash_functions.sh         # Auxiliary bash functions used in run_workflow.sh
│   │   ├── correct_illum.sh          # Runs part I of the workflow
│   │   ├── csv_generator.py          # Auxiliary Python functions used in main.py
│   │   ├── main.py                   # Produces CSV files required for CellProfiler headless pipelines
│   │   ├── run_workflow.sh           # Main script, runs processes I and III-VI
│   │   ├── qc                        # Folder with all code related to the image QC workflow
│   │   |   ├── cli.py                # Command-line entry point of the QC step
│   │   |   ├── pipeline.py           # Orchestrates loading, rendering, report and parquet generation
│   │   |   ├── profiles.py           # Dataset profile from the .env (channels, regex, wells) and shared constants
│   │   |   ├── schema.py             # Canonical vocabulary: metadata column names, aggregation levels and plate-id normalization
│   │   |   |
│   │   |   ├── assets                # Folder for the QC interface of the report
|   |   |   |   ├── report.css        # Report style configurations
|   |   |   |   ├── report.html       # Report frame
|   |   |   |   ├── report.js         # Logic for the interactive report (site-level QC engine, editable thresholds, failed-sites download)
|   |   |   |   └── utif.js           # UTIF.js library to process 16 bits tiff files
|   |   |   |
│   │   |   ├── io                    # Folder with code in charge of loading the QC morphological profiles from CellProfiler
|   |   |   |   ├── __init__.py
|   |   |   |   ├── platemap.py       # Load metadata information related to the platemap of each plate
|   |   |   |   └── readers.py        # Code in charge of loading all relevant features from CellProfiler output files
|   |   |   |
│   │   |   ├── output                # Folder with code for the QC output files
|   |   |   |   ├── html.py           # Generation of the self-contained HTML QC report
|   |   |   |   └── pdf.py            # Generation of PDF summary report
|   |   |   |
│   │   |   ├── payload               # Folder with code for the connection between Python objects and JavaScript code
|   |   |   |   └── builder.py        # Code that generates the nested dict used by report.js to build the QC graphical report (includes default thresholds and per-perturbation z-score references)
|   |   |   |
│   │   |   ├── render                # Folder with all code related to rendering images for the QC report
|   |   |   |   ├── __init__.py
|   |   |   |   ├── collage.py        # Make the plate collage to display at the QC report
|   |   |   |   ├── montage.py        # I/O layer for the tiff files and create graphical elements for the report
|   |   |   |   └── overview.py       # Make the miniatures for the QC report and compress images
|   |   |   |
│   │   |   └── stats                 # Folder with code related to all statistical measurements
|   |   |       ├── __init__.py
|   |   |       ├── aggregate.py      # Aggregation of well/site/plate morphological profiles, keep track of worst site at site level.
|   |   |       ├── colors.py         # Assignment of colors according to pass or failed thresholds (default color specification sent to the report)
|   |   |       ├── engine.py         # Threshold engine (absolute + adaptive MAD bands) for the non-robust QC features; Blur/MedianIntensity verdicts come from site_flags.py
|   |   |       ├── expressions.py    # Polars expressions to compute used statistics from dataframes with QC features
|   |   |       ├── pca.py            # Code to compute PCA to analyze QC features
|   |   |       ├── scores.py         # Well-level MAD z-scores (against same-perturbation wells) and colorscales
|   |   |       ├── site_flags.py     # Site-level classification: z-score references per perturbation, failure rules, failed-well counts and the parquet file with all sites that failed any QC criteria
|   |   |       └── thresholds.py     # Single source of truth for the default QC thresholds, criteria and minimum reference-group sizes
│   │   |
│   │   ├── VI_feat_processing.py     # Step VI — morphological profiling postprocessing
│   │   ├── VII_Clustering.py         # Step VII — clustering of morphological profiles
│   │   ├── VIII_Reproducibility.py   # Step VIII — reproducibility evaluation of profiles
│   │   ├── IX_Subprofiles.py         # Step IX — generation of subprofiles per perturbation
│   │   ├── X_Morphological_map.py    # Step X — graph analysis of morphological profiles network
│   │   └── XI_Random_forest.py       # Step XI — random forest to detect main features that produce the morphological profile signature
│   │
│   ├── Pipelines/                           # CellProfiler pipeline templates, adapted by run_workflow.sh before execution
│   │   ├── template_feat_extraction.cppipe  # Feature extraction pipeline (step V)
│   │   ├── template_illum.cppipe            # Illumination correction function pipeline
│   │   └── template_QC.cppipe               # Auxiliary step III pipeline: generates overlays and calculates QC metrics
│   │
│   ├── Dockerfile                # Container configuration file
│   ├── requirements.txt          # Python dependencies - must be adapted before building
│   └── variables.env             # Environment variables - must be adapted before running
│
├── Cellpose_seg/                 # Docker container for CellPose segmentation
│   ├── Code/
│   │   ├── II_cellpose_seg.py    # Step II — CellPose segmentation
│   │   └── run_cellpose.sh       # Main script for step II
│   │
│   ├── Dockerfile                # Container configuration file
│   ├── requirements.txt          # Python dependencies  - must be adapted before building
│   └── variables.env             # Environment variables - must be adapted before building
│
├── logs/                         # Stores all logs generated during container execution
├── .env                          # Parameters required to configure the running of the full workflow
├── .gitignore                    # Specifies files excluded from Git tracking
├── run_container.sh              # Helper script to run the container
└── compose.yml                   # Docker Compose orchestration file - must be adapted before building
```