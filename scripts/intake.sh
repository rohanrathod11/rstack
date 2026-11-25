#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C

fail() { printf 'Intake failed. %s\n' "$*" >&2; exit 1; }
usage() {
  printf 'Usage: intake.sh [worktree] [config-file]\n'
  printf '       intake.sh [worktree] [config-file] --ack <pr>:<head> [reason]\n'
}
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  usage
  (( $# <= 2 )) && exit 0
  exit 2
fi

worktree=.
config_file=
ack=
ack_reason=
if (( $# )) && [[ $1 != --ack ]]; then
  worktree=${1:-.}
  shift
fi
if (( $# )) && [[ $1 != --ack ]]; then
  config_file=$1
  shift
fi
if (( $# )); then
  if [[ $1 != --ack ]] || (( $# < 2 || $# > 3 )); then
    usage >&2
    exit 2
  fi
  [[ $2 =~ ^[1-9][0-9]*:[0-9a-f]{40}$ ]] || fail 'Use a PR number and full head SHA for --ack.'
  ack=$2
  ack_reason=${3-}
fi

if [[ -n $config_file && $config_file != /* ]]; then
  config_file=$PWD/$config_file
fi
cd -P -- "$worktree" || fail 'Cannot open the worktree.'
root=$(git rev-parse --show-toplevel) || fail 'Use a worktree.'
cd -P -- "$root" || fail 'Cannot open the worktree root.'

settings=$(jq -ces '
  def path: type == "string" and length > 0 and (contains("\u0000") | not);
  select(length == 1) | .[0]
  | select(type == "object")
  | .merge_queue.repository as $repo
  | (if has("intake") then .intake else {} end) as $intake
  | select(($repo | path and test("^[^/[:space:]]+/[^/[:space:]]+$"))
      and ($intake | type == "object"
        and (if has("state_file") then .state_file | path else true end)
        and (if has("verdicts_dir") then .verdicts_dir | path else true end)))
  | {repository: $repo,
     state_file: ($intake.state_file // ".rstack/intake-state.json"),
     verdicts_dir: ($intake.verdicts_dir // ".rstack/verdicts")}
' "${config_file:-conductor.config.json}") || fail 'Check merge_queue.repository and the intake paths.'
repository=$(jq -r '.repository' <<< "$settings")
state_file=$(jq -r '.state_file' <<< "$settings")
verdicts_dir=$(jq -r '.verdicts_dir' <<< "$settings")

exec python3 - "$repository" "$state_file" "$verdicts_dir" "$ack" "$ack_reason" <<'PY'
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path


repository, state_path, verdicts_path, ack, reason = sys.argv[1:]
owner, repo = repository.split("/")
state_file = Path(state_path)
verdicts_dir = Path(verdicts_path)
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
HEAD = re.compile(r"[0-9a-f]{40}")
PR_FIELDS = """
    number
    headRefOid
    isDraft
    createdAt
    updatedAt
    author { login }
    commits(first: 1) { totalCount }
"""
OPEN_PRS = """
    query($owner: String!, $repo: String!, $endCursor: String) {
      repository(owner: $owner, name: $repo) {
        pullRequests(states: OPEN, first: 100, after: $endCursor,
                     orderBy: {field: CREATED_AT, direction: ASC}) {
          nodes { %s }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
""" % PR_FIELDS
ONE_PR = """
    query($owner: String!, $repo: String!, $number: Int!) {
      repository(owner: $owner, name: $repo) {
        pullRequest(number: $number) { state %s }
      }
    }
""" % PR_FIELDS


def matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def valid_review(entry, timestamp_key, text_key):
    return (isinstance(entry, dict)
            and type(entry.get("pr")) is int and entry["pr"] > 0
            and matches(HEAD, entry.get("head"))
            and matches(TIMESTAMP, entry.get(timestamp_key))
            and isinstance(entry.get(text_key), str))


def read_state():
    if not state_file.exists() and not state_file.is_symlink():
        return {"since": "1970-01-01T00:00:00Z", "pending": [], "acked": []}
    if not state_file.is_file():
        raise ValueError(f"Expected an intake state file at {state_file}.")
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        if not matches(TIMESTAMP, state["since"]):
            raise ValueError
        datetime.strptime(state["since"], TIME_FORMAT)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Expected a UTC since timestamp in {state_file}.") from error
    for key, timestamp_key, text_key in (("pending", "first_seen", "author"),
                                         ("acked", "at", "reason")):
        entries = state.setdefault(key, [])
        if not isinstance(entries, list) or not all(
            valid_review(entry, timestamp_key, text_key) for entry in entries
        ):
            raise ValueError(f"Expected a valid {key} list in {state_file}.")
    return state


def save_state(state):
    scratch = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=state_file.parent,
                                          prefix=".intake-state.", delete=False)
    path = Path(scratch.name)
    try:
        with scratch:
            json.dump(state, scratch, indent=2)
            scratch.write("\n")
        os.replace(path, state_file)
    finally:
        if path.exists():
            path.unlink()


def review_key(entry):
    return entry["pr"], entry["head"]


def acknowledge(state, now):
    number, head = ack.split(":")
    pr = int(number)
    key = (pr, head)
    if any(review_key(entry) == key for entry in state["acked"]):
        return
    pending = [entry for entry in state["pending"] if review_key(entry) != key]
    if len(pending) == len(state["pending"]):
        raise ValueError(f"No pending review for #{pr} head {head}.")
    state["pending"] = pending
    state["acked"].append({"pr": pr, "head": head, "reason": reason, "at": now})
    save_state(state)


def graphql(query, *options):
    return json.loads(subprocess.check_output([
        "gh", "api", *options, "graphql", "-f", f"owner={owner}", "-f", f"repo={repo}",
        "-f", f"query={query}",
    ]))


def repo_data(response):
    if not isinstance(response, dict) or response.get("errors") not in (None, []):
        raise ValueError
    return response["data"]["repository"]


def parse_pr(node):
    pr, head, draft = node["number"], node["headRefOid"], node["isDraft"]
    created, updated = node["createdAt"], node["updatedAt"]
    author = node["author"]
    login = "[unknown]" if author is None else author["login"]
    commits = node["commits"]["totalCount"]
    if not (type(pr) is int and pr > 0 and matches(HEAD, head)
            and type(draft) is bool and type(commits) is int and commits >= 0
            and matches(TIMESTAMP, created) and matches(TIMESTAMP, updated)
            and isinstance(login, str) and login):
        raise ValueError
    return {"pr": pr, "head": head, "author": login, "draft": draft, "commits": commits,
            "created": created, "updated": updated}


READ_ERRORS = (OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError)


def read_open_prs():
    try:
        pages = graphql(OPEN_PRS, "--paginate", "--slurp")
        if not isinstance(pages, list) or not pages:
            raise ValueError
        prs = {}
        for index, page in enumerate(pages):
            connection = repo_data(page)["pullRequests"]
            nodes = connection["nodes"]
            if not isinstance(nodes, list) or connection["pageInfo"]["hasNextPage"] is not (
                index + 1 < len(pages)
            ):
                raise ValueError
            for node in nodes:
                candidate = parse_pr(node)
                pr = candidate["pr"]
                if pr not in prs or candidate["updated"] >= prs[pr]["updated"]:
                    prs[pr] = candidate
        return prs
    except READ_ERRORS as error:
        raise ValueError("Cannot read open PRs.") from error


def read_pending_pr(pr):
    try:
        node = repo_data(graphql(ONE_PR, "-F", f"number={pr}"))["pullRequest"]
        if node["number"] == pr:
            if node["state"] in ("CLOSED", "MERGED"):
                return None
            if node["state"] == "OPEN":
                return parse_pr(node)
        raise ValueError
    except READ_ERRORS as error:
        raise ValueError(f"Cannot read pending PR #{pr}.") from error


def scan(state, started):
    previous = {entry["pr"]: entry for entry in state["pending"]}
    acked = {review_key(entry) for entry in state["acked"]}
    prs = read_open_prs()
    # A pending PR can be absent while the open list is changing between pages.
    for pr in sorted(previous.keys() - prs.keys()):
        candidate = read_pending_pr(pr)
        if candidate is not None:
            prs[pr] = candidate

    pending, report = [], []
    for candidate in sorted(prs.values(), key=lambda row: (row["author"], row["pr"])):
        pr, head = review_key(candidate)
        old = previous.get(pr)
        changed = max(candidate["created"], candidate["updated"]) >= state["since"]
        if (not changed and old is None) or (pr, head) in acked:
            continue
        if (verdicts_dir / f"{head}.md").is_file():
            if changed:
                report.append((candidate, "verdict exists"))
            continue
        same_head = old is not None and old["head"] == head
        pending.append({"pr": pr, "head": head, "author": candidate["author"],
                        "first_seen": old["first_seen"] if same_head else started})
        report.append((candidate, "still pending" if same_head else "needs review"))
    state.update(since=started, pending=pending)
    return report


def print_report(report, since):
    if not report:
        print(f"No open PRs created or updated since {since}.")
    previous_author = None
    for candidate, status in report:
        author = candidate["author"]
        if author != previous_author:
            print(author)
            previous_author = author
        draft = str(candidate["draft"]).lower()
        print(f"  #{candidate['pr']} head {candidate['head']} draft {draft} "
              f"commits {candidate['commits']}. {status}")


try:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    # Keep scans and acknowledgements from replacing each other's state.
    with state_file.with_name(f".{state_file.name}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read_state()
        since = state["since"]
        started = subprocess.check_output(["date", "-u", f"+{TIME_FORMAT}"], encoding="ascii").strip()
        if started < since:
            raise ValueError("The state timestamp is ahead of the current time.")
        if ack:
            acknowledge(state, started)
        else:
            # The start time keeps updates during pagination eligible for the next scan.
            report = scan(state, started)
            save_state(state)
    if not ack:
        print_report(report, since)
except (OSError, ValueError, subprocess.SubprocessError) as error:
    sys.exit(f"Intake failed. {error}")
PY
