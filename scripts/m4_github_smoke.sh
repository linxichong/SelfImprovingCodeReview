#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "使用法: $0 <対象リポジトリ> <設定YAML> <Pull Request番号>" >&2
  exit 64
fi

if [[ -z "${REVIEW_SCM_TOKEN:-}" ]]; then
  echo "REVIEW_SCM_TOKEN を環境変数へ設定してください。" >&2
  exit 65
fi

repository=$1
config=$2
change=$3
script_directory=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
project_directory=$(cd "${script_directory}/.." && pwd)
output_directory="${repository}/.review/m4-github-smoke"
mkdir -p "${output_directory}"

uv --project "${project_directory}" run --frozen review scm review \
  --repository "${repository}" \
  --config "${config}" \
  --change "${change}" \
  --output "${output_directory}/dry-run.json"

echo "ドライラン完了: ${output_directory}/dry-run.json"

if [[ "${M4_GITHUB_ALLOW_PUBLISH:-NO}" == "YES" ]]; then
  uv --project "${project_directory}" run --frozen review scm review \
    --repository "${repository}" \
    --config "${config}" \
    --change "${change}" \
    --publish \
    --output "${output_directory}/published.json"
  echo "明示投稿完了: ${output_directory}/published.json"
fi

if [[ "${M4_GITHUB_SYNC_FEEDBACK:-NO}" == "YES" ]]; then
  uv --project "${project_directory}" run --frozen review scm sync-feedback \
    --repository "${repository}" \
    --config "${config}" \
    --change "${change}" \
    --output "${output_directory}/feedback.json"
  echo "フィードバック同期完了: ${output_directory}/feedback.json"
fi
