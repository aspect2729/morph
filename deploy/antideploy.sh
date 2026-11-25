#!/usr/bin/env bash
# Deploy Morph to Antideploy (https://morphpdf.antideploy.app) and wait for the result.
#   deploy/antideploy.sh
# Uses the account token in ~/.antideploy/config.json (never printed) and the app id in .antideploy.json.
set -euo pipefail
cd "$(dirname "$0")/.."

TOKEN=$(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.antideploy/config.json')))['token'])")
APP=$(python3 -c "import json;print(json.load(open('.antideploy.json'))['applicationId'])")
API=https://antideploy.com/api/v1

# Server settings for Antideploy's 1 GB instance (no swap): one conversion at a time, smaller caps.
# MORPH_OCR_LANG / MORPH_FONT_DIR are optional (these are their defaults) but set explicitly so the
# Antideploy console doesn't list them as missing.
ENV='{"MORPH_MODE":"web","MORPH_DATA":"/data","MORPH_WORKERS":"1","MORPH_JOBS_PER_WORKSPACE":"2",
"MORPH_MAX_UPLOAD_MB":"50","MORPH_MAX_PAGES":"150","MORPH_RATE_PER_MIN":"30",
"MORPH_OCR_LANG":"eng","MORPH_FONT_DIR":"/usr/share/fonts/truetype/liberation"}'

# Only what the Dockerfile builds from (keeps the upload far below Antideploy's 28 MB limit).
ARCHIVE=$(mktemp -t morph-deploy).tgz
COPYFILE_DISABLE=1 tar czf "$ARCHIVE" --exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store' \
  Dockerfile .dockerignore requirements.lock app static scripts
echo "Uploading $(du -h "$ARCHIVE" | cut -f1) ..."

RESP=$(curl -sS -X POST "$API/deploy?applicationId=$APP" -H "Authorization: Bearer $TOKEN" \
  -F "archive=@$ARCHIVE" -F "env=$ENV" ${FORCE:+-F force=true})
rm -f "$ARCHIVE"
TASK=$(printf '%s' "$RESP" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('taskId') or '')")
if [ -z "$TASK" ]; then echo "Deploy not queued: $RESP"; exit 1; fi
echo "Queued task $TASK — building (first build installs LibreOffice etc., ~10-20 min)"

LAST=""
while true; do
  sleep 15
  S=$(curl -sS -H "Authorization: Bearer $TOKEN" "$API/deployments/$TASK")
  ST=$(printf '%s' "$S" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('status',''),'|',d.get('step') or d.get('currentStep') or '')")
  [ "$ST" != "$LAST" ] && echo "  $(date +%H:%M:%S) $ST" && LAST="$ST"
  case "$ST" in succeeded*|failed*) break;; esac
done
printf '%s' "$S" > .antideploy-last.json
printf '%s' "$S" | python3 -c "
import sys,json;d=json.load(sys.stdin)
for k in ('summary','error','failedStep','security','warnings','hazards','agentNote'):
    if d.get(k): print(k+':', json.dumps(d[k])[:1500])
"
case "$ST" in succeeded*) exit 0;; *) exit 1;; esac
