"""Train a household wake head for the nightly wake-learning job (``wake_learning/nightly.py``).

Runs in a separate Python with PyTorch and onnx (``WAKE_TRAINER_PYTHON``); the app itself never imports them.
Fine-tunes the main head's weights (openWakeWord's 16x96 -> 32 -> 32 -> 1 network) on the dataset the job wrote:
``X`` float16 windows, ``Y`` 1 for a call and 0 for anything else. Calls are oversampled to one in eight. Writes
an ONNX file whose batch size is free, so the job can score whole days quickly.

Usage: python wake_train.py --data dataset.npz --init reachy-official33.onnx --out challenger.onnx
"""

import sys
import argparse
from pathlib import Path

import onnx
import numpy as np
import torch
from onnx import numpy_helper
from torch import nn


SEED = 20261031
LEARNING_RATE = 1e-4
EPOCHS = 15
CALLS_PER_OTHER = 1 / 8
BATCH = 1024
NOISE = 0.05  # feature noise, as a fraction of the features' spread


class Block(nn.Module):
    """One hidden layer of openWakeWord's head."""

    def __init__(self, dim: int) -> None:
        """Make a ``dim``-wide layer."""
        super().__init__()
        self.fcn_layer = nn.Linear(dim, dim)
        self.layer_norm = nn.LayerNorm(dim)
        self.relu = nn.ReLU()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Apply the layer."""
        return self.relu(self.layer_norm(self.fcn_layer(x)))


class Head(nn.Module):
    """openWakeWord's DNN head, the shape of the main head."""

    def __init__(self, dim: int = 32) -> None:
        """Make the head."""
        super().__init__()
        self.flatten = nn.Flatten()
        self.layer1 = nn.Linear(16 * 96, dim)
        self.layernorm1 = nn.LayerNorm(dim)
        self.relu1 = nn.ReLU()
        self.blocks = nn.ModuleList([Block(dim)])
        self.last_layer = nn.Linear(dim, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Score windows."""
        x = self.relu1(self.layernorm1(self.layer1(self.flatten(x))))
        for block in self.blocks:
            x = block(x)
        return torch.sigmoid(self.last_layer(x))


def main() -> int:
    """Train and export; exit 0 on success."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--init", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    torch.manual_seed(SEED)
    rng = np.random.default_rng(SEED)

    dataset = np.load(args.data)
    windows, labels = dataset["X"], dataset["Y"].astype(np.float32)
    calls, others = np.flatnonzero(labels == 1), np.flatnonzero(labels == 0)
    if not len(calls) or not len(others):
        print("Need both calls and other windows", file=sys.stderr)
        return 2
    model = Head()
    weights = {
        t.name: torch.from_numpy(numpy_helper.to_array(t).copy()) for t in onnx.load(str(args.init)).graph.initializer
    }
    model.load_state_dict(weights)
    spread = float(np.asarray(windows[others[:: max(1, len(others) // 20000)]], dtype=np.float32).std())
    optimizer = torch.optim.AdamW(model.parameters(), lr=LEARNING_RATE, weight_decay=1e-3)
    loss = nn.BCELoss()
    for _ in range(EPOCHS):
        model.train()
        order = np.concatenate([rng.choice(calls, int(len(others) * CALLS_PER_OTHER)), others])
        rng.shuffle(order)
        for start in range(0, len(order), BATCH):
            batch = np.sort(order[start : start + BATCH])
            x = torch.from_numpy(np.asarray(windows[batch], dtype=np.float32))
            x = x + NOISE * spread * torch.randn_like(x)
            value = loss(model(x).squeeze(1), torch.from_numpy(labels[batch]))
            optimizer.zero_grad()
            value.backward()
            optimizer.step()
    model.eval()
    torch.onnx.export(
        model,
        torch.zeros(1, 16, 96),
        str(args.out),
        input_names=["features"],
        output_names=["score"],
        dynamic_axes={"features": {0: "batch"}, "score": {0: "batch"}},
        dynamo=False,
    )
    print(f"trained on {len(calls)} call and {len(others)} other windows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
