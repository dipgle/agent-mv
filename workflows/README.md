# ComfyUI Workflows

Mỗi modality cần 1 workflow JSON để ComfyUI biết chuỗi node nào chạy. Workflow là **đặc thù ComfyUI** — bạn không tự code, mà export từ ComfyUI UI.

## Cách lấy workflow

### Option 1 — Workflow chính thức từ ComfyUI Manager (recommended)

1. Mở ComfyUI: http://localhost:8188
2. Sidebar → **Manager** → **Install Custom Nodes**
3. Cài: `ComfyUI-FluxDev`, `ComfyUI-LTX-Video`, `ComfyUI-F5-TTS`, `ComfyUI-StableAudio`
4. Sidebar → **Workflows** → **Templates** → chọn workflow tương ứng
5. Load → menu **File → Save (API Format)** → save vào `workflows/<name>.json`

### Option 2 — Download từ community

| Workflow | Source |
|---|---|
| Flux keyframe | https://comfyanonymous.github.io/ComfyUI_examples/flux/ |
| LTX-Video motion | https://github.com/Lightricks/ComfyUI-LTXVideo |
| F5-TTS | https://github.com/SWivid/F5-TTS (ComfyUI example) |
| Stable Audio | https://github.com/Stability-AI/stable-audio-tools |
| Whisper STT | https://github.com/ltdrdata/ComfyUI-Whisper |

### Option 3 — Tự build trong ComfyUI UI

Drag nodes, connect, test, save API format. Đầy đủ tutorial: https://docs.comfy.org/tutorials

## Workflow cần có

| File | Modality | Input | Output |
|---|---|---|---|
| `flux_keyframe.json` | text→image | `prompt`, `width`, `height`, `seed`, `steps` | PNG |
| `ltx_motion.json` | image→video | `image_path`, `motion_prompt`, `duration_s`, `fps` | MP4 |
| `f5_tts.json` | text→speech | `text`, `voice_ref_wav`, `voice_ref_text` | WAV |
| `stable_audio_music.json` | text→music | `prompt`, `duration_s`, `bpm` | WAV |
| `whisper_caption.json` | audio→text | `audio_path`, `language` | SRT |

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
    "flux_keyframe": {"prompt_node": "6", "width_node": "5", ...},
    ...
}
```

## Smoke test workflow

```bash
# Activate venv
python orchestrator/lib/comfy_client.py --workflow workflows/flux_keyframe.json \
    --params '{"prompt":"blue sky","width":1024,"height":1024,"steps":20}'
```

## Đặt workflow JSON ở đâu

File JSON nằm cùng folder này (`workflows/`). Pipeline đọc theo tên trong code:
```python
WORKFLOW_DIR = Path("workflows")
flux_wf = json.loads((WORKFLOW_DIR / "flux_keyframe.json").read_text())
```

## Note về stub workflow

File `*.json.stub` trong folder này là **placeholder rỗng** — bạn PHẢI thay bằng workflow thật từ ComfyUI trước khi chạy pipeline. Pipeline sẽ throw error rõ ràng nếu detect stub.
