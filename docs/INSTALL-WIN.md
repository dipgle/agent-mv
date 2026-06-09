# Cài đặt — Windows 10 / 11

## Yêu cầu hệ thống

| Thành phần | Mức tối thiểu | Khuyến nghị |
|---|---|---|
| OS | Windows 10 22H2 hoặc Windows 11 | Windows 11 23H2+ |
| CPU | x64 4 cores | 8 cores+ |
| RAM | 16 GB | 32-64 GB |
| GPU | NVIDIA 12 GB VRAM (RTX 3060) | RTX 4090 24 GB / RTX 5090 |
| Storage | 200 GB SSD free | 500 GB NVMe |
| Driver | NVIDIA Game Ready 552+ với CUDA 12.4 | latest |
| Network | 50 Mbps (first download ~60 GB) | 200 Mbps+ |

> **AMD/Intel GPU**: chưa hỗ trợ chính thức. ROCm/Vulkan workaround có thể chạy Flux nhưng LTX/Wan không đảm bảo.
> **CPU-only**: chạy được nhưng cực chậm (5-30 phút cho 1 ảnh, 1-3 giờ cho 1 clip 5s).

## Bước 1 — Verify GPU

Mở PowerShell:
```powershell
nvidia-smi
```
Phải hiện driver + CUDA version. Nếu không → cài driver mới từ [nvidia.com/drivers](https://www.nvidia.com/drivers).

## Bước 2 — Cài winget (chỉ Windows 10)

Win 11 đã có sẵn. Win 10: cài "App Installer" từ Microsoft Store.

Verify:
```powershell
winget --version
```

## Bước 3 — Clone hoặc extract folder dự án

Nếu nhận qua zip: extract tới `C:\dev\video\` (hoặc bất kỳ path không có dấu cách).

Nếu clone git:
```powershell
mkdir C:\dev
cd C:\dev
git clone <repo-url> video
cd video
```

## Bước 4 — Chạy setup script

PowerShell (chế độ user thường, KHÔNG cần admin):
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\run.ps1 setup
```

(Tương đương `.\infra\setup.ps1` — `run.ps1` là wrapper tiện lợi để chạy mọi
lệnh pipeline mà không cần activate venv thủ công.)

Script sẽ tự động:
1. Cài Python 3.11, Git, ffmpeg, sqlite, Ollama qua winget
2. Pull text models qua Ollama (~35 GB)
3. Clone ComfyUI + cài PyTorch CUDA + dependencies
4. Download model weights từ HuggingFace (~42 GB):
   - FLUX.1-dev (24 GB)
   - LTX-Video (8 GB)
   - F5-TTS (2 GB)
   - Stable Audio Open (4 GB)
   - Whisper large-v3 (3 GB)
5. Tạo Python venv + pip install requirements
6. Copy `.env.example` → `.env`

**Thời gian dự kiến**: 1-3 giờ tùy bandwidth + GPU. Có thể chạy lại (idempotent) nếu interrupt.

## Bước 5 — Cấu hình `.env` (tùy chọn)

Mở `.env` bằng Notepad, điền các API key NẾU muốn dùng cloud escalation:

```
GROQ_API_KEY=gsk_...          # free, https://console.groq.com/keys
ANTHROPIC_API_KEY=sk-ant-...  # paid (chỉ khi escalate Sonnet/Opus)
```

Không bắt buộc. Pipeline chạy 100% local nếu để trống.

## Bước 6 — Khởi động services

Mở **3 PowerShell windows** riêng:

**Window 1 — Ollama**:
```powershell
ollama serve
```

**Window 2 — ComfyUI**:
```powershell
cd C:\dev\video\infra\comfy\ComfyUI
.\venv\Scripts\Activate.ps1
python main.py --listen
```
Sau ~30s, mở [http://localhost:8188](http://localhost:8188) verify ComfyUI lên.

**Window 3 — LiteLLM proxy**:
```powershell
cd C:\dev\video
.\venv\Scripts\Activate.ps1
litellm --config infra\litellm.yaml --port 4000
```

## Bước 7 — Verify environment

Trước khi chạy pipeline, verify mọi thứ OK:
```powershell
.\run.ps1 check --verbose
```

Mong đợi: tất cả `[ OK ]`, riêng 3 endpoint Ollama/ComfyUI/LiteLLM có thể
`[WARN]` nếu services chưa start (bước 6 chạy nó).

## Bước 8 — Smoke test (render 5s clip)

Window 4:
```powershell
.\run.ps1 pipeline `
    --intent "Test clip 5s: blue sky time-lapse" `
    --feature-id SMOKE-001 `
    --aspect 16:9 `
    --duration 5 `
    --brand brand-example.json
```

Kết quả: `out\SMOKE-001\final.mp4`. Nếu mở play được = setup thành công.

## Bước 9 — Lên lịch Supervisor cron (Windows Task Scheduler)

Supervisor cần chạy daily + weekly tự động. Trên Windows không có cron,
dùng Task Scheduler.

### Daily task (audit + cost rollup + auto-promote)

PowerShell admin:
```powershell
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File C:\dev\video\orchestrator\cron\daily.ps1" `
    -WorkingDirectory "C:\dev\video"
$trigger = New-ScheduledTaskTrigger -Daily -At "02:00"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable
Register-ScheduledTask -TaskName "AgentMV-Daily" `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description "Supervisor daily: audit + cost rollup + auto-promote"
```

### Weekly task (scan + propose)

```powershell
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File C:\dev\video\orchestrator\cron\weekly.ps1" `
    -WorkingDirectory "C:\dev\video"
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At "09:00"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable
Register-ScheduledTask -TaskName "AgentMV-Weekly" `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description "Supervisor weekly: external scan + propose improvements"
```

Verify tasks đã đăng ký:
```powershell
Get-ScheduledTask -TaskName "AgentMV-*"
```

Chạy thử ngay:
```powershell
Start-ScheduledTask -TaskName "AgentMV-Daily"
# Sau ~1 phút, check output:
Get-Content logs\cron-daily.log -Tail 20
Get-ChildItem eval\reports\
```

## Troubleshooting

| Lỗi | Cách khắc phục |
|---|---|
| `nvidia-smi: not found` | Cài NVIDIA driver |
| `CUDA out of memory` | Giảm resolution proxy hoặc dùng Q4 quant của Flux |
| `winget: not recognized` | Win 10: cài App Installer từ MS Store |
| `huggingface-cli: not found` | Activate venv ComfyUI trước khi chạy |
| `ollama: connection refused` | Window 1 chưa chạy hoặc port 11434 đang dùng |
| `ComfyUI: workflow not found` | Import workflow JSON từ `workflows/` qua UI ComfyUI |
| `is not digitally signed` | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` mỗi PowerShell window mới |
| `Activate.ps1: cannot be loaded` | Như trên — execution policy |
| `python: command not found` | Restart PowerShell sau khi `winget install Python` xong |
| Tiếng Việt / emoji hiện `???` | `run.ps1` đã set `chcp 65001` + `PYTHONIOENCODING=utf-8`; nếu gọi script trực tiếp thì set `$env:PYTHONIOENCODING="utf-8"` trước |
| Bash script không chạy (CRLF) | `.gitattributes` ép LF cho `*.sh`; nếu vẫn lỗi: `dos2unix orchestrator/cron/*.sh` |
| `pwsh: command not found` (khi Python gọi compose) | Pipeline tự fallback sang `powershell.exe` 5.1; chỉ cảnh báo nhẹ |
| `subprocess.CalledProcessError ffmpeg` | Verify `where ffmpeg` ra path; nếu không, restart shell sau winget install |
| `OOM khi pip install opencv-python` | Tăng pagefile lên 16GB hoặc cài `opencv-python-headless` thay |
| Render rất chậm (>30 min/5s) | Đang dùng CPU mode, check `.\run.ps1 check --verbose` |
| Antivirus block `pyinstaller.exe` | Whitelist folder dự án trong Defender |
| Task Scheduler không chạy | Settings → "Run whether user is logged on or not" + check user có quyền "Log on as batch job" |

## Cập nhật

```powershell
cd C:\dev\video
git pull               # nếu clone từ git
.\infra\setup.ps1      # re-run idempotent, sẽ update components
```

## Uninstall

```powershell
# Stop services (close 3 PowerShell windows)
# Delete folder
Remove-Item -Recurse -Force C:\dev\video
# Uninstall Ollama qua Settings → Apps
# (Optional) Uninstall Python, Git, ffmpeg qua Settings → Apps
```

Models đã download nằm trong `infra\comfy\ComfyUI\models\` — xóa folder là dọn sạch ~60 GB.
