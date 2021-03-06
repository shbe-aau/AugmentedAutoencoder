import os
import torch
import numpy as np
import imageio
import torch.nn as nn
import torch.nn.functional as F
import matplotlib.pyplot as plt
from skimage import img_as_ubyte
import argparse
import glob
import hashlib
import cv2 as cv

import time
import pickle
import random
from utils import *

import imgaug as ia
import imgaug.augmenters as iaa

# io utils
from pytorch3d.io import load_obj

# datastructures
from pytorch3d.structures import Meshes, Textures, list_to_padded

# 3D transformations functions
from pytorch3d.transforms import Rotate, Translate

# rendering components
from pytorch3d.renderer import (
    OpenGLPerspectiveCameras, look_at_view_transform, look_at_rotation,
    RasterizationSettings, MeshRenderer, MeshRasterizer, BlendParams,
    HardPhongShader, PointLights, DirectionalLights
)

def load_bg_images(output_path, background_path, num_bg_images, h, w, c):
    if(background_path == ""):
        return []

    bg_img_paths = glob.glob(background_path + "*.jpg")
    noof_bg_imgs = min(num_bg_images, len(bg_img_paths))
    shape = (h, w, c)
    bg_imgs = np.empty( (noof_bg_imgs,) + shape, dtype=np.uint8 )

    current_config_hash = hashlib.md5((str(shape) + str(noof_bg_imgs) + str(background_path)).encode('utf-8')).hexdigest()
    current_file_name = os.path.join(output_path + '-' + current_config_hash +'.npy')
    if os.path.exists(current_file_name):
        bg_imgs = np.load(current_file_name)
    else:
        file_list = bg_img_paths[:noof_bg_imgs]
        print(len(file_list))
        from random import shuffle
        shuffle(file_list)


        for j,fname in enumerate(file_list):
            print('loading bg img %s/%s' % (j,noof_bg_imgs))
            bgr = cv2.imread(fname)
            H,W = bgr.shape[:2]
            y_anchor = int(np.random.rand() * (H-shape[0]))
            x_anchor = int(np.random.rand() * (W-shape[1]))
            # bgr = cv2.resize(bgr, shape[:2])
            bgr = bgr[y_anchor:y_anchor+shape[0],x_anchor:x_anchor+shape[1],:]
            if bgr.shape[0]!=shape[0] or bgr.shape[1]!=shape[1]:
                continue
            if shape[2] == 1:
                bgr = cv2.cvtColor(np.uint8(bgr), cv2.COLOR_BGR2GRAY)[:,:,np.newaxis]
            bg_imgs[j] = bgr
        np.save(current_file_name,bg_imgs)
        print('loaded %s bg images' % noof_bg_imgs)
    return bg_imgs

# Augmentation
aug = iaa.Sequential([
    #iaa.Sometimes(0.5, iaa.PerspectiveTransform(0.05)),
    #iaa.Sometimes(0.5, iaa.CropAndPad(percent=(-0.05, 0.1))),
    #iaa.Sometimes(0.5, iaa.Affine(scale=(1.0, 1.2))),
    iaa.Sometimes(0.5, iaa.CoarseDropout( p=0.05, size_percent=0.01) ),
    iaa.Sometimes(0.5, iaa.GaussianBlur(1.2*np.random.rand())),
    iaa.Sometimes(0.5, iaa.Add((-0.1, 0.1), per_channel=0.3)),
    iaa.Sometimes(0.3, iaa.Invert(0.2, per_channel=True)),
    iaa.Sometimes(0.5, iaa.Multiply((0.6, 1.4), per_channel=0.5)),
    iaa.Sometimes(0.5, iaa.Multiply((0.6, 1.4))),
    iaa.Sometimes(0.5, iaa.ContrastNormalization((0.5, 2.2), per_channel=0.3))],
                     random_order=False)

# Parse parameters
parser = argparse.ArgumentParser()
parser.add_argument("obj_path", help="path to the .obj file")
parser.add_argument("-b", help="batch size, each batch will have the same pose but different augmentations", type=int, default=10)
parser.add_argument("-d", help="distance to the object", type=float, default=2000.0)
parser.add_argument("-l", help="number loops", type=int, default=10)
parser.add_argument("-v", help="visualize the data", type=bool, default=False)
parser.add_argument("-o", help="output path", default="")
parser.add_argument("-bg", help="background images path", default="")
arguments = parser.parse_args()

batch_size = arguments.b
dist = arguments.d
loops = arguments.l
obj_path = arguments.obj_path
visualize = arguments.v
output_path = arguments.o
background_path = arguments.bg

# Set the cuda device
device = torch.device("cuda:0")
torch.cuda.set_device(device)

# Load the obj and ignore the textures and materials.
verts, faces_idx, _ = load_obj(obj_path)
faces = faces_idx.verts_idx

verts_rgb = torch.ones_like(verts)
batch_verts_rgb = list_to_padded([verts_rgb for k in np.arange(batch_size)])  # B, Vmax, 3

batch_textures = Textures(verts_rgb=batch_verts_rgb.to(device))
batch_mesh = Meshes(
    verts=[verts.to(device) for k in np.arange(batch_size)],
    faces=[faces.to(device) for k in np.arange(batch_size)],
    textures=batch_textures
)

# Initialize an OpenGL perspective camera.
cameras = OpenGLPerspectiveCameras(
    fov=5.0,
    device=device)

# We will also create a phong renderer. This is simpler and only needs to render one face per pixel.
raster_settings = RasterizationSettings(
    image_size=320,
    blur_radius=0.0,
    faces_per_pixel=1,
    bin_size=0
)
# We can add a point light in front of the object.
#lights = PointLights(device=device, location=((-1.0, -1.0, -2.0),))
#"ambient_color", "diffuse_color", "specular_color"
# 'ambient':0.4,'diffuse':0.8, 'specular':0.3
lights = DirectionalLights(device=device,
                     ambient_color=[[0.25, 0.25, 0.25]],
                     diffuse_color=[[0.6, 0.6, 0.6]],
                     specular_color=[[0.15, 0.15, 0.15]],
                     direction=[[-1.0, -1.0, 1.0]])
phong_renderer = MeshRenderer(
    rasterizer=MeshRasterizer(
        cameras=cameras,
        raster_settings=raster_settings
    ),
    shader=HardPhongShader(device=device, lights=lights)
)

Rs = []
ts = []
elevs = []
azims = []
images = []
org_images = []
lights = []

h = 320
w = 320
c = 3
num_bg_images = loops*batch_size
bg_imgs = load_bg_images(output_path, background_path, num_bg_images, h, w, c)
#plt.imshow(bg_imgs[0])

from scipy.spatial.transform import Rotation as scipyR
start = time.time()
for i in np.arange(loops):

    curr_Rs = []
    curr_ts = []
    cam_pose = None

    # Generate random pose for the batch
    # All images in the batch will share pose but different augmentations
    R, t = look_at_view_transform(dist, elev=0, azim=0)

    # Sample azimuth and apply transformation
    azim = np.random.uniform(low=0.0, high=360.0, size=1)
    azim = 45.0
    rot = scipyR.from_euler('z', azim, degrees=True)
    rot_mat = torch.tensor(rot.as_matrix(), dtype=torch.float32)
    R = torch.matmul(R, rot_mat)

    # Sample elevation and apply transformation
    elev = np.random.uniform(low=-180.0, high=0.0, size=1)
    elev = 25.0
    rot = scipyR.from_euler('x', elev, degrees=True)
    rot_mat = torch.tensor(rot.as_matrix(), dtype=torch.float32)
    R = torch.matmul(R, rot_mat)

    for k in np.arange(batch_size):
        elevs.append(elev)
        azims.append(azim)

        curr_Rs.append(R.squeeze())
        curr_ts.append(t.squeeze())

    batch_R = torch.tensor(np.stack(curr_Rs), device=device, dtype=torch.float32)
    batch_T = torch.tensor(np.stack(curr_ts), device=device, dtype=torch.float32)

    random_lights = []
    for l in np.arange(batch_size):
        random_light = [random.uniform(-1.0,1.0) for i in np.arange(3)]
        random_lights.append(random_light)
    batch_light = torch.tensor(np.stack(random_lights), device=device, dtype=torch.float32)
    phong_renderer.shader.lights.direction = batch_light
    lights.append(random_lights)

    image_renders = phong_renderer(meshes_world=batch_mesh, R=batch_R, T=batch_T)

    # Augment data
    image_renders = image_renders.cpu().numpy()
    images_aug = aug(images=image_renders)

    if(len(bg_imgs) > 0):
        bg_im_isd = np.random.choice(len(bg_imgs), batch_size, replace=False)
    for k in np.arange(batch_size):
        image_base = image_renders[k]
        image_ref = images_aug[k]

        if(len(bg_imgs) > 0):
            img_back = bg_imgs[bg_im_isd[k]]
            img_back = cv.cvtColor(img_back, cv.COLOR_BGR2RGBA).astype(float)
            alpha = image_base[:, :, 0:3].astype(float)
            sum_img = np.sum(image_base[:,:,:3], axis=2)
            alpha[sum_img > 0] = 1
            image_ref[:, :, 0:3] = image_ref[:, :, 0:3] * alpha + img_back[:, :, 0:3]/255 * (1 - alpha)
        else:
            image_ref = image_ref[:, :, 0:3]

        image_ref = np.clip(image_ref, 0.0, 1.0) #[:, :, 0:3]

        Rs.append(curr_Rs[k].cpu().numpy().squeeze())
        ts.append(curr_ts[k].cpu().numpy().squeeze())

        org_img = image_renders[k]
        ys, xs = np.nonzero(org_img[:,:,0] > 0)
        obj_bb = calc_2d_bbox(xs,ys,[640,640])
        cropped = extract_square_patch(image_ref, obj_bb)
        cropped_org = extract_square_patch(org_img, obj_bb)
        images.append(cropped[:,:,:3])
        org_images.append(cropped_org)
        print("Loop: {0} Batch: {1}".format(i,k))

        if(visualize):
            plt.figure(figsize=(6, 2))
            plt.subplot(1, 3, 1)
            plt.imshow(org_img)
            plt.title("Rendered image")

            plt.subplot(1, 3, 2)
            plt.imshow(cropped_org)
            plt.title("Cropped image")

            plt.subplot(1, 3, 3)
            plt.imshow(cropped)
            plt.title("Augmented image")
            plt.show()
print("Elapsed: {0}".format(time.time()-start))

data={"images":images,
      "Rs":Rs,
      "ts":ts,
      "elevs":elevs,
      "azims":azims,
      "dist":dist,
      "light_dir":lights}

if(output_path == ""):
    output_path = "./training-images.p"
pickle.dump(data, open(output_path, "wb"), protocol=2)
