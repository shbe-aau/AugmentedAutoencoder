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
from utils.utils import *
from utils.tools import *
from Encoder import Encoder

import imgaug as ia
import imgaug.augmenters as iaa

from scipy.stats import special_ortho_group
from scipy.spatial.transform import Rotation as scipyR
from plot_loss_landscape import eqv_dist_points

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

from utils.pytless import inout, misc
from vispy import app, gloo
from utils.pytless.renderer import Renderer


class DatasetGenerator():

    def __init__(self, background_path, obj_path, obj_distance, batch_size,
                 encoder_weights, device, sampling_method="sphere", random_light=True,
                 num_bgs=17000):
        self.device = device
        self.poses = []
        self.obj_path = obj_path
        self.batch_size = batch_size
        self.dist = obj_distance
        self.img_size = 320
        self.max_rel_offset = 0.2
        self.K = np.array([1075.65, 0, self.img_size/2,
                           0, 1073.90, self.img_size/2,
                           0, 0, 1]).reshape(3,3)
        self.aug = self.setup_augmentation()
        self.model = inout.load_ply(obj_path.replace(".obj",".ply"))
        self.backgrounds = self.load_bg_images("backgrounds", background_path, num_bgs,
                                               self.img_size, self.img_size)
        if(encoder_weights is not None):
            self.encoder = self.load_encoder(encoder_weights)
        else:
            self.encoder = None

        self.renderer = Renderer(self.model, (self.img_size,self.img_size),
                                 self.K, surf_color=(1, 1, 1), mode='rgb',
                                 random_light=random_light)

        self.pose_reuse = False
        if(sampling_method.split("-")[-1] == "reuse"):
            self.pose_reuse = True
        sampling_method = sampling_method.replace("-reuse","")

        self.simple_pose_sampling = False
        if(sampling_method == "tless"):
            self.pose_sampling = self.tless_sampling
            self.simple_pose_sampling = False
        elif(sampling_method == "quat"):
            self.pose_sampling = self.quat_sampling
            self.simple_pose_sampling = False
        elif(sampling_method == "sphere"):
            self.pose_sampling = self.sphere_sampling
            self.simple_pose_sampling = False
        elif(sampling_method == "tless-simple"):
            self.pose_sampling = self.tless_sampling
            self.simple_pose_sampling = True
        elif(sampling_method == "sphere-simple"):
            self.pose_sampling = self.sphere_sampling
            self.simple_pose_sampling = True
        elif(sampling_method == "haar"):
            self.pose_sampling = self.haar_sampling
            self.simple_pose_sampling = False
        elif(sampling_method == "fixed"): # Mainly for debugging purposes
            self.pose_sampling = self.fixed_sampling
            self.simple_pose_sampling = False
        elif(sampling_method == "sundermeyer-random"):
            self.pose_sampling = self.sm_quat_random
            self.simple_pose_sampling = False
        elif(".p" in sampling_method):
            self.pose_sampling = self.pickle_sampling
            self.pose_path = sampling_method
            self.poses = []
            self.simple_pose_sampling = False
        else:
            print("ERROR! Invalid view sampling method: {0}".format(sampling_method))

        if(self.pose_reuse == True):
            self.poses = []
            for i in np.arange(20*1000):
                R, t = self.pose_sampling()
                self.poses.append(R)
            self.pose_sampling = self.reuse_poses

    def reuse_poses(self):
        random.shuffle(self.poses)
        R = self.poses[-1]
        t = torch.tensor([0.0, 0.0, self.dist])
        return R,t

    def load_encoder(self, weights_path):
        model = Encoder(weights_path).to(self.device)
        model.eval()
        return model

    def load_bg_images(self, output_path, background_path, num_bg_images, h, w, c=3):
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

    def setup_augmentation(self):
        # Augmentation
        # aug = iaa.Sequential([
        #     #iaa.Sometimes(0.5, iaa.PerspectiveTransform(0.05)),
        #     #iaa.Sometimes(0.5, iaa.CropAndPad(percent=(-0.05, 0.1))),
        #     #iaa.Sometimes(0.5, iaa.Affine(scale=(1.0, 1.2))),
        #     iaa.Sometimes(0.5, iaa.CoarseDropout( p=0.05, size_percent=0.01) ),
        #     iaa.Sometimes(0.5, iaa.GaussianBlur(1.2*np.random.rand())),
        #     iaa.Sometimes(0.5, iaa.Add((-0.1, 0.1), per_channel=0.3)),
        #     iaa.Sometimes(0.3, iaa.Invert(0.2, per_channel=True)),
        #     iaa.Sometimes(0.5, iaa.Multiply((0.6, 1.4), per_channel=0.5)),
        #     iaa.Sometimes(0.5, iaa.Multiply((0.6, 1.4))),
        #     iaa.Sometimes(0.5, iaa.ContrastNormalization((0.5, 2.2), per_channel=0.3))],
        #                      random_order=False)
        aug = iaa.Sequential([
            iaa.Sometimes(0.5, iaa.CoarseDropout( p=0.25, size_percent=0.02) ),
            iaa.Sometimes(0.5, iaa.GaussianBlur(1.2*np.random.rand())),
            iaa.Sometimes(0.5, iaa.Add((-60, 60), per_channel=0.3)),
            iaa.Sometimes(0.5, iaa.Multiply((0.6, 1.4), per_channel=0.5)),
            iaa.Sometimes(0.5, iaa.Multiply((0.6, 1.4))),
            iaa.Sometimes(0.5, iaa.ContrastNormalization((0.5, 2.2), per_channel=0.3))],
                             random_order=False)
        return aug

    # Randomly sample poses from .p file (pickle)
    def pickle_sampling(self):
        # Load poses from .p file once
        if(len(self.poses) == 0):
            with open(self.pose_path, "rb") as f:
                self.poses = pickle.load(f, encoding="latin1")["Rs"]

        # Sample pose randomly
        random.shuffle(self.poses)
        R = torch.tensor(self.poses[-1], dtype=torch.float32)
        t = torch.tensor([0.0, 0.0, self.dist])
        return R,t
    
    # From: https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.special_ortho_group.html
    def haar_sampling(self):
        R = torch.tensor(special_ortho_group.rvs(3), dtype=torch.float32)
        t = torch.tensor([0.0, 0.0, self.dist])
        return R,t

    # Fixed pose - mainly for debugging purposes
    def fixed_sampling(self):
        # Generate random pose for the batch
        # All images in the batch will share pose but different augmentations
        R, t = look_at_view_transform(self.dist, elev=0, azim=0, up=((0, 1, 0),))

        # Sample azimuth and apply transformation
        azim = 35.0
        rot = scipyR.from_euler('z', azim, degrees=True)
        rot_mat = torch.tensor(rot.as_matrix(), dtype=torch.float32)
        R = torch.matmul(R, rot_mat)

        # Sample elevation and apply transformation
        elev = 25.
        rot = scipyR.from_euler('x', elev, degrees=True)
        rot_mat = torch.tensor(rot.as_matrix(), dtype=torch.float32)
        R = torch.matmul(R, rot_mat)

        t = torch.tensor([0.0, 0.0, self.dist])
        return R,t

    # Sampling based on the T-LESS dataset
    def tless_sampling_broken(self):
        # Generate random pose for the batch
        # All images in the batch will share pose but different augmentations
        R, t = look_at_view_transform(self.dist, elev=0, azim=0, up=((0, 1, 0),))

        # Sample azimuth and apply transformation
        azim = np.random.uniform(low=0.0, high=360.0, size=1)
        rot = scipyR.from_euler('z', azim, degrees=True)
        rot_mat = torch.tensor(rot.as_matrix(), dtype=torch.float32)
        R = torch.matmul(R, rot_mat)

        # Sample elevation and apply transformation
        elev = np.random.uniform(low=-360, high=0.0, size=1)
        rot = scipyR.from_euler('x', elev, degrees=True)
        rot_mat = torch.tensor(rot.as_matrix(), dtype=torch.float32)
        R = torch.matmul(R, rot_mat)

        # Sample cam plane rotation and apply
        if(not self.simple_pose_sampling):
            in_plane = np.random.uniform(low=0.0, high=360.0, size=1)
            rot = scipyR.from_euler('y', in_plane, degrees=True)
            rot_mat = torch.tensor(rot.as_matrix(), dtype=torch.float32)
            R = torch.inverse(R)
            R = torch.matmul(R, rot_mat)
            R = torch.inverse(R)

        t = torch.tensor([0.0, 0.0, self.dist])
        return R.squeeze(),t

    def tless_sampling(self):
        theta_sample = np.random.uniform(low=0.0, high=2.0*np.pi, size=1)[0]
        phi_sample = np.random.uniform(low=0.0, high=2.0*np.pi, size=1)[0]

        x = self.dist*np.sin(theta_sample)*np.cos(phi_sample)
        y = self.dist*np.sin(theta_sample)*np.sin(phi_sample)
        z = self.dist*np.cos(theta_sample)

        cam_position = torch.tensor([float(x), float(y), float(z)]).unsqueeze(0)
        if(z < 0):
            R = look_at_rotation(cam_position, up=((0, 0, -1),)).squeeze()
        else:
            R = look_at_rotation(cam_position, up=((0, 0, 1),)).squeeze()

        # Rotate in-plane
        if(not self.simple_pose_sampling):
            rot_degrees = np.random.uniform(low=-90.0, high=90.0, size=1)
            rot = scipyR.from_euler('z', rot_degrees, degrees=True)
            rot_mat = torch.tensor(rot.as_matrix(), dtype=torch.float32)
            R = torch.matmul(R, rot_mat)
            R = R.squeeze()

        t = torch.tensor([0.0, 0.0, self.dist])
        return R,t


    # Based on Sundermeyer
    def sm_quat_random(self):
        # Sample random quaternion
        rand = np.random.rand(3)
        r1 = np.sqrt(1.0 - rand[0])
        r2 = np.sqrt(rand[0])
        pi2 = math.pi * 2.0
        t1 = pi2 * rand[1]
        t2 = pi2 * rand[2]
        random_quat = np.array([np.cos(t2)*r2, np.sin(t1)*r1,
                                np.cos(t1)*r1, np.sin(t2)*r2])

        # Convert quaternion to rotation matrix
        q = np.array(random_quat, dtype=np.float64, copy=True)
        n = np.dot(q, q)
        if n < 0.0001: #_EPS:
            return np.identity(4)
        q *= math.sqrt(2.0 / n)
        q = np.outer(q, q)
        R = np.array([
            [1.0-q[2, 2]-q[3, 3],     q[1, 2]-q[3, 0],     q[1, 3]+q[2, 0], 0.0],
            [    q[1, 2]+q[3, 0], 1.0-q[1, 1]-q[3, 3],     q[2, 3]-q[1, 0], 0.0],
            [    q[1, 3]-q[2, 0],     q[2, 3]+q[1, 0], 1.0-q[1, 1]-q[2, 2], 0.0],
            [                0.0,                 0.0,                 0.0, 1.0]])

        R = torch.from_numpy(R[:3,:3])
        t = torch.tensor([0.0, 0.0, self.dist])
        return R,t
    
    def quat_sampling(self):
        R = get_sampled_rotation_matrices_by_quat(1).squeeze()
        t = torch.tensor([0.0, 0.0, self.dist])
        return R,t
    
    # Truely random
    # Based on: https://www.cmu.edu/biolphys/deserno/pdf/sphere_equi.pdf
    def sphere_sampling(self):
        #z = np.random.uniform(low=-self.dist, high=self.dist, size=1)[0]
        z = np.random.uniform(low=-self.dist, high=self.dist, size=1)[0]
        theta_sample = np.random.uniform(low=0.0, high=2.0*np.pi, size=1)[0]
        x = np.sqrt((self.dist**2 - z**2))*np.cos(theta_sample)
        y = np.sqrt((self.dist**2 - z**2))*np.sin(theta_sample)

        cam_position = torch.tensor([x, y, z]).unsqueeze(0)
        if(z < 0):
            R = look_at_rotation(cam_position, up=((0, 0, -1),)).squeeze()
        else:
            R = look_at_rotation(cam_position, up=((0, 0, 1),)).squeeze()

        # Rotate in-plane
        if(not self.simple_pose_sampling):
            rot_degrees = np.random.uniform(low=-90.0, high=90.0, size=1)
            rot = scipyR.from_euler('z', rot_degrees, degrees=True)
            rot_mat = torch.tensor(rot.as_matrix(), dtype=torch.float32)
            R = torch.matmul(R, rot_mat)
            R = R.squeeze()

        t = torch.tensor([0.0, 0.0, self.dist])
        return R,t

    def generate_image_batch(self):
        # Generate random poses
        curr_Rs = []
        curr_ts = []

        image_renders = []
        for k in np.arange(self.batch_size):
            R, t = self.pose_sampling()
            
            R = R.detach().cpu().numpy()
            t = t.detach().cpu().numpy()

            # Convert R matrix from pytorch to opengl format
            # for rendering only!
            xy_flip = np.eye(3, dtype=np.float)
            xy_flip[0,0] = -1.0
            xy_flip[1,1] = -1.0
            R_opengl = np.dot(R,xy_flip)
            R_opengl = np.transpose(R_opengl)

            # Render images
            #ren_rgb = renderer.render(self.model, (self.img_size,self.img_size),
            #                          self.K, R_opengl, t, surf_color=(1, 1, 1), mode='rgb')

            ren_rgb = self.renderer.render(R_opengl, t)

            curr_Rs.append(R)
            curr_ts.append(t)

            image_renders.append(ren_rgb)       

        if(len(self.backgrounds) > 0):
            bg_im_isd = np.random.choice(len(self.backgrounds), self.batch_size, replace=False)

        images = []
        for k in np.arange(self.batch_size):
            image_base = image_renders[k].copy()
                
            if(len(self.backgrounds) > 0):
                img_back = self.backgrounds[bg_im_isd[k]]
                img_back = cv.cvtColor(img_back, cv.COLOR_BGR2RGBA).astype(float)
                alpha = image_base[:, :, 0:3].astype(float)
                sum_img = np.sum(image_base[:,:,:3], axis=2)
                alpha[sum_img > 0] = 1

                image_base[:, :, 0:3] = image_base[:, :, 0:3] * alpha + img_back[:, :, 0:3] * (1 - alpha)
            else:
                image_base = image_base[:, :, 0:3]

            # Augment data
            image_aug = np.array([image_base])
            image_aug = self.aug(images=image_aug)            

            # Convert to float and clip
            image_aug = image_aug[0].astype(np.float)/np.max(image_aug[0])
            image_ref = np.clip(image_aug, 0.0, 1.0)

            org_img = image_renders[k]
            ys, xs = np.nonzero(org_img[:,:,0] > 0)
            obj_bb = calc_2d_bbox(xs,ys,[self.img_size,self.img_size])

            # Add relative offset when cropping - like Sundermeyer
            x, y, w, h = obj_bb
            
            rand_trans_w = np.random.uniform(-self.max_rel_offset, 0) * w
            rand_trans_h = np.random.uniform(-self.max_rel_offset, 0) * h
            
            rand_trans_x = np.random.uniform(-self.max_rel_offset, self.max_rel_offset) * w
            rand_trans_y = np.random.uniform(-self.max_rel_offset, self.max_rel_offset) * h
            obj_bb_off = obj_bb + np.array([rand_trans_x,rand_trans_y,
                                            rand_trans_w,rand_trans_h])

            cropped = extract_square_patch(image_ref, obj_bb_off)
            cropped_org = extract_square_patch(org_img, obj_bb)
            images.append(cropped[:,:,:3])

        data = {"images":images,
                "Rs":curr_Rs}
        return data

    def generate_images(self, num_samples):
        data = {"images":[],
                "Rs":[]}
        while(len(data["images"]) < num_samples):
            print("Generating images: {0}/{1}".format(len(data["images"]), num_samples))
            curr_data = self.generate_image_batch()
            data["images"] = data["images"] + curr_data["images"]
            data["Rs"] = data["Rs"] + curr_data["Rs"]
        data["images"] = data["images"][:num_samples]
        data["Rs"] = data["Rs"][:num_samples]
        return data

    def generate_samples(self, num_samples):
        data = self.generate_images(num_samples)

        if(self.encoder is not None):
            codes = []
            for img in data["images"]:
                img = torch.from_numpy(img).unsqueeze(0).permute(0,3,1,2).to(self.device)
                code = self.encoder(img.float())
                code = code.detach().cpu().numpy()[0]
                norm_code = code / np.linalg.norm(code)
                codes.append(norm_code)
            data["codes"] = codes
        return data

def str2bool(v):
    if isinstance(v, bool):
       return v
    if v.lower() in ('yes', 'true', 't', 'y', '1'):
        return True
    elif v.lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    else:
        raise argparse.ArgumentTypeError('Boolean value expected.')

if __name__ == "__main__":
    # Parse parameters
    parser = argparse.ArgumentParser()
    parser.add_argument("obj_path", help="path to the .obj file")
    parser.add_argument("-b", help="batch size, each batch will have the same pose but different augmentations", type=int, default=8)
    parser.add_argument("-d", help="distance to the object", type=float, default=2000.0)
    parser.add_argument("-n", help="number of total samples", type=int, default=1000)
    parser.add_argument("-v", help="visualize the data", default=False)
    parser.add_argument("-o", help="output path", default="")
    parser.add_argument("-bg", help="background images path", default="")
    parser.add_argument("-s", help="pose sampling method", default="tless-simple")
    parser.add_argument("-e", help="path to .npy encoder weights", default=None)
    parser.add_argument("-rl", help="enable random light", default=True)
    parser.add_argument("-ng", help="number of backgrounds", type=int, default=17000)
    arguments = parser.parse_args()

    # Create dataset generator
    device = torch.device("cuda:0")
    torch.cuda.set_device(device)
    dg = DatasetGenerator(background_path=arguments.bg,
                          obj_path=arguments.obj_path,
                          obj_distance=arguments.d,
                          batch_size=arguments.b,
                          sampling_method=arguments.s,
                          encoder_weights=arguments.e,
                          random_light=str2bool(arguments.rl),
                          num_bgs=arguments.ng,
                          device=device)

    # Generate data
    data = dg.generate_samples(num_samples=arguments.n)

    # Visualize it (optional)
    if(str2bool(arguments.v)):
        for i,img in enumerate(data["images"]):
            window_name = "Sample {0}/{1}".format(i,arguments.n)
            cv2.namedWindow(window_name)
            cv2.moveWindow(window_name,42,42)
            # Flip last axis to convert from RGB to BGR before showing using cv2
            cv2.imshow(window_name, np.flip(img,axis=2))
            key = cv2.waitKey(0)
            cv2.destroyWindow(window_name)
            if(key == ord("q")):
                break

    # Save generated data
    output_path = arguments.o
    if(output_path == ""):
        output_path = "./training-images.p"
    pickle.dump(data, open(output_path, "wb"), protocol=2)
    print("Saved dataset to: ", output_path)
