# Trademark / Logo Similarity Index

## Purpose

This directory holds reference images of logos and brand marks that the
pipeline uses for trademark similarity screening.

When a video is produced, the moderation layer samples frames and computes
CLIP cosine similarity against every image in this directory.  If any frame
exceeds the similarity threshold (default 0.85), the check flags the video
as `severity=major` with a recommendation to verify legal clearance before
publishing.

## How to add a trademark image

1. Drop a PNG or JPG of the logo into this directory:

   ```
   data/trademark_index/my-brand-logo.png
   ```

2. On the **next pipeline run**, the embeddings are rebuilt automatically
   and cached to `trademark_embeddings.npz`.

   To force a rebuild now (e.g. after adding many images at once):
   ```bash
   rm -f data/trademark_index/trademark_embeddings.npz
   python -c "
   from orchestrator.lib.moderation import check_trademark_similarity
   check_trademark_similarity([], rebuild=True)
   "
   ```

3. Check logs for the event kind=`moderation` check=`trademark` to confirm
   the embedding count matches expectations.

## How to opt out entirely

Keep this directory empty (or containing only this README).  The check is
skipped when no `.png` / `.jpg` files are present — no false positives, no
CLIP model loaded.

## Threshold tuning

The default cosine-similarity threshold is **0.85**.  Lower values increase
sensitivity (more flags, possible false positives); higher values decrease
sensitivity (may miss near-identical reproductions).

Override per-run in pipeline.py or via the `threshold` argument to
`check_trademark_similarity()`.

## Privacy note

Trademark images you place here are **local only**.  They are never sent
to any remote server.  CLIP inference runs entirely on local hardware.
