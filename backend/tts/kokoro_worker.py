"""Synthesize a batch of lines with Kokoro's CPU runtime."""
import json
import os
import sys
import wave
from pathlib import Path

import numpy as np
from kokoro_onnx import Kokoro

model_dir = Path(sys.argv[1])
job = json.load(sys.stdin)
os.environ["ONNX_PROVIDER"] = "CPUExecutionProvider"
model = Kokoro(str(model_dir / "kokoro-v1.0.onnx"), str(model_dir / "voices-v1.0.bin"))
for item in job["items"]:
    audio, rate = model.create(item["text"], voice=job["voice"], lang=job["language"], speed=1.0)
    with wave.open(item["path"], "wb") as output:
        output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        output.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
