"""Stream the trainer's original overlapping validation windows without expanding them."""
import numpy as np

class FalsePositiveWindows:
    def __init__(self,path,length):
        self.features=np.load(path,mmap_mode='r')
        self.length=int(length)
        if self.length<=0 or self.features.ndim!=2:
            raise ValueError('Invalid validation shape')
    def __len__(self):
        # Match the original range(0, feature_count-window_length, 1) exactly.
        return max(0,len(self.features)-self.length)
    def __getitem__(self,index):
        if not 0<=index<len(self):raise IndexError(index)
        return np.array(self.features[index:index+self.length],copy=True),np.float32(0)

def false_positive_loader(path,length,batch_size=1024):
    import torch
    return torch.utils.data.DataLoader(FalsePositiveWindows(path,length),
                                       batch_size=batch_size,num_workers=0)
