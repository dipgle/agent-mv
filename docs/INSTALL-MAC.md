# Cài đặt — macOS (Apple Silicon)

## Yêu cầu hệ thống

| Thành phần | Mức tối thiểu | Khuyến nghị |
|---|---|---|
| OS | macOS 14 Sonoma | macOS 15 Sequoia+ |
| Chip | M1/M2 Pro 16 GB | M3/M4 Max 64 GB |
| Storage | 200 GB free | 500 GB+ |
| Network | 50 Mbps | 200 Mbps+ |

> **Intel Mac**: không hỗ trợ — CUDA/MPS đều không có, chỉ chạy CPU rất chậm.

## Bước 1 — Cài Homebrew (nếu chưa có)

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

## Bước 2 — Clone hoặc extract folder

```bash
mkdir -p ~/dev
cd ~/dev
# Hoặc extract zip tới ~/dev/video
git clone <repo-url> video
cd video
```

## Bước 3 — Chạy setup

```bash
chmod +x infra/setup.sh
./infra/setup.sh
```

Script sẽ:
1. `brew install` Python 3.11, ffmpeg, sqlite3, ollama
2. Pull text models qua Ollama
3. Clone ComfyUI + cài PyTorch MPS + dependencies
4. Download model weights từ HuggingFace (~42 GB)
5. Setup Python venv + pip install

## Bước 4 — Cấu hình `.env`

```bash
cp .env.example .env
# Edit .env nếu muốn dùng cloud API
```

## Bước 5 — Khởi động services

**Terminal 1**:
```bash
ollama serve
```

**Terminal 2**:
```bash
cd ~/dev/video/infra/comfy/ComfyUI
source venv/bin/activate
python main.py --listen
```

**Terminal 3**:
```bash
cd ~/dev/video
source venv/bin/activate
litellm --config infra/litellm.yaml --port 4000
```

## Bước 6 — Smoke test

```bash
cd ~/dev/video
source venv/bin/activate
python orchestrator/pipeline.py \
    --intent "Test clip 5s: blue sky time-lapse" \
    --feature-id SMOKE-001 \
    --aspect 16:9 \
    --duration 5 \
    --brand brand-example.json
```

## Troubleshooting

| Lỗi | Cách khắc phục |
|---|---|
| `Permission denied: setup.sh` | `chmod +x infra/setup.sh` |
| `brew: command not found` | Cài Homebrew |
| MPS out of memory | Giảm batch size hoặc resolution trong ComfyUI workflow |
| `ollama: connection refused` | Terminal 1 chưa chạy |
| Render rất chậm (CPU mode) | Verify MPS: `python -c "import torch; print(torch.backends.mps.is_available())"` |

## Đặc thù M-series

- ComfyUI dùng MPS backend (không CUDA)
- Flux Q4 trên M3 Max 64GB: ~25s/image
- LTX-Video 5s 768²: ~3 phút
- Wan2.1-14B: cần 64GB+ RAM (không 32GB)
