# tartan_drive_2.0


## Download Instructions
Documentation is in progress, but the data is now publicly available!
To download, run the following command:
`
python3 scripts/tartandrive_gui.py
`
This will allow you to click through different runs and download either the rosbags, or datasets in a KITTI format. If you only want a few specific modalities, it is suggested to download the dataset form to save space. You might need to expand the size of the gui in order to see all of the options/buttons.



To download the pointcloud scan of the ATV, run `pull_atv_scan.py`. You can then use something like cloudcompare to make your own measurements.

Our data collection scripts and metadata system is also available (more details in the repo), which allows you to streamline your data collection framework into one launch command that also associates metadata with it.

Under construction
TODO:
- [x] Upload sample bag
- [x] Upload sample dataset from bag
- [x] Import metadata_utils and data collection scripts
- [ ] Import rosbag_to_dataset branch
- [x] Link to all data
