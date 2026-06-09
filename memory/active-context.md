# Active Context — agent-mv

Last session ended: 2026-06-09

## Repo
- GitHub: https://github.com/dipgle/agent-mv
- Local: /Users/hanguyen/Documents/projects/projects/video/
- Branch: main, clean working tree
- HEAD: e166315 (smoke verification after batch-2)

## State summary

Self-improving multi-agent video pipeline. Core feedback loop closed end-to-end:

```
Pipeline render -> 4-tier Reviewer -> final.mp4
                                       |
                            publish_record -> Tier 4 outcome ingest
                                                       |
                              calibrate panel + hook weights from outcome
                                                       |
                          champion/anti-pattern library evolution
                                                       |
                       supervisor scan + propose + canary + auto-promote
                                                       |
                                  config_mutator atomic apply / rollback
```

## What's shipped (production-ready scaffolding)

1. **4-role pipeline** (Researcher / Planner / Executor / Reviewer) + Supervisor (5th)
2. **6 modality executor** through ComfyUI: keyframe / motion / voice / music / caption / compose
3. **Cost foundation**: cloud + compute + electricity tracked per call; per-video/day/month caps with cascade fallback
4. **Cost gate + Codex pool**: legit free Tier A- (Groq, Cerebras, Codestral, OpenRouter); Tier S Codex pool restricted to Adjudicator/Architect via `assert_codex_quota_role`
5. **0$ commercial stack**: Flux.1-schnell + Wan2.1-T2V-14B + Pixabay music + F5-TTS + Whisper (all Apache/MIT/CC0)
6. **4-tier Reviewer**: Tier 1 deterministic (ffprobe + LUFS + freeze + scene + palette + brand) -> Tier 2 LLM panel ensemble (4 models, trimmed-mean, sigma>0.3 escalates) -> Tier 3 frontier adjudicator -> aggregate
7. **Tier 0 moderation gate**: NSFW + real-person face + trademark similarity + voice clone consent (graceful degradation when nudenet/open_clip not installed)
8. **C2PA AI-disclosure** manifest embedded post-ffmpeg
9. **LLM panel hardening**: per-model timeout, circuit breaker (3 failures / 30 calls -> 5min cooldown), retry-once on soft errors, partial-panel fallback (0/1/2+ branches)
10. **Pipeline resumability**: `.checkpoint.json` per feature_dir, atomic step tracking, `--force-redo` / `--from-step` / `--show-checkpoint` / `--max-crashes` CLI flags
11. **Auto_promote real config mutation**: snapshot -> mutate litellm.yaml or swap workflow JSON -> verify -> rollback on failure; canary stale (>2x duration with no metrics) auto-rollback; `--rollback <PROPOSAL_ID>` CLI
12. **Tier 4 outcome ingest**: YouTube Analytics + TikTok Business + Meta Marketing + ManualClient
13. **Calibration loop**: `calibrate_panel.py` ridge regression of panel scores -> outcome watch-through; `calibrate_hook.py` same for 6 hook signals; falls back to equal weights if n<threshold or R2<0.10
14. **Champion evolution**: top 20% promoted, bottom 20% anti-pattern, capped 5/category
15. **Per-shot re-render**: Reviewer reject only re-renders shots with critical/major issues
16. **Supervisor scan sources** (10 total): HF trending + arxiv + LiteLLM pricing + ComfyUI Manager + Civitai + Replicate + GitHub trending ML + Reddit r/SD + (4 originals)
17. **Regression baseline**: `regression_check.py snapshot/check/list-baselines` with 7-day overwrite protection
18. **eval/serve.py production-ready**: Bearer auth + token-bucket rate limit (100 authed / 10 unauth per min) + CORS + rotating access log + `/health`
19. **Cost forecast + webhook alerts**: 75/90/100% MTD with 24h dedup; Slack/Discord/generic POST
20. **Web-chat-router MCP Phase 1**: 3 zero-ToS adapters (Perplexity, LMArena, HuggingChat) with redaction guard
21. **CI/CD**: GitHub Actions (syntax + shell + schema + smoke) + release.yml + dependabot
22. **5 docs**: runbook, cost-tuning, eval-tuning, c2pa, migration-flux-dev-to-schnell
23. **Cross-OS**: bash + PowerShell scripts, `run.sh` + `run.ps1` wrappers, `scripts/check_env.py`, UTF-8 console fix, `.gitattributes` line endings

## What's NOT shipped (P3+, future)

- Real ComfyUI workflow JSON exports (still `.stub` — user task once they install ComfyUI)
- GUI launcher (Tkinter/PySide)
- Inno Setup Windows installer
- Web-chat-router Phase 2 (login-required adapters Claude/GPT/Gemini)
- Web-chat-router Phase 3 (throwaway account farm with proxy + captcha solver)
- Batched keyframe rendering (currently sequential per shot)
- Native Researcher VL scrape (currently manual `reference.json` fill)
- Frontier Adjudicator panel vote (Tier 3 still calls single Opus, not ensemble)

## Stats

- 33 Python files (all ast.parse clean)
- 35 SQLite VIEWs
- 8 supervisor scripts (audit / cost_rollup / scan / propose / auto_promote / fetch_outcomes / calibrate_panel / calibrate_hook / evolve_champions / regression_check)
- 6 dashboard tabs (Cost / Proposals / Audit / Quality / Outcomes / Champions / Calibration / Compliance)
- 13 commits today (3d239ff -> e166315)

## Resume from this state

```bash
cd ~/Documents/projects/projects/video
./run.sh check --verbose     # verify env
./run.sh dashboard           # boot HTTP server :7891
./run.sh cron-daily          # smoke supervisor pipeline
```

For full e2e video render: install ComfyUI + export 5 workflow JSONs from Manager + set ComfyUI/Ollama/LiteLLM services running, then `./run.sh pipeline --intent "..." --feature-id X`.
