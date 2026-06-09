# ComfyUI Workflows

Mỗi modality cần 1 workflow JSON để ComfyUI biết chuỗi node nào chạy. Workflow là **đặc thù ComfyUI** — bạn không tự code, mà export từ ComfyUI UI.

> **License hygiene**: all default models use Apache 2.0 or MIT licenses.
> See `docs/conventions.md` "License hygiene" for the full policy.

## Cách lấy workflow

### Option 1 — Workflow chính thức từ ComfyUI Manager (recommended)

1. Mở ComfyUI: http://localhost:8188
2. Sidebar → **Manager** → **Install Custom Nodes**
3. Cài: `ComfyUI-FluxDev` (works for Schnell too), `ComfyUI-WanVideo`, `ComfyUI-F5-TTS`
4. Sidebar → **Workflows** → **Templates** → chọn workflow tương ứng
5. Load → menu **File → Save (API Format)** → save vào `workflows/<name>.json`

### Option 2 — Download từ community

| Workflow | Source |
|---|---|
| FLUX.1-schnell keyframe | https://comfyanonymous.github.io/ComfyUI_examples/flux/ (use Schnell checkpoint) |
| Wan2.1-T2V-14B motion | https://github.com/Wan-AI/Wan2.1 (see ComfyUI integration section) |
| F5-TTS | https://github.com/SWivid/F5-TTS (ComfyUI example) |
| Whisper STT | https://github.com/ltdrdata/ComfyUI-Whisper |

### Option 3 — Tự build trong ComfyUI UI

Drag nodes, connect, test, save API format. Đầy đủ tutorial: https://docs.comfy.org/tutorials

## Workflow cần có

| File | Modality | Input | Output | Model | License |
|---|---|---|---|---|---|
| `flux_schnell_keyframe.json` | text→image | `prompt`, `width`, `height`, `seed`, `steps` | PNG | FLUX.1-schnell | Apache 2.0 |
| `wan21_motion.json` | image→video | `image_path`, `motion_prompt`, `num_frames`, `fps` | MP4 | Wan2.1-T2V-14B | Apache 2.0 |
| `f5_tts.json` | text→speech | `text`, `voice_ref_wav`, `voice_ref_text` | WAV | F5-TTS | Apache 2.0 |
| `whisper_caption.json` | audio→text | `audio_path`, `language` | SRT | Whisper large-v3 | MIT |

**Note on music**: music is NOT a ComfyUI workflow. It is handled by
`orchestrator/lib/stock_music.py` using the Pixabay API + CC0 fallback library.
See `workflows/stock_music_search.json.stub` for configuration details.

**Removed workflows (non-commercial licenses):**
- `flux_keyframe.json.stub` → replaced by `flux_schnell_keyframe.json.stub`
- `ltx_motion.json.stub` → replaced by `wan21_motion.json.stub`
- `stable_audio_music.json.stub` → removed; music sourced from Pixabay API

## Workflow API call pattern

ComfyUI nhận POST `/prompt` với JSON:
```json
{
  "prompt": <workflow_json>,
  "client_id": "unique-id"
}
```
Sau đó poll `/history/<prompt_id>` đến khi done, rồi GET `/view?filename=<output>` để lấy file.

Code thực hiện ở `orchestrator/lib/comfy_client.py`.

## Customize workflow

Nếu workflow bạn dùng có node ID khác mặc định, sửa mapping trong `comfy_client.py`:
```python
NODE_MAP = {
    "flux_schnell_keyframe": {"prompt_node": "6", "width_node": "5", ...},
    "wan21_motion": {"image_node": "10", "text_node": "6", "video_node": "8", ...},
    ...
}
```

## Smoke test workflow

```bash
# Activate venv
python orchestrator/lib/comfy_client.py --workflow workflows/flux_schnell_keyframe.json \
    --params '{"prompt":"blue sky","width":1024,"height":1024,"steps":4,"cfg":0.0}'
```

Note: FLUX.1-schnell uses steps=4, cfg=0.0 (distilled model — CFG has no effect).

## Đặt workflow JSON ở đâu

File JSON nằm cùng folder này (`workflows/`). Pipeline đọc theo tên trong code:
```python
WORKFLOW_DIR = Path("workflows")
flux_wf = json.loads((WORKFLOW_DIR / "flux_schnell_keyframe.json").read_text())
wan_wf  = json.loads((WORKFLOW_DIR / "wan21_motion.json").read_text())
```

## Note về stub workflow

File `*.json.stub` trong folder này là **placeholder rỗng** — bạn PHẢI thay bằng workflow thật từ ComfyUI trước khi chạy pipeline. Pipeline sẽ throw error rõ ràng nếu detect stub.

Exception: `stock_music_search.json.stub` — đây không phải ComfyUI workflow; đọc instructions bên trong để cấu hình Pixabay API key.
