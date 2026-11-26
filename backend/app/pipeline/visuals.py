"""Generate images with an owned offline ComfyUI process and publish MP4 cutaways."""
import logging
import os
import re
import secrets
import socket
import subprocess
import time
import uuid
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import requests
from PIL import Image

from ..config import settings
from ..files import atomic_output
from ..models import SceneMotion, VisualPrompt
from . import broll

WIDTH, HEIGHT = 768, 1024
_ID = re.compile(r"[0-9a-f]{32}")
_POLL_SECONDS = 0.25
_MAX_PNG_BYTES = 8 * 2**20


def status() -> dict:
    root = settings.comfyui_dir.expanduser()
    ready = all(path.is_file() for path in (
        root / "venv/bin/python", root / "ComfyUI/main.py",
        root / "ComfyUI/models/checkpoints" / settings.comfyui_checkpoint,
    ))
    return {"available": ready, "setup": None if ready else
            "Local images need ComfyUI, CUDA PyTorch and the SDXL Lightning four-step checkpoint. See README."}


@contextmanager
def _server(low_memory: bool = False):
    setup = status()["setup"]
    if setup:
        raise ValueError(setup)
    directory = settings.comfyui_dir.expanduser().resolve()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with TemporaryDirectory(prefix="clipforge-comfy-") as temporary, requests.Session() as session:
        session.trust_env = False
        work = Path(temporary)
        for name in ("output", "temp", "user", "input"):
            (work / name).mkdir()
        log_path = work / "server.log"
        environment = os.environ.copy()
        # The ClipForge launcher exports Whisper's CUDA wheel libraries. ComfyUI uses its own PyTorch CUDA stack.
        if "LD_LIBRARY_PATH" in environment:
            environment["LD_LIBRARY_PATH"] = ":".join(
                path for path in environment["LD_LIBRARY_PATH"].split(":")
                if path and not any(f"/nvidia/{library}/lib" in path for library in ("cublas", "cudnn"))
            )
        flags = ["--disable-dynamic-vram", "--lowvram", "--reserve-vram", "1.5"] if low_memory else []
        with log_path.open("wb") as log:
            try:
                server = subprocess.Popen([
                    str(directory / "venv/bin/python"), "main.py", "--listen", "127.0.0.1",
                    "--port", str(port), "--offline", "--disable-all-custom-nodes",
                    "--output-directory", str(work / "output"), "--temp-directory", str(work / "temp"),
                    "--user-directory", str(work / "user"), "--input-directory", str(work / "input"),
                    *flags,
                ], cwd=directory / "ComfyUI", env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
            except OSError as error:
                raise RuntimeError("Could not launch local ComfyUI") from error
            try:
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    if server.poll() is not None:
                        raise RuntimeError("ComfyUI exited during startup")
                    try:
                        response = session.get(base + "/system_stats", timeout=1)
                        response.raise_for_status()
                        if not any(device.get("type") == "cuda" for device in response.json().get("devices", [])):
                            raise RuntimeError("ComfyUI did not detect a CUDA GPU")
                        break
                    except requests.RequestException:
                        time.sleep(0.5)
                else:
                    raise RuntimeError("Local ComfyUI did not start within 60 seconds")
                yield session, base
            except (RuntimeError, requests.RequestException, ValueError):
                logging.warning("Local ComfyUI diagnostic: %s", log_path.read_text(errors="replace")[-3000:])
                raise
            finally:
                # Exit the owned process before returning the GPU reservation, including after a timeout.
                if server.poll() is None:
                    server.terminate()
                    try:
                        server.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait()


def _workflow(prompt: str, seed: int) -> dict:
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": settings.comfyui_checkpoint}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": prompt}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": ""}},
        "4": {"class_type": "EmptyLatentImage", "inputs": {"width": WIDTH, "height": HEIGHT, "batch_size": 1}},
        "5": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0],
            "seed": seed, "steps": 4, "cfg": 1, "sampler_name": "euler", "scheduler": "sgm_uniform", "denoise": 1,
        }},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "clipforge"}},
    }


class ImageMemoryError(RuntimeError):
    pass


def _execution_error(status: dict) -> RuntimeError:
    for kind, detail in status.get("messages", []):
        if kind == "execution_error":
            message = " ".join(str(detail.get("exception_message") or "Unknown error").split())[:260]
            error_type = str(detail.get("exception_type", ""))
            if "out of memory" in message.lower() or "outofmemory" in error_type.lower():
                return ImageMemoryError("GPU memory is full. Close GPU-heavy apps and retry the remaining images.")
            return RuntimeError(f"ComfyUI failed at {detail.get('node_type') or 'a workflow node'}: {message}")
    return RuntimeError("ComfyUI stopped before it finished the image.")


def _generate(session: requests.Session, base: str, item: VisualPrompt) -> dict:
    seed = item.seed if item.seed is not None else secrets.randbits(32)
    prompt = item.prompt.strip()
    response = session.post(base + "/prompt", json={"prompt": _workflow(prompt, seed)}, timeout=15)
    response.raise_for_status()
    job_id = response.json()["prompt_id"]
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            response = session.get(base + f"/history/{job_id}", timeout=5)
        except requests.Timeout:
            # ComfyUI can be briefly unresponsive while paging model weights.
            continue
        response.raise_for_status()
        record = response.json().get(job_id)
        if record:
            if not record["status"]["completed"]:
                raise _execution_error(record["status"])
            image = record["outputs"]["7"]["images"][0]
            break
        time.sleep(_POLL_SECONDS)
    else:
        raise TimeoutError("Image generation timed out")
    with session.get(base + "/view", params=image, timeout=15, stream=True) as response:
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_content(64 * 1024):
            data.extend(chunk)
            if len(data) > _MAX_PNG_BYTES:
                raise RuntimeError("ComfyUI returned an oversized image")
    with Image.open(BytesIO(data)) as decoded:
        if decoded.format != "PNG" or decoded.size != (WIDTH, HEIGHT):
            raise RuntimeError("ComfyUI returned an unexpected image")
        decoded.verify()
    ident = uuid.uuid4().hex
    target = _root() / f"{ident}.png"
    with atomic_output(target) as staged:
        staged.write_bytes(data)
    return {"id": ident, "seed": seed}


def generate(images: list[VisualPrompt], *, progress=None) -> list[dict]:
    if any(not item.prompt.strip() for item in images):
        raise ValueError("Enter an image prompt for every selected scene")
    _root().mkdir(parents=True, exist_ok=True)
    results = []
    def render_remaining(session, base):
        for index in range(len(results), len(images)):
            if progress:
                progress(index, None)
            result = _generate(session, base, images[index])
            results.append(result)
            if progress:
                progress(index, result)

    try:
        with _server() as (session, base):
            render_remaining(session, base)
    except ImageMemoryError:
        logging.info("Retrying unfinished images with ComfyUI low-memory mode")
        try:
            with _server(low_memory=True) as (session, base):
                render_remaining(session, base)
        except (RuntimeError, OSError, ValueError, KeyError, IndexError) as error:
            results.append({"error": _failure_message(error)})
    except (RuntimeError, OSError, ValueError, KeyError, IndexError) as error:
        results.append({"error": _failure_message(error)})
    if progress and len(results) and "error" in results[-1]:
        progress(len(results) - 1, results[-1])
    return results


def _failure_message(error: Exception) -> str:
    logging.warning("Local image generation failed", exc_info=True)
    if isinstance(error, (RuntimeError, TimeoutError, ValueError)):
        return str(error)
    return "ComfyUI could not return the image. Check the ClipForge terminal for details."


def _root() -> Path:
    return settings.data_dir / "generated_images"


def image_path(ident: str) -> Path:
    if not _ID.fullmatch(ident):
        raise KeyError(ident)
    path = _root() / f"{ident}.png"
    if not path.is_file():
        raise KeyError(ident)
    return path


def save(ident: str, motion: bool | SceneMotion) -> dict:
    image = image_path(ident).resolve()
    selected = "pan-zoom" if motion is True else "still" if motion is False else motion
    label = {"pan-zoom": "pan and zoom", "still": "still", "push": "slow push", "pan": "side pan"}[selected]
    relative = Path("Generated") / f"AI image {ident[:12]} ({label}).mp4"
    output = broll.library_root() / relative
    if not output.is_file():
        output.parent.mkdir(parents=True, exist_ok=True)
        frames = 192
        crop = "crop=576:1024"
        moving = selected != "still"
        if moving:
            # Complete a gentle cycle so looping a short asset has no jump at the seam.
            angle = f"2*PI*on/{frames}"
            zoom = "1.10" if selected == "pan" else f"1.06-0.06*cos({angle})"
            horizontal = "0.5" if selected == "push" else f"0.5+0.45*sin({angle})"
            crop += (",scale=1152:2048,zoompan="
                     f"z='{zoom}':x='(iw-iw/zoom)*({horizontal})':"
                     f"y='(ih-ih/zoom)/2':d={frames}:s=720x1280:fps=24")
        else:
            crop += ",scale=720:1280,fps=24"
        with atomic_output(output) as staged:
            try:
                subprocess.run([
                    "ffmpeg", "-nostdin", "-v", "error", "-y", "-filter_threads", "1",
                    *([] if moving else ["-loop", "1", "-framerate", "24"]),
                    "-i", str(image), "-vf", f"{crop},setsar=1", "-frames:v", str(frames),
                    "-an", "-c:v", "libx264", "-preset", "veryfast", "-threads:v", "2", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", str(staged),
                ], capture_output=True, check=True, timeout=90)
            except (OSError, subprocess.SubprocessError) as error:
                raise RuntimeError("Could not turn the generated image into MP4 B-roll") from error
    return {"id": broll.asset_id(relative), "name": relative.as_posix()}
