#!/usr/bin/env bash
# Entry point of the git-xray GitHub Action. See action.yml for inputs.
set -euo pipefail

log() { echo "git-xray: $*"; }

# 1. Full history is the whole point; deepen a shallow checkout if needed.
if [ "$(git rev-parse --is-shallow-repository)" = "true" ]; then
  echo "::notice title=git-xray::Shallow checkout detected, fetching full history." \
       "Set 'fetch-depth: 0' on actions/checkout to skip this step."
  git fetch --quiet --unshallow --no-tags origin || true
fi

# 2. Work out the base to compare with.
BASE="${INPUT_BASE:-}"
if [ -z "$BASE" ]; then
  if [ -n "${GITHUB_BASE_REF:-}" ]; then
    git fetch --quiet --no-tags origin \
      "+refs/heads/${GITHUB_BASE_REF}:refs/remotes/origin/${GITHUB_BASE_REF}" || true
    BASE="origin/${GITHUB_BASE_REF}"
  else
    echo "::error title=git-xray::Not a pull_request event. Set the 'base' input."
    exit 1
  fi
fi
log "comparing with $BASE"

# 3. Install into a private venv (system Python may be externally managed).
PY="$(command -v python3 || command -v python)"
VENV="${RUNNER_TEMP:-/tmp}/git-xray-venv"
"$PY" -m venv "$VENV"
if [ -x "$VENV/bin/python" ]; then VPY="$VENV/bin/python"; else VPY="$VENV/Scripts/python.exe"; fi
PKG="${INPUT_PACKAGE:-}"
if [ -z "$PKG" ]; then
  PKG="git-xray-tool${INPUT_VERSION:+==$INPUT_VERSION}"
fi
"$VPY" -m pip install --quiet --disable-pip-version-check "$PKG"

# 4. Run the check once as JSON and once as Markdown.
OUT="${RUNNER_TEMP:-/tmp}/git-xray"
mkdir -p "$OUT"
ARGS=(--diff "$BASE")
[ -n "${INPUT_SINCE:-}" ] && ARGS+=(--since "$INPUT_SINCE")
"$VPY" -m git_xray "${ARGS[@]}" --json > "$OUT/review.json"
"$VPY" -m git_xray "${ARGS[@]}" --md > "$OUT/review.md"
RISK="$("$VPY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["risk"])' "$OUT/review.json")"
log "risk is $RISK"
echo "risk=$RISK" >> "$GITHUB_OUTPUT"
cat "$OUT/review.md" >> "$GITHUB_STEP_SUMMARY"

# 5. Post or update a single sticky PR comment.
MARKER="<!-- git-xray:pr-review -->"
PR="$("$VPY" -c 'import json,os; e=json.load(open(os.environ["GITHUB_EVENT_PATH"])); print((e.get("pull_request") or {}).get("number") or "")')"
MODE="${INPUT_COMMENT:-always}"
if [ -n "$PR" ] && [ "$MODE" != "never" ]; then
  EXISTING="$(gh api "repos/$GITHUB_REPOSITORY/issues/$PR/comments" --paginate \
      --jq ".[] | select(.body | startswith(\"$MARKER\")) | .id" 2>/dev/null | head -n1 || true)"
  if [ -n "$EXISTING" ]; then
    gh api -X PATCH "repos/$GITHUB_REPOSITORY/issues/comments/$EXISTING" \
      -F "body=@$OUT/review.md" > /dev/null \
      && log "updated PR comment" \
      || echo "::warning title=git-xray::Could not update the PR comment (missing 'pull-requests: write'?)."
  elif [ "$MODE" = "always" ] || [ "$RISK" != "low" ]; then
    gh api -X POST "repos/$GITHUB_REPOSITORY/issues/$PR/comments" \
      -F "body=@$OUT/review.md" > /dev/null \
      && log "posted PR comment" \
      || echo "::warning title=git-xray::Could not post a PR comment. Forked PRs get a read-only token; the report is in the job summary."
  else
    log "risk is low, no comment posted (comment: risky)"
  fi
fi

# 6. Optional gate.
case "${INPUT_FAIL_ON:-never}:$RISK" in
  high:high|medium:medium|medium:high)
    echo "::error title=git-xray::PR risk is $RISK (fail-on: $INPUT_FAIL_ON)"
    exit 1 ;;
esac
