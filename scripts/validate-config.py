#!/usr/bin/env python3
"""Validate a conductor config without running its commands."""

import argparse
import json
import re
import sys
from pathlib import Path


def object_fields(fields, optional=None):
    optional = optional or {}
    known = fields.keys() | optional.keys()

    def check(value, path, problems):
        if not isinstance(value, dict):
            problems.append(f"{path}: expected an object")
            return
        for key, validate in fields.items():
            if key in value:
                validate(value[key], f"{path}.{key}", problems)
            else:
                problems.append(f"{path}.{key}: required")
        for key, validate in optional.items():
            if key in value:
                validate(value[key], f"{path}.{key}", problems)
        for key in sorted(value.keys() - known):
            problems.append(f"{path}[{json.dumps(key)}]: unknown key")

    return check


def array_of(validate, minimum=0):
    def check(value, path, problems):
        if not isinstance(value, list):
            problems.append(f"{path}: expected an array")
            return
        if len(value) < minimum:
            problems.append(f"{path}: expected at least {minimum} item(s)")
        for index, item in enumerate(value):
            validate(item, f"{path}[{index}]", problems)

    return check


def rule(accepts, message):
    def check(value, path, problems):
        if not accepts(value):
            problems.append(f"{path}: {message}")

    return check


def string_value(value):
    return isinstance(value, str) and "\0" not in value


string = rule(string_value, "expected a string without NUL")
nonempty = rule(
    lambda value: string_value(value) and bool(value),
    "expected a nonempty string without NUL",
)
positive = rule(lambda value: type(value) is int and value > 0, "expected a positive integer")


def matching_string(pattern, message):
    return rule(lambda value: string_value(value) and re.fullmatch(pattern, value), message)


def command(value, path, problems):
    array_of(string, minimum=1)(value, path, problems)
    if isinstance(value, list) and value and value[0] == "":
        problems.append(f"{path}[0]: expected a program name")


def optional_command(value, path, problems):
    if value != []:
        command(value, path, problems)


def fallbacks(value, path, problems):
    if not isinstance(value, dict):
        problems.append(f"{path}: expected an object")
        return
    for model, choices in value.items():
        model_path = f"{path}[{json.dumps(model)}]"
        nonempty(model, model_path, problems)
        array_of(nonempty)(choices, model_path, problems)


utc_clock = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")
weekday = rule(
    lambda value: type(value) is int and 1 <= value <= 7,
    "expected an ISO weekday from 1 to 7",
)
window_fields = object_fields({
    "days": array_of(weekday, minimum=1),
    "start_utc": matching_string(utc_clock, "expected a UTC time in HH:MM format"),
    "end_utc": matching_string(utc_clock, "expected a UTC time in HH:MM format"),
})


def freeze_window(value, path, problems):
    window_fields(value, path, problems)
    if not isinstance(value, dict):
        return
    start = value.get("start_utc")
    if isinstance(start, str) and utc_clock.fullmatch(start) and start == value.get("end_utc"):
        problems.append(f"{path}.end_utc: must differ from start_utc")


route = object_fields({"via": nonempty, "model": string})
validate = object_fields({
    "roles": object_fields({
        **{role: route for role in (
            "conductor", "planner", "plan_reviewer", "investigator",
            "builder_high_risk", "builder_normal", "builder_easy", "reviewer",
        )},
        "plan_panel": array_of(route),
    }),
    "difficulty_grades": object_fields({
        grade: array_of(route) for grade in ("frontier", "hard", "normal", "easy")
    }),
    "review_policy": object_fields({
        key: nonempty for key in (
            "docs_wording_generated", "normal_code", "high_impact_parts",
            "large_work_items", "plans_and_designs", "p1_loops",
        )
    }),
    "metrics": object_fields({
        "parts_file": nonempty,
        "routing_review": nonempty,
        "move_down_when": nonempty,
    }),
    "trust_tiers": nonempty,
    "hierarchy": object_fields({
        "main_conductor": nonempty,
        "lane_windows": nonempty,
        "max_depth": positive,
    }),
    "caps": object_fields({
        key: positive for key in (
            "running_agents_per_window", "local_heavy_run_slots",
            "commits_per_train", "prs_per_train", "trains_in_ci",
        )
    }, optional={"windows": positive}),
    "merge_queue": object_fields({
        "repository": matching_string(r"[^/\s]+/[^/\s]+", "expected owner/repo"),
        "main_branch": nonempty,
        "push_gate": object_fields({
            "pr_description_command": command,
            "secret_scan_command": command,
            "history_walk_command": optional_command,
            "test_command": command,
            "test_paths": array_of(nonempty, minimum=1),
            "test_families": array_of(object_fields({
                "name": nonempty,
                "paths": array_of(nonempty, minimum=1),
                "min_files": positive,
            }), minimum=1),
            "reader_path_prefixes": array_of(rule(
                lambda value: string_value(value) and value.endswith("/"),
                "expected a directory prefix ending in /",
            )),
            "module_test_names": array_of(rule(
                lambda value: string_value(value) and value.count("{module}") == 1,
                "expected a string with one {module} placeholder",
            )),
            "blocking_suites": array_of(object_fields({
                "name": nonempty,
                "inputs": array_of(nonempty, minimum=1),
                "command": command,
            })),
        }),
        "merge_when_green": object_fields({
            "min_workflows": positive,
            "advisory_workflow_paths": array_of(nonempty),
            "deploy_workflow": nonempty,
            "freeze_windows": array_of(freeze_window),
            "deploy_lead_minutes": rule(
                lambda value: type(value) is int and 0 <= value < 10080,
                "expected an integer from 0 to 10079",
            ),
            "poll_seconds": positive,
            "log_file": nonempty,
        }),
    }),
    "planning": object_fields({"plan_for": nonempty, "tasks_and_bug_fixes": nonempty}),
    "fallbacks": fallbacks,
    "autonomy": object_fields({
        "proceed_without_asking": array_of(nonempty),
        "pause_for": array_of(nonempty),
    }),
}, optional={
    "_comment": string,
    "intake": object_fields({}, optional={"state_file": nonempty, "verdicts_dir": nonempty}),
})


def reject_constant(value):
    raise ValueError(f"invalid JSON value {value}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", nargs="?", default="conductor.config.json", type=Path)
    args = parser.parse_args()
    problems = []
    try:
        with args.config.open(encoding="utf-8") as stream:
            config = json.load(stream, parse_constant=reject_constant)
    except (OSError, ValueError) as error:
        problems.append(str(error))
    else:
        validate(config, "$", problems)

    for problem in problems:
        print(problem.replace("\n", r"\n"), file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
