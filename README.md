# tartan_drive_2.0


## Quickstart

```bash
pip install -r requirements.txt
```

### Terminal downloader 

Interactive TUI — works locally and over SSH:

```bash
python3 scripts/tartandrive_cli.py
```

Navigate with arrow keys, filter datasets by typing, use Tab for path completion, and select KITTI modalities with checkboxes.

Non-interactive commands for scripting:

```bash
# List all available datasets
python3 scripts/tartandrive_cli.py list
python3 scripts/tartandrive_cli.py list --type kitti

# Show files and metadata for a dataset
python3 scripts/tartandrive_cli.py info <dataset_name>
python3 scripts/tartandrive_cli.py info <dataset_name> --type kitti

# Download a full rosbag run
python3 scripts/tartandrive_cli.py download <dataset_name> -o ~/my_data

# Download specific KITTI modalities
python3 scripts/tartandrive_cli.py download <dataset_name> --type kitti \
    -o ~/my_data --modalities cmd controls gps_odom image_left
```

### GUI downloader

```bash
python3 scripts/tartandrive_gui.py
```

Click through runs to download rosbags or KITTI-format datasets. You may need to resize the window to see all options.

## Download Instructions
Documentation is in progress, but the data is now publicly available!



To download the pointcloud scan of the ATV, run `pull_atv_scan.py`. You can then use something like cloudcompare to make your own measurements.

Our data collection scripts and metadata system is also available (more details in the repo), which allows you to streamline your data collection framework into one launch command that also associates metadata with it.

Under construction
TODO:
- [x] Upload sample bag
- [x] Upload sample dataset from bag
- [x] Import metadata_utils and data collection scripts
- [ ] Import rosbag_to_dataset branch
- [x] Link to all data
