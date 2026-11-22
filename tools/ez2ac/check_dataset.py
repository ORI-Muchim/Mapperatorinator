"""Check that a built EZ2AC dataset loads through the real training data pipeline.

Run this before a long training run. It composes the training config, loads the
pretrained V32 osu!mania tokenizer, and pulls samples from the train and test
splits exactly like osuT5/train.py does. No GPU is needed.

Usage (from the repository root):
    python tools/ez2ac/check_dataset.py --dataset /abs/path/to/datasets/ez2ac_5k --train-end 410 --test-end 433
"""
from __future__ import annotations

import argparse
import collections
import itertools
import sys
from argparse import Namespace
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# Same import root as `python osuT5/train.py`: the inner osuT5 package must win over the outer folder.
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "osuT5"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=Path, required=True, help="absolute path of the dataset folder")
    parser.add_argument("--config", default="lora_ez2ac_5k", help="training config name in configs/train")
    parser.add_argument("--train-end", type=int, required=True, help="songs [0, train-end) are the train split")
    parser.add_argument("--test-end", type=int, required=True, help="songs [train-end, test-end) are the test split")
    parser.add_argument("--samples", type=int, default=40, help="samples to pull from each split")
    args = parser.parse_args(argv)

    from hydra import compose, initialize_config_dir
    from omegaconf import OmegaConf
    from osuT5.config import TrainConfig  # noqa: F401  (registers the config store and resolvers)
    from osuT5.dataset.osu_parser import OsuParser
    from osuT5.event import EventType
    from osuT5.tokenizer import Tokenizer
    from osuT5.utils.model_utils import get_dataset

    dataset = args.dataset.resolve()
    with initialize_config_dir(config_dir=str(REPO / "configs" / "train"), version_base="1.1"):
        cfg = compose(args.config, overrides=[
            f"data.train_dataset_path={dataset.as_posix()}",
            f"data.test_dataset_path={dataset.as_posix()}",
            "data.train_dataset_start=0",
            f"data.train_dataset_end={args.train_end}",
            f"data.test_dataset_start={args.train_end}",
            f"data.test_dataset_end={args.test_end}",
            "dataloader.num_workers=0",
        ])
    train_args = OmegaConf.to_object(cfg)
    subfolder = f"gamemode={train_args.pretrained_gamemode}" if train_args.pretrained_gamemode is not None else None
    tokenizer = Tokenizer.from_pretrained(train_args.pretrained_path, subfolder=subfolder)
    if EventType.MANIA_COLUMN not in tokenizer.event_start:
        raise SystemExit("The pretrained tokenizer has no mania tokens; check pretrained_gamemode.")

    shared = Namespace(current_train_step=1)
    failed = False
    for test in (False, True):
        split = "test" if test else "train"
        ds = get_dataset(args=train_args, test=test, parser=OsuParser(train_args, tokenizer),
                         tokenizer=tokenizer, shared=shared)
        count, types = 0, collections.Counter()
        for sample in itertools.islice(iter(ds), args.samples):
            count += 1
            for token in sample["decoder_input_ids"].tolist():
                if token >= tokenizer.offset:
                    try:
                        types[tokenizer.decode(token).type.name] += 1
                    except Exception:
                        pass
        notes = types["CIRCLE"] + types["HOLD_NOTE"]
        print(f"{split}: {count} samples, {notes} notes ({types['HOLD_NOTE']} holds), "
              f"{types['MANIA_COLUMN']} lane tokens, {types['BEAT']} beat tokens")
        if count == 0 or notes == 0 or types["MANIA_COLUMN"] == 0:
            print(f"  FAIL: the {split} split produced no usable osu!mania samples")
            failed = True
    if failed:
        raise SystemExit(1)
    print("OK: the dataset loads through the training pipeline.")


if __name__ == "__main__":
    main()
