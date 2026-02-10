import tkinter as tk
from tkinter import messagebox, filedialog
import yaml
import os
import numpy as np
import matplotlib.pyplot as plt
from PIL import Image, ImageTk
import rasterio
from rasterio.enums import Resampling
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import (FigureCanvasTkAgg, NavigationToolbar2Tk)
from tkinter.scrolledtext import ScrolledText
from tqdm import tqdm
from rasterio.transform import rowcol

import boto3
from botocore import UNSIGNED
from botocore.client import Config
from os.path import isfile, join

plt.rcParams['image.interpolation'] = 'None'

_dat = rasterio.open(r"./assets/gascola.tif")
_scale = 0.5
_map = _dat.read(out_shape=(_dat.count,
    int(_dat.height * _scale),
    int(_dat.width * _scale)
), resampling=Resampling.bilinear)[:3]/255.0


with open("./assets/files.yaml") as stream:
    FILE_MAP = yaml.safe_load(stream)

KITTI_FILE_MAP = FILE_MAP['kitti']

class AirLabDownloader(object):
    def __init__(self, bucket_name = 'tartandrive2') -> None:

        endpoint_url = "https://airlab-cloud.andrew.cmu.edu:8080/swift/v1/AUTH_ac8533a83cff4d48bc8c608ad222d330"

        self.client = boto3.client("s3", endpoint_url=endpoint_url, config=Config(signature_version=UNSIGNED))
        self.bucket_name = bucket_name

    def download(self, filename, output_name):
        success_source_files, success_target_files = [], []

        target_file_name = output_name
        source_file_name = filename
        import os
        from pathlib import Path
        target_dir = os.path.dirname(target_file_name)
        print(target_dir)
        # if not os.path.exists(target_dir):
            # make recursive directory
            # os.makedirs(target_dir)
        # if isfile(target_file_name):
        #     print_error('Error: Target file {} already exists..'.format(target_file_name))
        #     continue
        #     # return False, success_source_files, success_target_files

        print(f"  Downloading {source_file_name} from {self.bucket_name}...")
        try:
            resp = self.client.get_object(Bucket=self.bucket_name, Key=source_file_name)

            path = Path(target_file_name)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(target_file_name, "wb") as f:
                for chunk in resp["Body"].iter_chunks(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)

        except Exception as e:
            print(f"Error: Failed to download {source_file_name} due to {e}.")
            return False, success_source_files, success_target_files

        print(f"  Successfully downloaded {source_file_name} to {target_file_name}!")

        return True, success_source_files, success_target_files


# Function to get the metadata from the YAML file
def get_metadata(bucket_name, directory):
    try:
        downloader.download(f"bags/{directory}/info.yaml", 'temp_metadata.yaml')
        with open('temp_metadata.yaml', 'r') as file:
            metadata = yaml.safe_load(file)

        return metadata.get('duration'), metadata.get('top_speed')
    except:
        messagebox.showerror("Error", f"Could not retrieve metadata")
        return None, None

# Function to list directories in the bucket
def list_directories(bucket_name):
    directories = []
    try:
        for obj in KITTI_FILE_MAP:
            if obj != 'files':
                directories.append(obj.split('/')[-2])
    except S3Error as e:
        messagebox.showerror("Error", f"Could not list directories: {e}")
    return directories

def list_items(bucket_name, prefix):
    items = []
    try:
        for obj in KITTI_FILE_MAP[prefix]:
            if obj == 'files':
                for sub_obj in KITTI_FILE_MAP[prefix]['files']:
                    items.append(sub_obj)
            else:
                items.append(obj)
    except S3Error as e:
        messagebox.showerror("Error", f"Could not list directories: {e}")
    return items

# Function to download selected directory
def download_directory(bucket_name, directory, save_path, type):

    if '.' in directory.split('/')[-1]:  #probably a file
        # print("Saving " + directory + " to " + save_path)
        downloader.download(directory, save_path)
    else: # directory, for now assuming only one level of depth
        if type == 'bags':
            i_bags = tqdm(FILE_MAP[type][directory + '/']['files'])
            for file in i_bags:
                i_bags.set_description("Downloading " + file)
                f_save_path = save_path + '/' + file.split('/')[-1]
                downloader.download(file, f_save_path)
                # print("Saving " + file + " to " + f_save_path)
        else:
            for file in FILE_MAP[type]['/'.join(directory.split('/')[:-2]) + '/'][directory]['files']:
                f_save_path = save_path + file.split('/')[-1]
                downloader.download(file, f_save_path)
                # print("Saving " + file + " to " + f_save_path)

    # print(FILE_MAP[save_path])


def add_option(option_name):
    if option_name not in kitti_vars:
        var = tk.IntVar()
        kitti_vars[option_name] = var
        disp_name = option_name.split('/')#[-1].split('.')[0]
        # disp_name = option_name
        if len(disp_name[-1]) == 0:
            disp_name = disp_name[-2]
        else:
            disp_name = disp_name[-1].split('.')[0]
        chk = tk.Checkbutton(modalities_frame, text=disp_name, variable=var)
        chk.pack(anchor=tk.W)
        modalities_frame.window_create('end', window=chk)
        modalities_frame.insert('end','\n')
        option_widgets.append(chk)

def remove_options():
    # for widget in option_widgets:
    #     widget.pack_forget()
    #     widget.destroy()
    #     option_widgets.remove(widget)
        # break
    global modalities_frame
    modalities_frame.pack_forget()
    modalities_frame.destroy()
    modalities_frame = ScrolledText(root)
    modalities_frame.pack()
    kitti_options_frame.pack(fill=tk.BOTH, expand=1)
    kitti_vars.clear()

# Function to handle directory selection and hover
def on_select(event):
    w = event.widget
    if len(w.curselection()) == 0:
        #probably clicking something else, not sure how this hook works
        return
    index = int(w.curselection()[0])
    directory = w.get(index)
    duration, top_speed = get_metadata(bucket_name, directory)
    hover_text.set(f"Duration: {duration}, Top Speed: {top_speed}")

    on_radio_select()

    display_image_and_plot(directory)

def repopulate_checkboxes(directory):
    remove_options()
    items = list_items(bucket_name, 'kitti/all_topics/' + directory + '/')
    for item in items:
        add_option(item)

# Function to display image and plot points
def display_image_and_plot(directory):
    try:
        plt.clf()
        points_path = os.path.join("temp_pts.npy")        
        downloader.download(f"bags/{directory}/gps.npy", points_path)
        t_odom = np.load(points_path)

        try:
            row,col = _dat.index(-t_odom[:,1], t_odom[:,0])
        except:
            row, col = rowcol(_dat.transform, -t_odom[:,1], t_odom[:,0])
        row = np.array(row).astype(float)
        col = np.array(col).astype(float)
        row *= _scale
        col *= _scale
        bad_thresh = -726698.0 * _scale
        if np.any(np.array(col) < bad_thresh + 50):
            row = row[col >= bad_thresh + 50]
            col = col[col >= bad_thresh + 50]

        fig = Figure()
        p1 = fig.add_subplot(111)
        plt.figure(figsize=(4, 4))

        p1.imshow(np.transpose(_map,(1,2,0)))

        p1.plot(col, row,'-r')
        p1.axis('off')
        fig.tight_layout()

        if hasattr(display_image_and_plot, 'canvas'):
            display_image_and_plot.canvas.get_tk_widget().pack_forget()
            display_image_and_plot.canvas.get_tk_widget().destroy()
        display_image_and_plot.canvas = FigureCanvasTkAgg(fig, master=plot_frame)
        display_image_and_plot.canvas.draw()
        display_image_and_plot.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        # Add toolbar
        if hasattr(display_image_and_plot, 'toolbar'):
            display_image_and_plot.toolbar.pack_forget()
            display_image_and_plot.toolbar.destroy()
        display_image_and_plot.toolbar = NavigationToolbar2Tk(display_image_and_plot.canvas, plot_frame)
        display_image_and_plot.toolbar.update()
        display_image_and_plot.toolbar.pack(fill=tk.BOTH, expand=True)

    except S3Error as e:
        messagebox.showerror("Error", f"Could not retrieve image or points: {e}")

# Function to handle download button click
def download_selected_directory():
    selected_dir = directory_listbox.get(directory_listbox.curselection())
    save_path = filedialog.askdirectory()
    if save_path:
        if dataset_type.get() == 'kitti':
            selected_options = [option for option, var in kitti_vars.items() if var.get() == 1]
            i_selected_options = tqdm(selected_options)
            for option in i_selected_options:
                i_selected_options.set_description("Downloading modality: " + option)
                # option_path = os.path.join(selected_dir, option)
                option_path = option
                splits = option.split('/')
                if len(splits[-1]) == 0:
                    suffix = '/'.join(splits[-3:])
                else:
                    suffix = '/'.join(splits[-2:])
                save_path_final = os.path.join(save_path, suffix)
                download_directory(bucket_name, option_path, save_path_final, 'kitti')
        else:
            download_directory(bucket_name, 'bags/'+selected_dir, os.path.join(save_path, selected_dir),'bags')


def on_radio_select():
    if dataset_type.get() == 'kitti':
        # print(directory_listbox.get(directory_listbox.curselection()),'+')
        try:
            cur_dir = directory_listbox.get(directory_listbox.curselection())
            repopulate_checkboxes(cur_dir)
        except:
            messagebox.showerror("Error", "Please make sure a directory is selected")

    else:
        kitti_options_frame.pack_forget()
        global modalities_frame
        modalities_frame.pack_forget()
        modalities_frame.destroy()

# Function to select/deselect all checkboxes
def select_all():
    for var in kitti_vars.values():
        var.set(1)

# Function to deselect all checkboxes
def deselect_all():
    for var in kitti_vars.values():
        var.set(0)

# Bucket name
bucket_name = 'tartandrive2'
downloader = AirLabDownloader(bucket_name=bucket_name)

# Create main window
root = tk.Tk()
root.title("TartanDrive Directory Downloader")

# Listbox to display directories
directory_listbox = tk.Listbox(root, height = 10, width = 40)
directory_listbox.pack(side=tk.LEFT, fill=tk.BOTH)
directory_listbox.bind('<<ListboxSelect>>', on_select)
# scrollbar = tk.Scrollbar(directory_listbox, orient="vertical", command=directory_listbox.yview)
# scrollbar.pack(side="right", fill="both")
# directory_listbox.configure(yscrollcommand=scrollbar.set)

# Label to display hover text
hover_text = tk.StringVar()
hover_label = tk.Label(root, textvariable=hover_text)
hover_label.config(font=("Helvetica", 20, "bold"))
hover_label.pack()

image_label = tk.Label(root)
image_label.pack()
# plot_label = tk.Label(root)
# plot_label.pack()
plot_frame = tk.Frame(root)
plot_frame.pack(fill=tk.BOTH, expand=1)

# Radio buttons for selecting dataset type
dataset_type = tk.StringVar(value='rosbags')
radio_frame = tk.Frame(root)
tk.Radiobutton(root, text='Rosbags', variable=dataset_type, value='rosbags', command=on_radio_select).pack()
tk.Radiobutton(root, text='Kitti Dataset', variable=dataset_type, value='kitti', command=on_radio_select).pack()

# Frame for kitti options
kitti_options_frame = tk.Frame(root)
modalities_frame = ScrolledText(root)
# modalities_frame.pack()


# options_canvas = tk.Canvas(kitti_options_frame, height=200)  # Set a fixed height for the canvas
# scrollbar = ttk.Scrollbar(kitti_options_frame, orient="vertical", command=options_canvas.yview)
# options_inner_frame = tk.Frame(options_canvas)
#
# options_inner_frame.bind(
#     "<Configure>",
#     lambda e: options_canvas.configure(
#         scrollregion=options_canvas.bbox("all")
#     )
# )
#
# options_canvas.create_window((0, 0), window=options_inner_frame, anchor="nw")
# options_canvas.configure(yscrollcommand=scrollbar.set)
#
# options_canvas.pack(side="left", fill="both", expand=True)
# scrollbar.pack(side="right", fill="y")
# kitti_vars = {
#     'Option1': tk.IntVar(),
#     'Option2': tk.IntVar(),
#     'Option3': tk.IntVar(),
#     'Option4': tk.IntVar()
# }
# for option, var in kitti_vars.items():
#     tk.Checkbutton(kitti_options_frame, text=option, variable=var).pack(anchor=tk.W)
option_widgets = []
kitti_vars = {}

# Buttons to select/deselect all options
tk.Button(kitti_options_frame, text='Select All', command=select_all).pack(anchor=tk.W)
tk.Button(kitti_options_frame, text='Deselect All', command=deselect_all).pack(anchor=tk.W)


# Button to download selected directory
download_button = tk.Button(root, text="Download", command=download_selected_directory)
download_button.pack()

# Populate the listbox with directories
directories = list_directories(bucket_name)
# print(directories)
for directory in directories:
    directory_listbox.insert(tk.END, directory)

# Run the application
root.mainloop()
