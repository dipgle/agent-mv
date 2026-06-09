# Cài đặt — Linux (Ubuntu 22.04+ / Debian 12+)

## Yêu cầu hệ thống

| Thành phần | Mức tối thiểu | Khuyến nghị |
|---|---|---|
| Distro | Ubuntu 22.04 LTS | Ubuntu 24.04 |
| Kernel | 6.x | 6.8+ |
| RAM | 16 GB | 32-64 GB |
| GPU | NVIDIA 12 GB VRAM | RTX 4090/5090 |
| Storage | 200 GB SSD | 500 GB NVMe |
| Driver | NVIDIA 550+ với CUDA 12.4 | latest |

## Bước 1 — Cài NVIDIA driver + CUDA

Ubuntu:
```bash
sudo apt update
sudo apt install -y nvidia-driver-550 nvidia-cuda-toolkit
sudo reboot
```

Verify:
```bash
nvidia-smi
nvcc --version
```

## Bước 2 — System deps

```bash
sudo apt install -y python3.11 python3.11-venv python3-pip git ffmpeg sqlite3 curl
```

## Bước 3 — Cài Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

## Bước 4 — Clone hoặc extract folder

```bash
mkdir -p ~/dev
cd ~/dev
git clone <repo-url> video
cd video
```

## Bước 5 — Chạy setup

```bash
chmod +x infra/setup.sh
./infra/setup.sh
```

Script sẽ:
1. Pull Ollama text models
2. Clone ComfyUI + cài PyTorch CUDA + dependencies
3. Download model weights từ HuggingFace (~42 GB)
4. Setup Python venv + pip install requirements

## Bước 6 — Cấu hình `.env`

```bash
cp .env.example .env
nano .env       # điền API keys nếu cần cloud escalation
```

## Bước 7 — Khởi động services (3 terminals hoặc tmux)

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

## Bước 8 — Smoke test

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

## Optional — systemd services (chạy nền vĩnh viễn)

`/etc/systemd/system/video-pipeline-ollama.service`:
```ini
[Unit]
Description=Ollama for video pipeline
After=network.target

[Service]
ExecStart=/usr/local/bin/ollama serve
Restart=always
User=YOUR_USER

[Install]
WantedBy=multi-user.target
```

Tương tự cho `comfyui.service` và `litellm.service`. Sau đó:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now video-pipeline-ollama video-pipeline-comfyui video-pipeline-litellm
```

## Troubleshooting

| Lỗi | Cách khắc phục |
|---|---|
| `nvidia-smi: not found` | Cài nvidia-driver, reboot |
| `CUDA out of memory` | `nvidia-smi` xem process khác, kill nếu cần |
| `ollama: connection refused` | `systemctl status ollama` |
| Permission denied venv | Đảm bảo user owns folder, không chạy với `sudo` |
| `apt: package not found` | Update + check distro version |
