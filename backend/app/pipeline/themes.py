"""Local theme catalog and script-aware selection of reusable media."""
import json
from threading import Lock

from ..config import settings
from ..files import atomic_output
from ..models import ThemeAsset, ThemeMatchRequest
from . import broll, music, sfx, visuals
from .llm import LLM

_lock = Lock()


def _path():
    return settings.data_dir / "theme_library.json"


def _records() -> list[dict]:
    path = _path()
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []


def _exists(kind: str, asset: str, videos: dict) -> bool:
    if kind == "music":
        return sfx.music_path(asset) is not None
    if kind == "video":
        return asset in videos
    try:
        visuals.image_path(asset)
    except KeyError:
        return False
    return True


def catalog() -> list[dict]:
    videos = broll.library_assets()
    return [item for item in _records() if _exists(item["kind"], item["asset"], videos)]


def save(item: ThemeAsset) -> dict:
    record = item.model_dump()
    record["theme"] = item.theme.strip()
    record["description"] = item.description.strip()
    if not record["theme"] or not record["description"]:
        raise ValueError("Enter a theme and describe the reusable asset")
    if not _exists(item.kind, item.asset, broll.library_assets()):
        raise ValueError("That asset is no longer available")
    path = _path()
    with _lock:
        entries = [entry for entry in _records()
                   if (entry["theme"].casefold(), entry["kind"], entry["asset"])
                   != (record["theme"].casefold(), item.kind, item.asset)]
        if len(entries) >= 1000:
            raise ValueError("The theme library holds up to 1,000 tagged assets")
        path.parent.mkdir(parents=True, exist_ok=True)
        with atomic_output(path) as staged:
            staged.write_text(json.dumps([record, *entries], ensure_ascii=False), encoding="utf-8")
    return record


def describe_reference(image_id: str, llm: LLM) -> str:
    image = visuals.image_path(image_id)
    response = llm.chat_json(
        "Describe the visible elements that should stay consistent across later images. Include the "
        "specific subject, clothing or object details, setting, color palette, lighting and visual style. "
        "Use concise, concrete English, up to 350 characters. Omit image composition and pose.",
        {"type": "object", "required": ["reference"], "properties": {
            "reference": {"type": "string", "minLength": 1, "maxLength": 350},
        }},
        temperature=0.1, think=settings.vision_think, images=[image.read_bytes()],
    )
    reference = response.get("reference") if isinstance(response, dict) else None
    if not isinstance(reference, str) or not reference.strip():
        raise RuntimeError("Gemma could not describe the visual reference")
    return reference.strip()[:350]


def match(request: ThemeMatchRequest, llm: LLM) -> dict:
    candidates = [item for item in catalog() if item["theme"].casefold() == request.theme.strip().casefold()]
    # Untagged local footage remains eligible when a real action would be better than a still.
    included = {item["asset"] for item in candidates if item["kind"] == "video"}
    for ident, (_, name) in list(broll.library_assets().items()):
        if ident not in included and not name.as_posix().startswith("Generated/"):
            candidates.append({"kind": "video", "asset": ident, "description": name.as_posix()})
    candidates = candidates[:60]
    prompt = (
        "Select assets for this narrated short. Match each shot's subject, action and reference style. "
        "Prefer video for an action that must move. Select a still only if it matches the important details. "
        "Return zero-based candidate indices and a reason. Use each visual at most once. "
        "Return null for weak or missing matches. Choose at most one instrumental for the entire short, "
        "favoring space under speech and matching mood and energy. Give a reason for each choice or miss. "
        "Always provide a fallback English instrumental prompt with tempo, instruments and no vocals."
    )
    pick_schema = {"type": "object", "required": ["candidate", "reason"], "properties": {
        "candidate": {"type": ["integer", "null"]}, "reason": {"type": "string"},
    }}
    output = llm.chat_json(
        prompt + "\n" + json.dumps({"narration": request.text, "theme": request.theme,
            "reference": request.reference, "scenes": [scene.prompt for scene in request.scenes],
            "candidates": [{"index": i, "type": item["kind"], "description": item["description"]}
                           for i, item in enumerate(candidates)]}, ensure_ascii=False),
        {"type": "object", "required": ["scenes", "music", "music_prompt"], "properties": {
            "scenes": {"type": "array", "minItems": len(request.scenes), "maxItems": len(request.scenes),
                       "items": pick_schema},
            "music": pick_schema, "music_prompt": {"type": "string", "maxLength": 480},
        }}, temperature=0.1, think=settings.content_think,
    )
    if not isinstance(output, dict):
        raise RuntimeError("Gemma did not return a usable asset plan")
    scenes = output.get("scenes")
    if not isinstance(scenes, list) or len(scenes) != len(request.scenes):
        raise RuntimeError("Gemma did not return a match for every scene")
    used = set()

    def resolve(pick, kinds, fallback):
        if not isinstance(pick, dict):
            return {"asset": None, "reason": fallback}
        index, reason = pick.get("candidate"), pick.get("reason")
        if index is None:
            return {"asset": None, "reason": str(reason or fallback)[:220]}
        if type(index) is int and 0 <= index < len(candidates):
            item = candidates[index]
            key = (item["kind"], item["asset"])
            if item["kind"] in kinds and key not in used:
                used.add(key)
                return {"asset": item, "reason": str(reason or "Matches the story.")[:220]}
        return {"asset": None, "reason": fallback}

    fallback = output.get("music_prompt")
    visuals_matched = [resolve(pick, {"image", "video"}, "No suitable visual; generate this scene.") for pick in scenes]
    return {"scenes": visuals_matched,
            "music": resolve(output.get("music"), {"music"}, "No suitable music; generate an instrumental."),
            "music_prompt": fallback.strip()[:480] if isinstance(fallback, str) and fallback.strip()
            else music.suggest(request.text, llm)}
