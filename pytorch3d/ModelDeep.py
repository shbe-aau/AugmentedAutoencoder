import os
import torch
import torch.nn as nn
import torch.nn.functional as F

# Network structure inspired by:
# https://arxiv.org/pdf/1708.05628.pdf (see fig. 2)
class Model(nn.Module):
    def __init__(self, num_views=4):
        super(Model, self).__init__()

        self.num_views = num_views
        self.output_size = self.num_views*(6+1)

        # Regress pose
        self.l1 = nn.Linear(128,128)
        self.l1_int1 = nn.Linear(128,128)
        self.l1_int2 = nn.Linear(128,128)
        self.l1_int3 = nn.Linear(128,128)
        self.l2 = nn.Linear(128,64)
        self.l2_int1 = nn.Linear(64,64)
        self.l2_int2 = nn.Linear(64,64)
        self.l2_int3 = nn.Linear(64,64)

        self.l2_int4 = nn.Linear(64,32)
        self.l2_int5 = nn.Linear(32,32)
        self.l3 = nn.Linear(32,self.output_size)

        self.bn1 = nn.BatchNorm1d(128)
        self.bn1_int1 = nn.BatchNorm1d(128)
        self.bn1_int2 = nn.BatchNorm1d(128)
        self.bn1_int3 = nn.BatchNorm1d(128)
        self.bn2 = nn.BatchNorm1d(64)
        self.bn2_int1 = nn.BatchNorm1d(64)
        self.bn2_int2 = nn.BatchNorm1d(64)
        self.bn2_int3 = nn.BatchNorm1d(64)
        self.bn2_int4 = nn.BatchNorm1d(32)
        self.bn2_int5 = nn.BatchNorm1d(32)


    # Input: x = lantent code
    # Output: y = pose as quaternion
    def forward(self,x):
        x = F.relu(self.bn1(self.l1(x)))
        x = F.relu(self.bn1_int1(self.l1_int1(x)))
        x = F.relu(self.bn1_int2(self.l1_int2(x)))
        x = F.relu(self.bn1_int3(self.l1_int3(x)))
        x = F.relu(self.bn2(self.l2(x)))
        x = F.relu(self.bn2_int1(self.l2_int1(x)))
        x = F.relu(self.bn2_int2(self.l2_int2(x)))
        x = F.relu(self.bn2_int3(self.l2_int3(x)))
        x = F.relu(self.bn2_int4(self.l2_int4(x)))
        x = F.relu(self.bn2_int5(self.l2_int5(x)))

        y = self.l3(x)
        confs = F.softmax(y[:,:self.num_views], dim=1)
        return torch.cat([confs, y[:,self.num_views:]], dim=1)