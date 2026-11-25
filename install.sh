#!/usr/bin/env bash
set -euo pipefail

force=false
if [[ ${1:-} == --force ]]; then
  force=true
  shift
fi
if (( $# > 0 )); then
  printf 'Usage: install.sh [--force]\n'
  [[ $# == 1 && ( $1 == --help || $1 == -h ) ]] && exit 0
  exit 2
fi

fail() { printf '%s\n' "$*" >&2; exit 1; }
source_dir=$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
claude_dir=$HOME/.claude
skills_dir=${CLAUDE_SKILLS_DIR:-$claude_dir/skills}
destination=$skills_dir/rstack
agents_dir=$claude_dir/agents
status=0

require() {
  if ! command -v "$1" >/dev/null; then
    printf 'Missing %s. Run brew install %s or sudo apt install %s.\n' "$1" "$2" "$1" >&2
    status=1
  fi
}

for dependency in bash git jq gh; do
  require "$dependency" "$dependency"
done
require python3 python
if ! command -v codex >/dev/null; then
  printf 'Optional codex is missing. Run brew install --cask codex.\n'
fi
if ! command -v opencode >/dev/null; then
  printf 'Optional opencode is missing. Run npm install -g opencode-ai.\n'
fi

for config in "$PWD/conductor.config.json" "$source_dir/conductor.config.json" "$claude_dir/conductor.config.json"; do
  if [[ -e $config || -L $config ]]; then
    command -v python3 >/dev/null || fail "Install python3 to validate $config."
    python3 "$source_dir/scripts/validate-config.py" "$config" || fail "Fix $config before installing."
    printf 'Validated %s\n' "$config"
    break
  fi
done

mkdir -p -- "$skills_dir" "$agents_dir"
lock=$claude_dir/.rstack-install.lock
mkdir -- "$lock" || fail "Cannot acquire install lock at $lock."
skill_stage=
agent_stage=
cleanup() {
  [[ -z $skill_stage ]] || rm -rf -- "$skill_stage"
  [[ -z $agent_stage ]] || rm -rf -- "$agent_stage"
  rmdir -- "$lock"
}
trap cleanup EXIT

skill_stage=$(mktemp -d "$skills_dir/.rstack-install.XXXXXX")
mkdir -- "$skill_stage/rstack"
for entry in LICENSE README.md SKILL.md conductor.config.example.json install.sh agents evals references scripts tests; do
  cp -pR -- "$source_dir/$entry" "$skill_stage/rstack/"
done

if [[ -d $destination && ! -L $destination ]] && diff -qr -- "$skill_stage/rstack" "$destination" >/dev/null; then
  printf 'Skill already installed at %s\n' "$destination"
else
  if [[ -e $destination || -L $destination ]]; then
    "$force" || fail "Skipped $destination. Use --force to replace it."
    mv -- "$destination" "$skill_stage/previous"
  fi
  if ! mv -- "$skill_stage/rstack" "$destination"; then
    if [[ -e $skill_stage/previous || -L $skill_stage/previous ]]; then
      if ! mv -- "$skill_stage/previous" "$destination"; then
        previous=$skill_stage/previous
        skill_stage=
        fail "Cannot restore $destination. The previous install is at $previous."
      fi
    fi
    fail "Cannot install $destination."
  fi
  printf 'Installed skill at %s\n' "$destination"
fi

agent_stage=$(mktemp -d "$agents_dir/.rstack-install.XXXXXX")
for agent in "$source_dir"/agents/*.md; do
  name=${agent##*/}
  target=$agents_dir/$name
  if [[ -f $target && ! -L $target ]] && cmp -s -- "$agent" "$target"; then
    continue
  fi
  if [[ -d $target ]]; then
    printf 'Skipped %s. Move the directory first.\n' "$target" >&2
    status=1
    continue
  fi
  if [[ -e $target || -L $target ]] && ! "$force"; then
    printf 'Skipped %s. Use --force to replace it.\n' "$target" >&2
    status=1
    continue
  fi
  cp -p -- "$agent" "$agent_stage/$name"
  mv -f -- "$agent_stage/$name" "$target"
  printf 'Installed agent %s\n' "$name"
done
exit "$status"
