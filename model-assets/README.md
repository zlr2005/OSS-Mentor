# Gated runtime asset

`portable_gated_pr40_9bf9208d.zip` is stored with Git LFS rather than as a normal Git blob.
After cloning, install Git LFS and run:

```powershell
git lfs pull
python -m pip install --extra-index-url https://download.pytorch.org/whl/cpu `
  -e ".[gated-worker]"
python scripts/rebuild_gated_runtime_assets.py `
  --archive model-assets/portable_gated_pr40_9bf9208d.zip `
  --output data/gated-v1
```

The installer verifies the archive and every internal file against
`config/gated_runtime_assets.lock.json` before publishing the local runtime directory.
The archive SHA-256 is
`179d6fe270522f194e7242ddd837a1850637c3c2f581a0e35300069fbcfcff0f`.

Do not place credentials, user data, feature caches, or runtime logs in this directory.
