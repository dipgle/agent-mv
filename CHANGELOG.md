# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

(No unreleased changes yet. All recent work is tagged.)

## [0.4.0] — 2026-06-09

### Added
- Tier 4 outcome loop with multi-platform ingestion (YouTube, TikTok, Meta)
- Calibration framework for continuous improvement via A/B test champions
- Champion evolution algorithm with statistical significance testing
- Devlog schema extensions for outcome tracking and canary promotion

### Changed
- Supervisor workflow now includes outcome feedback integration

## [0.3.0] — 2026-06-07

### Added
- 4-tier reviewer ensemble (combining text + vision + reasoning across local + cloud models)
- Dashboard HTTP API with `/eval/metrics` endpoints for serving cost/audit/proposal data
- Support for structured critique JSON with shot-level severity classification

### Fixed
- Reviewer model selection now properly cross-validates family membership (avoid same-family echo)

## [0.2.0] — 2026-06-02

### Added
- Windows build hardening with `run.ps1` wrapper for cross-platform orchestration
- Environment check script with verbose diagnostics
- PowerShell equivalents for all cron jobs (`daily.ps1`, `weekly.ps1`)
- Unicode console support (UTF-8 enforcement on Windows)
- Add Supervisor (role 5) autonomous agent with daily + weekly cadence
- Cost tracking foundation: per-call cost estimation (cloud + compute + electricity)
- Cost gate with cascade fallback: enforces per-video, per-day, per-month caps
- Devlog cost schema tracking: `cloud_usd`, `compute_usd`, `electricity_usd` per model run

### Changed
- Pipeline now logs all model calls with cost metadata to devlog

## [0.1.0] — 2026-05-28

### Added
- Initial project skeleton: 4-role multi-agent local-first video pipeline
- Researcher → Planner → Executor (6 modality) → Reviewer → Adjudicator workflow
- ComfyUI integration with Flux.1-dev, LTX-Video, F5-TTS, Stable Audio, Whisper
- Ollama local text models (Qwen3, DeepSeek-R1)
- LiteLLM proxy for cascade routing (local → free cloud → paid cloud)
- SQLite devlog schema with event tracking, use cases, test cases
- Cross-OS support: setup scripts for Windows, macOS, Linux
- ffmpeg compose pipeline for final video assembly
- Vanilla JS dashboard skeleton for cost + audit + proposal visualization
- Workflows in JSON.stub format (placeholder; requires ComfyUI export)

[Unreleased]: https://github.com/dipgle/agent-mv/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/dipgle/agent-mv/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/dipgle/agent-mv/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/dipgle/agent-mv/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/dipgle/agent-mv/releases/tag/v0.1.0
