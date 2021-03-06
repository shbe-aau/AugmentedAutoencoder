# Copyright 2018 The TensorFlow Authors. All Rights Reserved.
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
# ============================================================================

## Original source:
## https://github.com/tensorflow/tensorflow/blob/v1.13.1/tensorflow/tools/dockerfiles/dockerfiles/gpu.Dockerfile
## Modified by shbe@create.aau.dk

ARG UBUNTU_VERSION=18.04

FROM nvidia/cudagl:10.0-devel-ubuntu${UBUNTU_VERSION} as base

# Pick up some TF dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        cuda-command-line-tools-10-0 \
        cuda-cublas-10-0 \
        cuda-cufft-10-0 \
        cuda-curand-10-0 \
        cuda-cusolver-10-0 \
        cuda-cusparse-10-0 \
        libcudnn7=7.4.1.5-1+cuda10.0 \
        libfreetype6-dev \
        libhdf5-serial-dev \
        libzmq3-dev \
        pkg-config \
        software-properties-common \
        unzip

RUN apt-get update && \
        apt-get install nvinfer-runtime-trt-repo-ubuntu1804-5.0.2-ga-cuda10.0 \
        && apt-get update \
        && apt-get install -y --no-install-recommends libnvinfer5=5.0.2-1+cuda10.0 \
        && apt-get clean \
        && rm -rf /var/lib/apt/lists/*

# For CUDA profiling, TensorFlow requires CUPTI.
ENV LD_LIBRARY_PATH /usr/local/cuda/extras/CUPTI/lib64:$LD_LIBRARY_PATH

# See http://bugs.python.org/issue19846
ENV LANG C.UTF-8

#ENV TORCH_CUDA_ARCH_LIST=7.0

# Required for Python3.6
RUN apt-get update && apt-get -q install -y \
    python3-pip \
    python3 \
    git

RUN pip3 --no-cache-dir install --upgrade \
    pip

RUN apt-get update && apt-get -q install -y \
    libsm6 \
    libxext6 \
    libxrender-dev

RUN DEBIAN_FRONTEND="noninteractive" apt-get -y install tzdata

RUN apt-get -q install -y \
    python3-tk

# Install tensorflow
RUN pip3 install tensorflow-gpu==1.14.0

# Install AAE dependicies
RUN apt-get install -y \
    	libglfw3-dev \
	libglfw3 \
	libassimp-dev
RUN pip3 --no-cache-dir install --pre --upgrade PyOpenGL PyOpenGL_accelerate
RUN pip3 --no-cache-dir install \
    cython
RUN pip3 --no-cache-dir install \
    cyglfw3 \
    imageio==2.6.0 \
    pyassimp==3.3 \
    imgaug \
    progressbar \
    configparser \
    opencv-python==3.4.0.14 \
    opencv-contrib-python

# Install AAE (forked)
RUN pip3 --no-cache-dir install 'git+https://github.com/shbe-aau/AugmentedAutoencoder'
ENV AE_WORKSPACE_PATH=/shared-folder/autoencoder_ws

# Install BOP toolkit requirements
RUN pip3 --no-cache-dir install \
    pyopengl \
    pyglet \
    vispy \
    ruamel.yaml

# Install pytorch3d dependencies
RUN pip3 --no-cache-dir install \
    torch==1.4.0 \
    torchvision==0.5.0

RUN pip3 --no-cache-dir install \
    imageio \
    matplotlib \
    scikit-image

# Install pytorch3d (forked)
ENV FORCE_CUDA=1
RUN pip3 --no-cache-dir install 'git+https://github.com/shbe-aau/pytorch3d.git'

# Missing stuff
RUN pip3 --no-cache-dir install \
    pypng \
    pytz \
    glumpy

# Python3 alias
RUN ln -s /usr/bin/python3 /usr/bin/python & \
    ln -s /usr/bin/pip3 /usr/bin/pip
