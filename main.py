import os
import sys
import pathlib
import functools
import typing as T
import argparse
import warnings

from tqdm import tqdm
import numpy as np
import cv2 as cv
import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F
import torch.utils.data as data
import torchvision as tv
import torchvision.transforms as tv_transforms
import pytorch_lightning as pl
import imageio

DIR_PATH = pathlib.Path(__file__).resolve().parent
sys.path.append(DIR_PATH)
import utils
import composers


warnings.filterwarnings('ignore')  # To shut down some useless shit pytorch sometimes spits out
parser = argparse.ArgumentParser(description='Main entry to train/evaluate models.')
parser.add_argument('--config', nargs='+', type=str, required=True)
parser.add_argument('-g', '--gpus', nargs='+', type=int, default=[], help='GPUs')
parser.add_argument('-b', '--baseline-checkpoint', type=str, default=None)
parser.add_argument('-m', '--model-checkpoint', type=str, default=None)
parser.add_argument('--modes', type=str, default=None)
parser.add_argument('-O', '--output-dirpath', type=str, default=None)
parser.add_argument('-L', '--log-save-dir', type=str, default=None)

args = parser.parse_args()
config = {}
for c in args.config:
    config_ = utils.parse_config(c)
    config.update(config_)
gpus = args.gpus


def main():
    os.environ['CUDA_VISIBLE_DEVICES'] = ', '.join([str(g) for g in gpus])

    # Pop metainfo
    modes = config.get('modes', 'train').split('+')
    if args.modes is not None:
        modes = args.modes

    output_dirpath = config.get('output_dir', './output')
    if args.output_dirpath is not None:
        output_dirpath = args.output_dirpath
        config['output_dirpath'] = output_dirpath

    log_save_dir = config.get('log_save_dir', './logs')
    if args.log_save_dir is not None:
        log_save_dir = args.log_save_dir
        config['log_save_dir'] = log_save_dir

    trainer__kwargs = config.get('trainer__kwargs', {})
    
    model_checkpoint = config.get('model_checkpoint', None)
    if args.model_checkpoint is not None:
        model_checkpoint = args.model_checkpoint
        config['model_checkpoint'] = model_checkpoint

    model_checkpoint_callback__monitor = config.get('model_checkpoint_callback__monitor', ['avg_val_loss'])
    model_checkpoint_callback__save_top_k = config.get('model_checkpoint_callback__save_top_k', 1)
    model_checkpoint_callback__mode = config.get('model_checkpoint_callback__mode', 'min')

    Composer = getattr(composers, config.get('composer'))

    if model_checkpoint is not None:
        print(f'Loaded: {model_checkpoint}')
        lit_model = Composer.load_from_checkpoint(model_checkpoint, **config)
    else:
        lit_model = Composer(**config)

    baseline_checkpoint = config.get('baseline_checkpoint', None)
    if args.baseline_checkpoint is not None:
        baseline_checkpoint = args.baseline_checkpoint
        config['baseline_checkpoint'] = baseline_checkpoint

    if baseline_checkpoint is not None:
        print(f'Loaded: {baseline_checkpoint}')
        BComposer = getattr(composers, config.get('baseline_composer'))
        baseline_lit_model = BComposer.load_from_checkpoint(baseline_checkpoint)
        lit_model.load_baseline(baseline_lit_model)
        del baseline_lit_model
    
    baseline_pt_checkpoint = config.get('model_pt_checkpoint', None)
    if baseline_pt_checkpoint is not None:
        print(f'Loaded: {baseline_pt_checkpoint}')
        lit_model.load_pt_baseline(baseline_pt_checkpoint)
    
    if 'train' in modes:
        # Form logger and checkpoint callback
        logger = pl.loggers.TensorBoardLogger(save_dir=log_save_dir, name='')
        checkpoint_callback = pl.callbacks.ModelCheckpoint(
            monitor=model_checkpoint_callback__monitor[0]
            , save_top_k=model_checkpoint_callback__save_top_k
            , mode=model_checkpoint_callback__mode
            , save_weights_only=False  # It allows to checkpoint generator, discriminator and all stuff
            , filename='{epoch}-' + '-'.join(['{' + m + ':.4f' + '}' for m in model_checkpoint_callback__monitor])
        )
        trainer = pl.Trainer(
            **trainer__kwargs
            , checkpoint_callback=checkpoint_callback
            , logger=logger
            , gpus=len(gpus)
        )

        train_dataset = lit_model._parse_dataset(
            config.get('train_dataset')
            , *config.get('train_dataset__args', [])
            , **config.get('train_dataset__kwargs', {})
        )
        train_data_loader = data.DataLoader(
            train_dataset
            , batch_size=config.get('batch_size', 1)
            , shuffle=True
            , num_workers=config.get('train_num_workers', 0)
        )
        val_dataset = lit_model._parse_dataset(
            config.get('val_dataset')
            , *config.get('val_dataset__args', [])
            , **config.get('val_dataset__kwargs', {})
        )
        val_data_loader = data.DataLoader(
            val_dataset
            , batch_size=1
            , num_workers=config.get('val_num_workers', 0)
        )        
        trainer.fit(lit_model, train_data_loader, val_data_loader)
    elif 'test' in modes:
        lit_model.eval()
        trainer = pl.Trainer(
            **trainer__kwargs
            , gpus=len(gpus)
        )

        test_dataset = lit_model._parse_dataset(
            config.get('test_dataset')
            , *config.get('test_dataset__args', [])
            , **config.get('test_dataset__kwargs', {})
        )
        test_data_loader = data.DataLoader(
            test_dataset
            , batch_size=config.get('batch_size', 1)
            , num_workers=config.get('test_num_workers', 0)
        )

        trainer.test(lit_model, test_data_loader)
    elif 'predict' in modes:
        lit_model.eval()
        os.makedirs(output_dirpath, exist_ok=True)

        # Deploy model on the appropriate device
        device = torch.device('cuda') if len(gpus) >= 1 else torch.device('cpu')
        lit_model.to(device)

        test_dataset = lit_model._parse_dataset(
            config.get('test_dataset')
            , *config.get('test_dataset__args', [])
            , **config.get('test_dataset__kwargs', {})
        )
        test_data_loader = data.DataLoader(
            test_dataset
            , batch_size=1
            , num_workers=config.get('test_num_workers', 0)
        )

        for i, batch in tqdm(enumerate(test_data_loader), total=len(test_data_loader)):
            lit_model.predict_step(batch, i, output_dirpath, device)
    else:
        raise NotImplementedError()


if __name__ == "__main__":
    main()
