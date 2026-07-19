#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/build_offline_image.sh --tag IMAGE_TAG --output IMAGE_TAR

Build the AutoAD production image for linux/amd64, verify its image metadata,
then export it with docker save. The output tar and a sibling .sha256 file are
the image portion of the offline deployment package.

Required:
  --tag IMAGE_TAG       Exact Docker image tag to embed in deployment commands.
  --output IMAGE_TAR    New tar file to create. Existing files are refused.
EOF
}

IMAGE_TAG=""
OUTPUT_PATH=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --tag)
      IMAGE_TAG="${2:-}"
      shift 2
      ;;
    --output)
      OUTPUT_PATH="${2:-}"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ -z "$IMAGE_TAG" ] || [ -z "$OUTPUT_PATH" ]; then
  echo "Both --tag and --output are required." >&2
  usage >&2
  exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker Engine CLI is required to build the offline image." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker Engine is not reachable by the current user." >&2
  exit 1
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="$(dirname "$OUTPUT_PATH")"

if [ ! -d "$OUTPUT_DIR" ]; then
  echo "Output directory does not exist: $OUTPUT_DIR" >&2
  exit 2
fi

if [ -e "$OUTPUT_PATH" ] || [ -e "${OUTPUT_PATH}.sha256" ]; then
  echo "Refusing to overwrite an existing package artifact: $OUTPUT_PATH" >&2
  exit 2
fi

docker build \
  --platform linux/amd64 \
  --file "$PROJECT_ROOT/docker/Dockerfile" \
  --tag "$IMAGE_TAG" \
  "$PROJECT_ROOT"

IMAGE_PLATFORM="$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$IMAGE_TAG")"
if [ "$IMAGE_PLATFORM" != "linux/amd64" ]; then
  echo "Built image platform is $IMAGE_PLATFORM; expected linux/amd64." >&2
  exit 1
fi

docker save --output "$OUTPUT_PATH" "$IMAGE_TAG"
sha256sum "$OUTPUT_PATH" > "${OUTPUT_PATH}.sha256"

echo "Offline image created: $OUTPUT_PATH"
echo "SHA-256 manifest created: ${OUTPUT_PATH}.sha256"
