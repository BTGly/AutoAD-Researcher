#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/package_offline_deployment.sh --tag IMAGE_TAG --output-dir DIRECTORY

Create a self-contained Linux/amd64 offline deployment directory containing
the docker-save image tar, SHA-256 manifest, deployment Compose file, and
deployment guide. The output directory must not already exist.
EOF
}

IMAGE_TAG=""
OUTPUT_DIR=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --tag)
      IMAGE_TAG="${2:-}"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="${2:-}"
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

if [ -z "$IMAGE_TAG" ] || [ -z "$OUTPUT_DIR" ]; then
  echo "Both --tag and --output-dir are required." >&2
  usage >&2
  exit 2
fi

if [ -e "$OUTPUT_DIR" ]; then
  echo "Refusing to overwrite an existing deployment directory: $OUTPUT_DIR" >&2
  exit 2
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$OUTPUT_DIR"

IMAGE_TAR="$OUTPUT_DIR/autoad-researcher-linux-amd64.tar"
"$PROJECT_ROOT/scripts/build_offline_image.sh" --tag "$IMAGE_TAG" --output "$IMAGE_TAR"
cp "$PROJECT_ROOT/docker/docker-compose.offline.yml" "$OUTPUT_DIR/docker-compose.yml"
cp "$PROJECT_ROOT/docs/deployment/offline-linux-amd64.md" "$OUTPUT_DIR/DEPLOYMENT.md"

printf '%s\n' "$IMAGE_TAG" > "$OUTPUT_DIR/image-tag.txt"
printf '%s\n' "Offline deployment package created: $OUTPUT_DIR"
