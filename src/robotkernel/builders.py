# -*- coding: utf-8 -*-
import os
from io import StringIO
from typing import Dict

from robot.api import get_model
from robot.errors import DataError
from robot.running.builder.parsers import ErrorReporter
from robot.running.model import TestSuite
from robot.running.builder.testsettings import TestDefaults
from robot.running.builder.transformers import SettingsBuilder, SuiteBuilder


def _get_rpa_mode(data):
    if not data:
        return None
    tasks = [s.tasks for s in data.sections if hasattr(s, "tasks")]
    if all(tasks) or not any(tasks):
        return tasks[0] if tasks else None
    raise DataError("One file cannot have both tests and tasks.")


def strip_duplicate_items(items):
    """Remove duplicates from an item list."""
    new_items = {}
    for item in items:
        new_items[item.name] = item
    items._items = list(new_items.values())


def clean_items(items):
    """Remove elements from an item list."""
    items._items = []

# TODO: Refactor to use public API only
# https://github.com/robotframework/robotframework/commit/fa024345cb58d154e1d8384552b62788d3ed6258


def populate_suite(code: str, suite: TestSuite, defaults: TestDefaults):
    """Build new code and populate the given test suite."""
    # Build code and populate the suite with the new keywords ands tests
    ast = get_model(
        StringIO(code), data_only=False, curdir=os.getcwd().replace("\\", "\\\\")
    )
    ErrorReporter(code).visit(ast)
    SettingsBuilder(suite, defaults).visit(ast)
    SuiteBuilder(suite, defaults).visit(ast)

    # Strip duplicate keywords and variables
    strip_duplicate_items(suite.resource.keywords)
    strip_duplicate_items(suite.resource.variables)

    # Detect RPA
    suite.rpa = _get_rpa_mode(ast)
