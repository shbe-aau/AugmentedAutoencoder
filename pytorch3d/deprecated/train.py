import os
import shutil
import torch
import numpy as np
import pickle
import matplotlib as mpl
mpl.use('Agg')
import matplotlib.pyplot as plt
import configparser
import json
import argparse
import glob

from utils.utils import *

from Model import Model
from BatchRender import BatchRender
from losses import Loss

learning_rate = -1
optimizer = None
views = []
epoch = 0

dbg_memory = False

def dbg(message, flag):
    if flag:
        print(message)

def latestCheckpoint(model_dir):
    checkpoints = glob.glob(os.path.join(model_dir, "*.pt"))
    checkpoints_sorted = sorted(checkpoints, key=os.path.getmtime)
    if(len(checkpoints_sorted) > 0):
        return checkpoints_sorted[-1]
    return None

def loadCheckpoint(model_path):
    # Load checkpoint and parameters
    checkpoint = torch.load(model_path)
    epoch = checkpoint['epoch'] + 1
    learning_rate = checkpoint['learning_rate']

    # Load model
    model = Model(output_size=6)
    model.load_state_dict(checkpoint['model'])

    # Load optimizer
    optimizer = torch.optim.Adam(model.parameters(),lr=learning_rate)
    optimizer.load_state_dict(checkpoint['optimizer'])
    print("Loaded the checkpoint: \n" + model_path)
    return model, optimizer, epoch, learning_rate

def loadDataset(file_list):
    data = {"codes":[],"Rs":[]}
    for f in file_list:
        print("Loading dataset: {0}".format(f))
        with open(f, "rb") as f:
            curr_data = pickle.load(f, encoding="latin1")
            data["codes"] = data["codes"] + curr_data["codes"].copy()
            data["Rs"] = data["Rs"] + curr_data["Rs"].copy()
    return data

def renderGroundtruths(ground_truth, renderer, t):
    gt_imgs = []

    start_indecs = 0
    while start_indecs < len(ground_truth):
        curr_batch = np.arange(renderer.batch_size) + start_indecs
        curr_batch = curr_batch[curr_batch < len(ground_truth)]

        # Prepare ground truth poses
        T = np.array(t, dtype=np.float32)
        Rs = []
        ts = []
        for b in curr_batch:
            Rs.append(ground_truth[b])
            ts.append(T.copy())

        Rs_gt = torch.tensor(np.stack(Rs), device=renderer.device,
                             dtype=torch.float32)
        for v in views:
            # Render ground truth images
            Rs_new = torch.matmul(Rs_gt, v.to(renderer.device))
            gt_images = renderer.renderBatch(Rs_new, ts)
            gt_imgs.append(gt_images.detach().cpu())
        start_indecs = start_indecs + curr_batch.shape[0]
        print("Rendered grouth truth: {0}/{1}".format(start_indecs,len(ground_truth)))
    return gt_imgs

def main():
    global learning_rate, optimizer, views, epoch
    # Read configuration file
    parser = argparse.ArgumentParser()
    parser.add_argument("experiment_name")
    arguments = parser.parse_args()

    cfg_file_path = os.path.join("./experiments", arguments.experiment_name)
    args = configparser.ConfigParser()
    args.read(cfg_file_path)

    # Prepare rotation matrices for multi view loss function
    eulerViews = json.loads(args.get('Rendering', 'VIEWS'))
    views = prepareViews(eulerViews)

    # Set the cuda device
    device = torch.device("cuda:0")
    torch.cuda.set_device(device)

    # Set up batch renderer
    br = BatchRender(args.get('Dataset', 'CAD_PATH'),
                     device,
                     batch_size=args.getint('Training', 'BATCH_SIZE'),
                     faces_per_pixel=args.getint('Rendering', 'FACES_PER_PIXEL'),
                     render_method=args.get('Rendering', 'SHADER'),
                     image_size=args.getint('Rendering', 'IMAGE_SIZE'))


    # Initialize a model using the renderer, mesh and reference image
    model = Model(output_size=6).to(device)
    #model.load_state_dict(torch.load("./output/model-epoch720.pt"))

    # Create an optimizer. Here we are using Adam and we pass in the parameters of the model
    learning_rate=args.getfloat('Training', 'LEARNING_RATE')

    # Load the dataset
    data = loadDataset(json.loads(args.get('Dataset', 'TRAIN_DATA_PATH')))
    print("Loaded dataset with {0} samples!".format(len(data["codes"])))

    # Load the validationset
    val_data = loadDataset(json.loads(args.get('Dataset', 'VALID_DATA_PATH')))
    print("Loaded validation set with {0} samples!".format(len(val_data["codes"])))

    output_path = args.get('Training', 'OUTPUT_PATH')
    prepareDir(output_path)
    shutil.copy(cfg_file_path, os.path.join(output_path, cfg_file_path.split("/")[-1]))

    mean = 0
    std = 1
    #mean, std = calcMeanVar(br, data, device, json.loads(args.get('Rendering', 'T')))

    #gt_imgs = renderGroundtruths(data["Rs"], br, t=json.loads(args.get('Rendering', 'T')))

    early_stopping = args.getboolean('Training', 'EARLY_STOPPING', fallback=False)
    if early_stopping:
        window = args.getint('Training', 'STOPPING_WINDOW', fallback=10)
        time_limit = args.getint('Training', 'STOPPING_TIME_LIMIT', fallback=10)
        window_means = []
        lowest_mean = np.inf
        lowest_x = 0
        timer = 0

    # Load checkpoint for last epoch if it exists
    model_path = latestCheckpoint(os.path.join(output_path, "models/"))
    if(model_path is not None):
        model, optimizer, epoch, learning_rate = loadCheckpoint(model_path)
        #model, _, epoch, _ = loadCheckpoint(model_path)
        model.to(device)

    if early_stopping:
        validation_csv=os.path.join(output_path, "validation-loss.csv")
        if os.path.exists(validation_csv):
            with open(validation_csv) as f:
                val_reader = csv.reader(f, delimiter='\n')
                val_loss = list(val_reader)
            val_losses = np.array(val_loss, dtype=np.float32).flatten()
            for epoch in range(window,len(val_loss)):
                timer += 1
                w_mean = np.mean(val_losses[epoch-window:epoch])
                window_means.append(w_mean)
                if w_mean < lowest_mean:
                    lowest_mean = w_mean
                    lowest_x = epoch
                    timer = 0


    np.random.seed(seed=args.getint('Training', 'RANDOM_SEED'))
    while(epoch < args.getint('Training', 'NUM_ITER')):
        loss = trainEpoch(mean, std, br, data, model, device, output_path,
                          loss_method=args.get('Training', 'LOSS'),
                          t=json.loads(args.get('Rendering', 'T')),
                          visualize=args.getboolean('Training', 'SAVE_IMAGES'))
        append2file([loss], os.path.join(output_path, "train-loss.csv"))
        val_loss = testEpoch(mean, std, br, val_data, model, device, output_path,
                          loss_method=args.get('Training', 'LOSS'),
                          t=json.loads(args.get('Rendering', 'T')),
                          visualize=args.getboolean('Training', 'SAVE_IMAGES'))
        append2file([val_loss], os.path.join(output_path, "validation-loss.csv"))
        val_losses = plotLoss(os.path.join(output_path, "train-loss.csv"),
                 os.path.join(output_path, "train-loss.png"),
                 validation_csv=os.path.join(output_path, "validation-loss.csv"))
        print("-"*20)
        print("Epoch: {0} - train loss: {1} - validation loss: {2}".format(epoch,loss,val_loss))
        print("-"*20)
        if early_stopping and epoch >= window:
            timer += 1
            if timer > time_limit:
                # print stuff here
                print()
                print("-"*60)
                print("Validation loss seems to have plateaued, stopping early.")
                print("Best mean loss value over an epoch window of size {} was found at epoch {} ({:.8f} mean loss)".format(window, lowest_x, lowest_mean))
                print("-"*60)
                break
            w_mean = np.mean(val_losses[epoch-window:epoch])
            window_means.append(w_mean)
            if w_mean < lowest_mean:
                lowest_mean = w_mean
                lowest_x = epoch
                timer = 0

        epoch = epoch+1

def testEpoch(mean, std, br, val_data, model,
               device, output_path, loss_method, t,
               visualize=False):
    global learning_rate, optimizer
    with torch.no_grad():
        dbg("Before test memory: {}".format(torch.cuda.memory_summary(device=device, abbreviated=False)), dbg_memory)

        model.eval()
        losses = []
        batch_size = br.batch_size
        num_samples = len(val_data["codes"])
        data_indeces = np.arange(num_samples)

        for i,curr_batch in enumerate(batch(data_indeces, batch_size)):
            codes = []
            for b in curr_batch:
                codes.append(val_data["codes"][b])
            batch_codes = torch.tensor(np.stack(codes), device=device, dtype=torch.float32) # Bx128

            predicted_poses = model(batch_codes)

            # Prepare ground truth poses for the loss function
            T = np.array(t, dtype=np.float32)
            Rs = []
            ts = []
            for b in curr_batch:
                Rs.append(val_data["Rs"][b])
                ts.append(T.copy())

            loss, batch_loss, gt_images, predicted_images = Loss(predicted_poses, Rs, br, ts,
                                                                 mean, std, loss_method=loss_method, views=views)

            #detach all from gpu
            batch_codes.detach().cpu().numpy()
            loss.detach().cpu().numpy()
            gt_images.detach().cpu().numpy()
            predicted_images.detach().cpu().numpy()

            print("Test batch: {0}/{1} (size: {2}) - loss: {3}".format(i+1,round(num_samples/batch_size), len(Rs),loss.data))
            losses.append(loss.data.detach().cpu().numpy())

            if(visualize):
                batch_img_dir = os.path.join(output_path, "val-images/epoch{0}".format(epoch))
                prepareDir(batch_img_dir)
                gt_img = (gt_images[0]).detach().cpu().numpy()
                predicted_img = (predicted_images[0]).detach().cpu().numpy()

                vmin = min(np.min(gt_img), np.min(predicted_img))
                vmax = max(np.max(gt_img), np.max(predicted_img))


                fig = plt.figure(figsize=(5+len(views)*2, 9))
                for viewNum in np.arange(len(views)):
                    plotView(viewNum, len(views), vmin, vmax, gt_images, predicted_images,
                             predicted_poses, batch_loss, batch_size)
                fig.tight_layout()

                #plt.hist(gt_img,bins=20)
                fig.savefig(os.path.join(batch_img_dir, "epoch{0}-batch{1}.png".format(epoch,i)), dpi=fig.dpi)
                plt.close()

                # fig = plt.figure(figsize=(4,4))
                # plt.imshow(data["images"][curr_batch[0]])
                # fig.savefig(os.path.join(batch_img_dir, "epoch{0}-batch{1}-gt.png".format(epoch,i)), dpi=fig.dpi)
                # plt.close()

        dbg("After test memory: {}".format(torch.cuda.memory_summary(device=device, abbreviated=False)), dbg_memory)
        return np.mean(losses)

def trainEpoch(mean, std, br, data, model,
               device, output_path, loss_method, t,
               visualize=False):
    global learning_rate, optimizer
    dbg("Before train memory: {}".format(torch.cuda.memory_summary(device=device, abbreviated=False)), dbg_memory)

    model.train()
    losses = []
    batch_size = br.batch_size
    num_samples = len(data["codes"])
    data_indeces = np.arange(num_samples)

    if(epoch % 2 == 1):
        learning_rate = learning_rate * 0.9
        print("Current learning rate: {0}".format(learning_rate))

    optimizer = torch.optim.Adam(model.parameters(),lr=learning_rate)
    np.random.shuffle(data_indeces)
    for i,curr_batch in enumerate(batch(data_indeces, batch_size)):
        optimizer.zero_grad()
        codes = []
        for b in curr_batch:
            codes.append(data["codes"][b])
        batch_codes = torch.tensor(np.stack(codes), device=device, dtype=torch.float32) # Bx128

        predicted_poses = model(batch_codes)

        # Prepare ground truth poses for the loss function
        T = np.array(t, dtype=np.float32)
        Rs = []
        ts = []
        for b in curr_batch:
            Rs.append(data["Rs"][b])
            ts.append(T.copy())

        loss, batch_loss, gt_images, predicted_images = Loss(predicted_poses, Rs, br, ts,
                                                             mean, std, loss_method=loss_method, views=views)
        loss.backward()
        optimizer.step()

        #detach all from gpu
        batch_codes.detach().cpu().numpy()
        loss.detach().cpu().numpy()
        gt_images.detach().cpu().numpy()
        predicted_images.detach().cpu().numpy()

        print("Batch: {0}/{1} (size: {2}) - loss: {3}".format(i+1,round(num_samples/batch_size), len(Rs),loss.data))
        losses.append(loss.data.detach().cpu().numpy())

        if(visualize):
            batch_img_dir = os.path.join(output_path, "images/epoch{0}".format(epoch))
            prepareDir(batch_img_dir)
            gt_img = (gt_images[0]).detach().cpu().numpy()
            predicted_img = (predicted_images[0]).detach().cpu().numpy()

            vmin = min(np.min(gt_img), np.min(predicted_img))
            vmax = max(np.max(gt_img), np.max(predicted_img))


            fig = plt.figure(figsize=(5+len(views)*2, 9))
            for viewNum in np.arange(len(views)):
                plotView(viewNum, len(views), vmin, vmax, gt_images, predicted_images,
                         predicted_poses, batch_loss, batch_size)
            fig.tight_layout()

            #plt.hist(gt_img,bins=20)
            fig.savefig(os.path.join(batch_img_dir, "epoch{0}-batch{1}.png".format(epoch,i)), dpi=fig.dpi)
            plt.close()

            # fig = plt.figure(figsize=(4,4))
            # plt.imshow(data["images"][curr_batch[0]])
            # fig.savefig(os.path.join(batch_img_dir, "epoch{0}-batch{1}-gt.png".format(epoch,i)), dpi=fig.dpi)
            # plt.close()

    model_dir = os.path.join(output_path, "models/")
    prepareDir(model_dir)
    state = {'model': model.state_dict(),
             'optimizer': optimizer.state_dict(),
             'learning_rate': learning_rate,
             'epoch': epoch}
    torch.save(state, os.path.join(model_dir,"model-epoch{0}.pt".format(epoch)))
    dbg("After train memory: {}".format(torch.cuda.memory_summary(device=device, abbreviated=False)), dbg_memory)
    return np.mean(losses)

if __name__ == '__main__':
    main()
