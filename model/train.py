"""Track B baseline: single-view (uav / svi) or cross-view (both) classifier on your own labelled pairs.

  python model/train.py --name alice --target material_rooftop --view uav
  python model/train.py --name alice --target material_wall --view both

Needs: pip install -r requirements-model.txt
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from PIL import Image
from torchvision import models, transforms

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from utils.labels import LABEL_COLUMNS  # noqa: E402

SUBMISSION = ROOT / "submission"
NORM = transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
TRAIN_TF = transforms.Compose([transforms.RandomHorizontalFlip(), transforms.ColorJitter(0.2, 0.2), transforms.ToTensor(), NORM])
EVAL_TF = transforms.Compose([transforms.ToTensor(), NORM])


class PairDataset(torch.utils.data.Dataset):
    def __init__(self, df, classes, view, tf):
        self.df, self.classes, self.view, self.tf = df.reset_index(drop=True), classes, view, tf

    def __len__(self):
        return len(self.df)

    def __getitem__(self, i):
        row = self.df.iloc[i]
        views = ["uav", "svi"] if self.view == "both" else [self.view]
        imgs = [self.tf(Image.open(SUBMISSION / v / f"{row.osm_id}.png").convert("RGB")) for v in views]
        return imgs, self.classes.index(row.label)


def backbone():
    net = models.resnet18(weights=models.ResNet18_Weights.DEFAULT)
    net.fc = nn.Identity()
    return net


class Model(nn.Module):
    def __init__(self, n_views, n_classes):
        super().__init__()
        self.encoders = nn.ModuleList(backbone() for _ in range(n_views))
        self.head = nn.Linear(512 * n_views, n_classes)

    def forward(self, imgs):
        return self.head(torch.cat([enc(x) for enc, x in zip(self.encoders, imgs)], dim=1))


def run_epoch(model, loader, device, optimizer=None):
    model.train(optimizer is not None)
    correct = total = 0
    for imgs, y in loader:
        imgs, y = [x.to(device) for x in imgs], y.to(device)
        with torch.set_grad_enabled(optimizer is not None):
            logits = model(imgs)
            loss = nn.functional.cross_entropy(logits, y)
        if optimizer:
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
        correct += (logits.argmax(1) == y).sum().item()
        total += len(y)
    return correct / max(total, 1)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--name", required=True)
    p.add_argument("--target", required=True, choices=LABEL_COLUMNS)
    p.add_argument("--view", required=True, choices=["uav", "svi", "both"])
    p.add_argument("--epochs", type=int, default=15)
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args()

    torch.manual_seed(args.seed)
    df = pd.read_csv(SUBMISSION / f"labels_{args.name}.csv", dtype=str, keep_default_na=False)
    df = df.rename(columns={args.target: "label"})
    df = df[(df["label"] != "") & (df["label"] != "unknown")]
    classes = sorted(df["label"].unique())
    if len(classes) < 2 or len(df) < 20:
        raise SystemExit(f"Not enough labelled data: {len(df)} rows, classes {classes}")

    idx = np.random.default_rng(args.seed).permutation(len(df))
    cut = int(0.8 * len(df))
    train_df, val_df = df.iloc[idx[:cut]], df.iloc[idx[cut:]]
    majority = val_df["label"].value_counts(normalize=True).max()

    loader = lambda d, tf, shuffle: torch.utils.data.DataLoader(PairDataset(d, classes, args.view, tf), batch_size=16, shuffle=shuffle)
    train_dl, val_dl = loader(train_df, TRAIN_TF, True), loader(val_df, EVAL_TF, False)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = Model(2 if args.view == "both" else 1, len(classes)).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-4)
    for epoch in range(1, args.epochs + 1):
        tr = run_epoch(model, train_dl, device, optimizer)
        va = run_epoch(model, val_dl, device)
        print(f"epoch {epoch:02d}  train_acc {tr:.3f}  val_acc {va:.3f}  (majority baseline {majority:.3f})")
    print(f"classes: {classes}")


if __name__ == "__main__":
    main()
