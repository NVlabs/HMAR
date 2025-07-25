# Copyright (c) 2025, NVIDIA Corporation. All rights reserved.
#
# This work is made available under the NVIDIA One-Way Noncommercial License v1 (NSCLv1).
# To view a copy of this license, please refer to LICENSE

# Adapted from https://github.com/FoundationVision/VAR/blob/main/utils/arg_util.py

import json
import os
import random
import re
import sys
from collections import OrderedDict
from typing import Optional, Union

import numpy as np
import torch
import yaml
from tap import Tap

import dist

def _seed_everything(seed, benchmark: bool):
        torch.backends.cudnn.enabled = True
        torch.backends.cudnn.benchmark = benchmark
        if seed is None:
            torch.backends.cudnn.deterministic = False
        else:
            torch.backends.cudnn.deterministic = True
            seed = seed * dist.get_world_size() + dist.get_rank()
            os.environ['PYTHONHASHSEED'] = str(seed)
            random.seed(seed)
            np.random.seed(seed)
            torch.manual_seed(seed)
            if torch.cuda.is_available():
                torch.cuda.manual_seed(seed)
                torch.cuda.manual_seed_all(seed)


def _set_tf32(tf32: bool):
    if torch.cuda.is_available():
        torch.backends.cudnn.allow_tf32 = bool(tf32)
        torch.backends.cuda.matmul.allow_tf32 = bool(tf32)
        if hasattr(torch, 'set_float32_matmul_precision'):
            torch.set_float32_matmul_precision('high' if tf32 else 'highest')
            print(f'[tf32] [precis] torch.get_float32_matmul_precision(): {torch.get_float32_matmul_precision()}')
        print(f'[tf32] [ conv ] torch.backends.cudnn.allow_tf32: {torch.backends.cudnn.allow_tf32}')
        print(f'[tf32] [matmul] torch.backends.cuda.matmul.allow_tf32: {torch.backends.cuda.matmul.allow_tf32}')
  
def _compile_model(m, fast):
        if fast == 0:
            return m
        return torch.compile(m, mode={
            1: 'reduce-overhead',
            2: 'max-autotune',
            3: 'default',
        }[fast]) if hasattr(torch, 'compile') else m
                
def _get_yaml_loader():
    #https://stackoverflow.com/questions/30458977/yaml-loads-5e-6-as-string-and-not-a-number
    loader = yaml.SafeLoader
    loader.add_implicit_resolver(
        u'tag:yaml.org,2002:float',
        re.compile(u'''^(?:
        [-+]?(?:[0-9][0-9_]*)\\.[0-9_]*(?:[eE][-+]?[0-9]+)?
        |[-+]?(?:[0-9][0-9_]*)(?:[eE][-+]?[0-9]+)
        |\\.[0-9_]+(?:[eE][-+][0-9]+)?
        |[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\\.[0-9_]*
        |[-+]?\\.(?:inf|Inf|INF)
        |\\.(?:nan|NaN|NAN))$''', re.X),
        list(u'-+0123456789.'))
    return loader
      
class Args(Tap):
    data_path: str = '/path/to/imagenet'
    experiment: str = 'default'
    base_experiment: str = ''

    # VAE
    vfast: int = 0      # torch.compile VAE; =0: not compile; 1: compile with 'reduce-overhead'; 2: compile with 'max-autotune'

    # HMAR
    tfast: int = 0      # torch.compile HMAR; =0: not compile; 1: compile with 'reduce-overhead'; 2: compile with 'max-autotune'
    depth: int = 16     # HMAR depth
    reweight_loss: bool = False
    loss_reweight_type: str = "lognorm"

    # HMAR initialization
    ini: float = -1     # -1: automated model parameter initialization
    hd: float = 0.02    # head.w *= hd
    aln: float = 0.5    # the multiplier of ada_lin.w's initialization
    alng: float = 1e-5  # the multiplier of ada_lin.w[gamma channels]'s initialization

    # HMAR optimization
    fp16: int = 0           # 1: using fp16, 2: bf16
    tblr: float = 1e-4      # base lr
    tlr: float = None       # lr = base lr * (bs / 256)
    twd: float = 0.05       # initial wd
    twde: float = 0         # final wd, =twde or twd
    tclip: float = 2.       # <=0 for not using grad clip
    ls: float = 0.0         # label smooth

    bs: int = 768           # global batch size
    batch_size: int = 0     # [automatically set; don't specify this] batch size per GPU = round(args.bs / args.ac / dist.get_world_size() / 8) * 8
    glb_batch_size: int = 0 # [automatically set; don't specify this] global batch size = args.batch_size * dist.get_world_size()
    ac: int = 1             # gradient accumulation

    ep: int = 250
    wp: float = 0
    wp0: float = 0.005      # initial lr ratio at the begging of lr warm up
    wpe: float = 0.01       # final lr ratio at the end of training
    sche: str = 'lin0'      # lr schedule

    opt: str = 'adamw'      #
    afuse: bool = True      # fused adamw

    # other hps
    saln: bool = False      # whether to use shared adaln
    anorm: bool = True      # whether to use L2 normalized attention
    fuse: bool = True       # whether to use fused op like flash attn, xformers, fused MLP, fused LayerNorm, etc.

    # data
    pn: str = '1_2_3_4_5_6_8_10_13_16'
    patch_size: int = 16
    patch_nums: tuple = None    # [automatically set; don't specify this] = tuple(map(int, args.pn.replace('-', '_').split('_')))
    resos: tuple = None         # [automatically set; don't specify this] = tuple(pn * args.patch_size for pn in args.patch_nums)

    data_load_reso: int = None  # [automatically set; don't specify this] would be max(patch_nums) * patch_size
    mid_reso: float = 1.125     # aug: first resize to mid_reso = 1.125 * data_load_reso, then crop to data_load_reso
    hflip: bool = False         # augmentation: horizontal flip
    workers: int = 0        # num workers; 0: auto, -1: don't use multiprocessing in DataLoader

    # would be automatically set in runtime
    cmd: str = ' '.join(sys.argv[1:])  # [automatically set; don't specify this]
    
    acc_mean: float = None      # [automatically set; don't specify this]
    acc_tail: float = None      # [automatically set; don't specify this]
    L_mean: float = None        # [automatically set; don't specify this]
    L_tail: float = None        # [automatically set; don't specify this]
    vacc_mean: float = None     # [automatically set; don't specify this]
    vacc_tail: float = None     # [automatically set; don't specify this]
    vL_mean: float = None       # [automatically set; don't specify this]
    vL_tail: float = None       # [automatically set; don't specify this]
    grad_norm: float = None     # [automatically set; don't specify this]
    cur_lr: float = None        # [automatically set; don't specify this]
    cur_wd: float = None        # [automatically set; don't specify this]
    cur_it: str = ''            # [automatically set; don't specify this]
    cur_ep: str = ''            # [automatically set; don't specify this]
    remain_time: str = ''       # [automatically set; don't specify this]
    finish_time: str = ''       # [automatically set; don't specify this]

    # environment
    shared_dir_path: str = '.'
    base_experiment_dir_path: str = '.'      # [automatically set; don't specify this]
    experiment_dir_path: str = '.'    # [automatically set; don't specify this]
    base_model_dir_path: str = '.'      # [automatically set; don't specify this]
    log_txt_path: str = '...'           # [automatically set; don't specify this]
    last_ckpt_path: str = '...'         # [automatically set; don't specify this]

    tf32: bool = True       # whether to use TensorFloat32
    device: str = 'cpu'     # [automatically set; don't specify this]
    seed: int = 42        # seed

    #logging
    wandb_entity : str  = None
    wandb_project : str = None
    wandb_resume : str = 'allow'
    wandb_id : str = f"{experiment}"
    log_to_wandb: bool = False  
    log_ckpt_to_wandb_every: int = 50  # every n epochs
    log_imgs_iters: int = 2000   # every n iterations
    
    #finetune-mask
    n_layers_train: int = 2 #how many layers to not freezee for finetune


    #evlaution
    eval_classes = [41, 595, 732, 521, 388, 154, 600, 434, 338, 752, 688, 748, 816, 660, 77, 930, 816, 498, 676, 50, 630, 593, 926, 142, 197, 79, 775, 40, 532, 212, 227, 621, 55, 809, 936, 809, 179, 835, 412, 770, 597, 608, 342, 305, 874, 822, 106, 114, 977, 13, 619, 770, 765, 87, 596, 269, 9, 669, 531, 385, 799, 811, 753, 221]
    checkpoint_frequency: int = 10 #save checkpoint every n epochs
    max_num_checkpoints: int = 3 #maximum number of checkpoints to keep
    ckpt_path: str = None #path to the checkpoint to evaluate

    def seed_everything(self, benchmark: bool):
        _seed_everything(self.seed, benchmark)
        
    same_seed_for_all_ranks: int = 0     # this is only for distributed sampler

    def get_different_generator_for_each_rank(self) -> Optional[torch.Generator]:   # for random augmentation
        if self.seed is None:
            return None
        g = torch.Generator()
        g.manual_seed(self.seed * dist.get_world_size() + dist.get_rank())
        return g

    def compile_model(self, m, fast):
        return _compile_model(m, fast)

    def state_dict(self, key_ordered=True) -> Union[OrderedDict, dict]:
        d = (OrderedDict if key_ordered else dict)()
        # self.as_dict() would contain methods, but we only need variables
        for k in self.class_variables.keys():
            if k not in {'device'}:     # these are not serializable
                d[k] = getattr(self, k)
        return d

    def load_state_dict(self, d: Union[OrderedDict, dict, str]):
        if isinstance(d, str):  # for compatibility with old version
            d: dict = eval('\n'.join([l for l in d.splitlines() if '<bound' not in l and 'device(' not in l]))
        for k in d.keys():
            try:
                setattr(self, k, d[k])
            except Exception as e:
                print(f'k={k}, v={d[k]}')
                raise e

    @staticmethod
    def set_tf32(tf32: bool):
        _set_tf32(tf32)

    def dump_log(self):
        if not dist.is_local_master():
            return
        if '1/' in self.cur_ep: # first time to dump log
            with open(self.log_txt_path, 'w') as fp:
                json.dump({'is_master': dist.is_master(), 'name': self.experiment, 'cmd': self.cmd}, fp, indent=0)
                fp.write('\n')

        log_dict = {}
        for k, v in {
            'it': self.cur_it, 'ep': self.cur_ep,
            'lr': self.cur_lr, 'wd': self.cur_wd, 'grad_norm': self.grad_norm,
            'L_mean': self.L_mean, 'L_tail': self.L_tail, 'acc_mean': self.acc_mean, 'acc_tail': self.acc_tail,
            'vL_mean': self.vL_mean, 'vL_tail': self.vL_tail, 'vacc_mean': self.vacc_mean, 'vacc_tail': self.vacc_tail,
            'remain_time': self.remain_time, 'finish_time': self.finish_time,
        }.items():
            if hasattr(v, 'item'): v = v.item()
            log_dict[k] = v
        with open(self.log_txt_path, 'a') as fp:
            fp.write(f'{log_dict}\n')

    def __str__(self):
        s = []
        for k in self.class_variables.keys():
            if k not in {'device', 'dbg_ks_fp'}:     # these are not serializable
                s.append(f'  {k:20s}: {getattr(self, k)}')
        s = '\n'.join(s)
        return f'{{\n{s}\n}}\n'


def init_dist_and_get_args(init_dist: bool = True, validate_args=True) -> Args:
    for i in range(len(sys.argv)):
        if sys.argv[i].startswith('--local-rank=') or sys.argv[i].startswith('--local_rank='):
            del sys.argv[i]
            break
    args = Args(explicit_bool=True).parse_args(known_only=True)
    
    loader = _get_yaml_loader()

    try:
        with open(f'config/experiment/{args.experiment}.yaml', 'r') as file:
            # Parse the YAML data into a Python dictionary
            config = yaml.load(file, Loader=loader)
            for key, value in config.items():
                if hasattr(args, key):
                    setattr(args, key, value)
    except FileNotFoundError:
        if validate_args:
            exit(f'{"*"*40}  please specify a valid experiment name  {"*"*40}')


    if args.data_path == '/path/to/imagenet' and validate_args:
        raise ValueError(f'{"*"*40}  please specify --data_path=/path/to/imagenet  {"*"*40}')

    if args.log_to_wandb == True and (args.wandb_entity == None or args.wandb_project == None):
        raise ValueError(f'{"*"*40}  please specify a valid wandb_entity and wandb_project {"*"*40}')
    
    # warn args.extra_args
    if len(args.extra_args) > 0:
        print(f'======================================================================================')
        print(f'=========================== WARNING: UNEXPECTED EXTRA ARGS ===========================\n{args.extra_args}')
        print(f'=========================== WARNING: UNEXPECTED EXTRA ARGS ===========================')
        print(f'======================================================================================\n\n')

    #update the local output directory
    args.shared_dir_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), args.shared_dir_path) 
    args.experiment_dir_path = os.path.join(args.shared_dir_path, f'experiments/{args.experiment}')
    args.base_experiment_dir_path = os.path.join(args.shared_dir_path, f'experiments/{args.base_experiment}')

    # init torch distributed
    from utils import misc
    os.makedirs(args.experiment_dir_path, exist_ok=True)
    if init_dist:
        misc.init_distributed_mode(local_out_path=args.experiment_dir_path, timeout=30)

    # set env
    args.set_tf32(args.tf32)
    args.seed_everything(benchmark=True)

    # update args: data loading
    args.device = dist.get_device()
    if args.pn == '256':
        args.pn = '1_2_3_4_5_6_8_10_13_16'
    elif args.pn == '512':
        args.pn = '1_2_3_4_6_9_13_18_24_32'
    elif args.pn == '1024':
        args.pn = '1_2_3_4_5_7_9_12_16_21_27_36_48_64'
    args.patch_nums = tuple(map(int, args.pn.replace('-', '_').split('_')))
    args.resos = tuple(pn * args.patch_size for pn in args.patch_nums)
    args.data_load_reso = max(args.resos)

    # update args: bs and lr
    bs_per_gpu = round(args.bs / args.ac / dist.get_world_size())
    args.batch_size = bs_per_gpu
    args.bs = args.glb_batch_size = args.batch_size * dist.get_world_size()
    args.workers = min(max(0, args.workers), args.batch_size)

    args.tlr = args.ac * args.tblr * args.glb_batch_size / 256
    args.twde = args.twde or args.twd

    if args.wp == 0:
        args.wp = args.ep * 1/50
        
    # update args: paths
    args.log_txt_path = os.path.join(args.experiment_dir_path, 'log.txt')
    args.last_ckpt_path = os.path.join(args.experiment_dir_path, f'ar-ckpt-last.pth')

    # update args: wandb
    args.wandb_id = f"{args.experiment}"
    
    return args