#!/usr/bin/env bash
# Evaluate which Docker image variants need to be built.
#
# Called by the `check` job of .github/workflows/docker-publish.yml. All
# inputs are passed as environment variables (see the workflow step); the
# resulting build matrix is written to $GITHUB_OUTPUT as `matrix` and
# `has_builds` (falls back to stdout when GITHUB_OUTPUT is unset, which
# makes the script easy to test locally).
#
# Outputs:
#   matrix     - JSON object {"include": [...]} for the build job matrix
#   has_builds - "true" when at least one variant should be built
set -euo pipefail

# --- Inputs (environment) ----------------------------------------------------
EVENT="${EVENT:?EVENT is required}"                 # github.event_name
SHA="${SHA:?SHA is required}"                       # github.sha
GIT_REF="${GIT_REF:-}"                              # github.ref
GIT_REF_TYPE="${GIT_REF_TYPE:-}"                    # github.ref_type
PR_BASE_BRANCH="${PR_BASE_BRANCH:-}"                # github.base_ref
PR_NUMBER="${PR_NUMBER:-}"                          # pull request number
FORCE_BUILD="${FORCE_BUILD:-false}"                 # workflow_dispatch input
PLUGIN_REF_INPUT="${PLUGIN_REF_INPUT:-main}"        # workflow_dispatch input
REGISTRY="${REGISTRY:?REGISTRY is required}"
GITHUB_REPOSITORY="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
BASE_IMAGE_LATEST="${BASE_IMAGE_LATEST:?BASE_IMAGE_LATEST is required}"
BASE_IMAGE_BETA="${BASE_IMAGE_BETA:?BASE_IMAGE_BETA is required}"
BASE_IMAGE_DEV="${BASE_IMAGE_DEV:?BASE_IMAGE_DEV is required}"
ANNOTATION_BASE_DIGEST="${ANNOTATION_BASE_DIGEST:?ANNOTATION_BASE_DIGEST is required}"
ANNOTATION_REVISION="${ANNOTATION_REVISION:?ANNOTATION_REVISION is required}"

# Companion tag suffix for rollback: <tag>-YYYYMMDD-<shortsha>
COMPANION_SUFFIX="$(date -u +%Y%m%d)-${SHA::7}"

# --- Helpers -----------------------------------------------------------------

# Hash the raw multi-arch index so the digest is stable across
# architectures and independent of the local platform.
get_digest() {
  local raw
  raw="$(docker buildx imagetools inspect --raw "$1")"
  if [ -z "$raw" ]; then
    echo "ERROR: empty inspect result for '$1'" >&2
    exit 1
  fi
  printf '%s' "$raw" | sha256sum | cut -d' ' -f1
}

# Read the base-digest / revision annotations from a published multi-arch
# image. Prints "<digest> <revision>" (either may be empty when the image
# or the annotation does not exist yet).
get_annotations() {
  local image="$1" raw digest revision
  if raw="$(docker buildx imagetools inspect --raw "$image" 2>/dev/null)"; then
    digest="$(printf '%s' "$raw" | jq -r --arg k "$ANNOTATION_BASE_DIGEST" \
      '.annotations[$k] // empty, (.manifests[]? | .annotations[$k] // empty)' 2>/dev/null | head -n1 || true)"
    revision="$(printf '%s' "$raw" | jq -r --arg k "$ANNOTATION_REVISION" \
      '.annotations[$k] // empty, (.manifests[]? | .annotations[$k] // empty)' 2>/dev/null | head -n1 || true)"
  fi
  printf '%s %s' "${digest:-}" "${revision:-}"
}

# Compare a variant against its published image: build when either the base
# digest or the stamped revision differs (or nothing was published yet).
# This also catches builds missed because a previous push build failed.
needs_build() {
  local base_digest="$1" pub="$2"
  local pub_digest="${pub%% *}" pub_rev="${pub#* }"
  [ -z "$pub_digest" ] || [ "$pub_digest" != "$base_digest" ] \
    || [ -z "$pub_rev" ] || [ "$pub_rev" != "$SHA" ]
}

# Emit a key/value pair to $GITHUB_OUTPUT (or stdout when unset).
emit() {
  local key="$1" value="$2"
  if [ -n "${GITHUB_OUTPUT:-}" ]; then
    printf '%s=%s\n' "$key" "$value" >> "$GITHUB_OUTPUT"
  else
    printf '%s=%s\n' "$key" "$value"
  fi
}

# --- Ref guard ---------------------------------------------------------------
# Manual runs are only allowed on main.
if [ "$EVENT" = "workflow_dispatch" ] && [ "$GIT_REF" != "refs/heads/main" ]; then
  echo "::warning::workflow_dispatch is restricted to the main branch (got '$GIT_REF'); skipping."
  emit "has_builds" "false"
  emit "matrix" '{"include":[]}'
  exit 0
fi

# --- Release build (tag-push) ------------------------------------------------
# Tags: the version (git tag without the `v` prefix) plus the moving
# `latest` tag. No digest-skip check applies.
if [ "$EVENT" = "push" ] && [ "$GIT_REF_TYPE" = "tag" ]; then
  TAG_NAME="${GIT_REF#refs/tags/}"
  VERSION="${TAG_NAME#v}"
  TAGS="$(printf '%s\nlatest' "$VERSION")"
  MATRIX="$(jq -nc --arg tags "$TAGS" '{
    include: [{
      variant: "release",
      ma_version: "latest",
      tags: $tags,
      plugin_ref: "",
      base_digest: "",
      push: "true"
    }]
  }')"
  emit "matrix" "$MATRIX"
  emit "has_builds" "true"
  echo "Release build for tag $TAG_NAME scheduled (tags: $VERSION, latest)."
  exit 0
fi

# --- PR build ----------------------------------------------------------------
# Single leg, base image depends on the target branch (main -> latest,
# beta -> beta, dev -> nightly). Build only, no push.
if [ "$EVENT" = "pull_request" ]; then
  case "$PR_BASE_BRANCH" in
    beta) base_image="$BASE_IMAGE_BETA"; ma_version="beta" ;;
    dev)  base_image="$BASE_IMAGE_DEV";  ma_version="nightly" ;;
    *)    base_image="$BASE_IMAGE_LATEST"; ma_version="latest" ;;
  esac
  base_digest="$(get_digest "$base_image")"
  MATRIX="$(jq -nc \
    --arg variant "pr-$PR_NUMBER" \
    --arg ma_version "$ma_version" \
    --arg base_digest "$base_digest" '{
    include: [{
      variant: $variant,
      ma_version: $ma_version,
      tags: $variant,
      plugin_ref: "",
      base_digest: $base_digest,
      push: "false"
    }]
  }')"
  emit "matrix" "$MATRIX"
  emit "has_builds" "true"
  echo "PR build (target branch: $PR_BASE_BRANCH, base: $base_image) scheduled."
  exit 0
fi

# --- Scheduled / push / manual runs ------------------------------------------
# Evaluate all three automatic variants. These never publish the `latest`
# tag - that tag is reserved for official (tested) tag-push releases.
declare -A BASE_IMAGE=(
  [edge]="$BASE_IMAGE_LATEST"
  [beta]="$BASE_IMAGE_BETA"
  [nightly]="$BASE_IMAGE_DEV"
)
declare -A MA_VERSION=( [edge]=latest [beta]=beta [nightly]=nightly )
declare -A PLUGIN_REF=(
  [edge]=""
  [beta]="$PLUGIN_REF_INPUT"
  [nightly]="$PLUGIN_REF_INPUT"
)

legs="[]"
for variant in edge beta nightly; do
  base_digest="$(get_digest "${BASE_IMAGE[$variant]}")"
  published_image="$REGISTRY/${GITHUB_REPOSITORY,,}:${variant}"
  pub="$(get_annotations "$published_image")"
  pub_digest="${pub%% *}"
  pub_rev="${pub#* }"

  build=false
  case "$EVENT" in
    push)
      # Always build on code changes to main.
      build=true
      ;;
    schedule|workflow_dispatch)
      if [ "$FORCE_BUILD" = "true" ] || needs_build "$base_digest" "$pub"; then
        build=true
      fi
      ;;
  esac

  echo "variant=$variant base_digest=$base_digest published=($pub_digest, ${pub_rev:-<none>}) should_build=$build"

  if [ "$build" = "true" ]; then
    legs="$(printf '%s' "$legs" | jq -c \
      --arg variant "$variant" \
      --arg ma_version "${MA_VERSION[$variant]}" \
      --arg tags "$(printf '%s\n%s-%s' "$variant" "$variant" "$COMPANION_SUFFIX")" \
      --arg plugin_ref "${PLUGIN_REF[$variant]}" \
      --arg base_digest "$base_digest" \
      '. + [{
        variant: $variant,
        ma_version: $ma_version,
        tags: $tags,
        plugin_ref: $plugin_ref,
        base_digest: $base_digest,
        push: "true"
      }]')"
  fi
done

MATRIX="$(jq -nc --argjson include "$legs" '{include: $include}')"
emit "matrix" "$MATRIX"
if [ "$legs" = "[]" ]; then
  emit "has_builds" "false"
  echo "All variants are up to date - nothing to build."
else
  emit "has_builds" "true"
fi
