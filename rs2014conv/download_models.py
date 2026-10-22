"""Download all models once so make_cdlc works offline afterwards.

  python -m rs2014conv.download_models
"""

from __future__ import annotations

import sys


def _hub_dir() -> str:
    try:
        import torch

        return str(__import__("pathlib").Path(torch.hub.get_dir()) / "checkpoints")
    except Exception:  # noqa: BLE001
        return "%USERPROFILE%\\.cache\\torch\\hub\\checkpoints"


def main() -> int:
    ok = True
    print("1/3 Demucs htdemucs_ft (bass separation)...")
    try:
        from demucs.pretrained import get_model

        get_model("htdemucs_ft")
        print("    OK")
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"    FAILED: {e}")
    print("2/3 Beat This! (beat tracking)...")
    try:
        from beat_this.inference import load_checkpoint

        load_checkpoint("final0")
        print("    OK")
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"    FAILED: {e}")
        print("    The model's download server (cloud.cp.jku.at, run by the Beat This! authors) was not")
        print("    reachable. It is sometimes down for maintenance - run setup.bat again later; see")
        print("    https://github.com/CPJKU/beat_this/issues for its status. Until then make_cdlc uses")
        print("    the simpler librosa beat tracker. If you get the file 'beat_this-final0.ckpt' another")
        print("    way, put it in:  " + _hub_dir())
    print("3/3 CREPE pitch model...")
    try:
        import torch
        import torchcrepe

        torchcrepe.predict(torch.zeros(1, 16000), 16000, 160, 32.0, 400.0, "full", device="cpu")
        print("    OK")
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"    FAILED: {e}")
    try:
        import torch

        print(f"GPU (CUDA) available: {torch.cuda.is_available()}"
              + (f" - {torch.cuda.get_device_name(0)}" if torch.cuda.is_available() else ""))
    except Exception:  # noqa: BLE001
        pass
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
