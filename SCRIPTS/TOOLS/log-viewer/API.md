# Log Viewer API

## Purpose
Opens separate terminal windows showing `tail -f` logs of any script managed by YuniScripts.

## Commands (via Unix socket `/tmp/yuniScripts-logviewer.sock`)
- `watch <script_id>`   – open a log window for the script
- `unwatch <script_id>` – close the log window for the script
- `list`                – list currently watched scripts and their log window PIDs
- `stop` / `exit`       – close all windows and shut down the log viewer