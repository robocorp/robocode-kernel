# -*- coding: utf-8 -*-
from collections import OrderedDict
from io import BytesIO, StringIO
import base64
import binascii
import os
import re
import sys
import types
import uuid
from copy import deepcopy
from tempfile import TemporaryDirectory
from traceback import format_exc
from typing import List, Tuple
from urllib.parse import unquote
import ipywidgets
from IPython.core.display import clear_output, display
from PIL import Image
from robot.reporting import ResultWriter
from robot.running.model import TestSuite
from robotkernel.builders import clean_items, populate_suite
from robotkernel.constants import ICON_FILE_TEXT
from robotkernel.display import DisplayKernel, ProgressUpdater
from robotkernel.listeners import ReturnValueListener, RobotKeywordsIndexerListener, RobotVariablesListener, StatusEventListener
from robotkernel.utils import data_uri, display_log, to_mime_and_metadata


def execute_python(kernel: DisplayKernel, code: str, module: str, silent: bool):
    """"Execute Python code str in the context of named module.
    If the named module is not found, a new module is introduced.
    """
    if module not in sys.modules:
        sys.modules[module] = types.ModuleType(module)
    try:
        exec(code, sys.modules[module].__dict__)
        return {"status": "ok", "execution_count": kernel.execution_count}
    except Exception as e:
        if not silent:
            kernel.send_error(
                {
                    "ename": e.__class__.__name__,
                    "evalue": str(e),
                    "traceback": list(format_exc().splitlines()),
                }
            )
        return {
            "status": "error",
            "ename": e.__class__.__name__,
            "evalue": str(e),
            "traceback": list(format_exc().splitlines()),
        }


def normalize_argument(name):
    if "=" in name:
        name, default = name.split("=", 1)
    else:
        default = None

    return (
        name,
        re.sub(r"\W", "_", re.sub(r"^[^\w]*|[^\w]*$", "", name, re.U), re.U),
        default
    )


def execute_ipywidget(
    kernel: DisplayKernel,
    silent: bool,
    display_id: str,
    rpa: bool,
    name,
    arguments,
    values,
):
    header = rpa and "Tasks" or "Test Cases"
    code = f"""\

*** {header} ***

{name}
    {name}  {'  '.join([values[a[1]] for a in arguments])}
"""

    # Copy the test suite
    suite = deepcopy(kernel.suite)
    populate_suite(code, suite, kernel.defaults)
    suite.rpa = True

    try:
        with TemporaryDirectory() as path:
            run_robot_suite(
                kernel, suite, silent, display_id, path, widget=True
            )
    except PermissionError:
        # Purging of TemporaryDirectory may fail e.g. with geckodriver.log still open
        pass


def inject_ipywidget(
    kernel: DisplayKernel,
    suite: TestSuite,
    silent: bool,
    display_id: str,
    rpa: bool,
    name: str,
    arguments: List[Tuple[str, str, str]],
):
    def execute(**values):
        execute_ipywidget(
            kernel,
            silent,
            display_id,
            rpa,
            name,
            arguments,
            values,
        )

    widgets = []
    controls = OrderedDict()
    out = ipywidgets.widgets.Output()

    def update(*args):
        values = {key: control.value for key, control in controls.items()}
        with out:
            description = widgets[0].description
            widgets[0].description = "Executing..."
            for widget in widgets:
                widget.disabled = True
            clear_output(wait=True)
            try:
                execute(**values)
            finally:
                widgets[0].description = description
                for widget in widgets:
                    widget.disabled = False

    for arg in arguments:
        widgets.append(ipywidgets.widgets.Label(value=arg[1] + "="))
        widgets.append(ipywidgets.widgets.Text(value=arg[2]))
        controls[arg[1]] = widgets[-1]

    button = ipywidgets.widgets.Button(description=name)
    button.on_click(update)
    widgets.insert(0, button)

    # noinspection PyTypeChecker
    layout = ipywidgets.widgets.Layout(
        display="flex", flex_flow="row", flex_wrap="wrap", justify_content="flex-start"
    )

    ui = ipywidgets.widgets.Box(widgets, layout=layout)
    # noinspection PyTypeChecker
    display(ui, out, display_id=display_id)


def inject_ipywidgets(
    kernel: DisplayKernel,
    suite: TestSuite,
    silent: bool,
    display_id: str,
    rpa: bool,
):
    for keyword in kernel.new_keywords:
        name = keyword.name
        arguments = [normalize_argument(arg) for arg in keyword.args]

        inject_ipywidget(
            kernel, suite, silent, display_id, rpa, name, arguments
        )


def execute_robot(
    kernel: DisplayKernel,
    suite: TestSuite,
    silent: bool,
):
    display_id = str(uuid.uuid4())

    for listener in kernel.listeners:
        # Update keywords catalog
        if isinstance(listener, RobotKeywordsIndexerListener):
            # noinspection PyProtectedMember
            listener._import_from_suite_data(suite)
        # Drop global variables from cache
        if isinstance(listener, RobotVariablesListener):
            for variable in suite.resource.variables:
                listener.variables.pop(variable.name, None)

    if suite.tests:
        try:
            with TemporaryDirectory() as path:
                reply = run_robot_suite(
                    kernel, suite, silent, display_id, path
                )
        except PermissionError:
            # Purging of TemporaryDirectory may fail e.g. with geckodriver.log still open
            pass
    else:
        inject_ipywidgets(
            kernel, suite, silent, display_id, suite.rpa
        )
        reply = {"status": "ok", "execution_count": kernel.execution_count}

    return reply


def run_robot_suite(
    kernel: DisplayKernel,
    suite: TestSuite,
    silent: bool,
    display_id: str,
    path: str,
    widget: bool = False,
):
    return_values = []
    if not (silent or widget):
        progress = ProgressUpdater(kernel, display_id, sys.__stdout__)
    else:
        progress = None

    # Init status
    listeners = kernel.listeners[:]
    if not (silent or widget):
        listeners.append(StatusEventListener(lambda data: progress.update(data)))
    if not silent:
        listeners.append(ReturnValueListener(lambda v: return_values.append(v)))

    stdout = StringIO()
    if progress is not None:
        sys.__stdout__ = progress
    try:
        results = suite.run(outputdir=path, stdout=stdout, listener=listeners)
    finally:
        if progress is not None:
            sys.__stdout__ = progress.stdout

    stats = results.statistics

    # Reply error on error
    if stats.total.critical.failed:
        if not silent:
            kernel.send_error(
                {"ename": "", "evalue": "", "traceback": stdout.getvalue().splitlines()}
            )

    # Display result of the last keyword
    elif (
        len(return_values)
        and return_values[-1] is not None
        and return_values[-1] != ""
        and return_values[-1] != b""
        and not silent
    ):  # this comparison is "numpy compatible"
        bundle, metadata = to_mime_and_metadata(return_values[-1])
        if bundle:
            kernel.send_execute_result(bundle, metadata)

    # Process screenshots
    process_screenshots(kernel, path, silent)

    # Generate report
    writer = ResultWriter(os.path.join(path, "output.xml"))
    writer.write_results(
        log=os.path.join(path, "log.html"),
        report=None,
        rpa=getattr(suite, "rpa", False),
    )

    with open(os.path.join(path, "log.html"), "rb") as fp:
        log = fp.read()
        log = log.replace(b'"reportURL":"report.html"', b'"reportURL":null')

    # Clear status and display results
    if not silent:
        log_button = """
        <button
          class="jp-mod-styled jp-mod-accept"
          onClick="{};event.preventDefault();event.stopPropagation();"
        >
          <div style="display:inline-flex;align-self:center;">
            <div style="top:.125em;position:relative;margin-right:5px;">{}</div> Log
          </div>
        </button>
        """.format(
            display_log(log, "log.html"),
            ICON_FILE_TEXT
        )
        if widget:
            kernel.send_display_data({"text/html": log_button}, display_id=display_id)
        else:
            kernel.send_update_display_data(
                {"text/html": log_button}, display_id=display_id
            )

    # Reply ok on pass
    if stats.total.critical.failed:
        return {
            "status": "error",
            "ename": "",
            "evalue": "",
            "traceback": stdout.getvalue().splitlines(),
        }
    else:
        return {"status": "ok", "execution_count": kernel.execution_count}


def process_screenshots(kernel: DisplayKernel, path: str, silent: bool):
    cwd = os.getcwd()
    with open(os.path.join(path, "output.xml")) as fp:
        xml = fp.read()
    for src in re.findall('img src="([^"]+)', xml):
        if os.path.exists(src):
            filename = src
        elif os.path.exists(os.path.join(path, src)):
            filename = os.path.join(path, src)
        elif os.path.exists(os.path.join(cwd, src)):
            filename = os.path.join(cwd, src)
        elif src.startswith("data:"):
            filename = None
            try:
                spec, uri = src.split(",", 1)
                spec, encoding = spec.split(";", 1)
                spec, mimetype = spec.split(":", 1)
                if not (encoding == "base64" and mimetype.startswith("image/")):
                    continue
                data = base64.b64decode(unquote(uri).encode("utf-8"))
                im = Image.open(BytesIO(data))
            except (binascii.Error, IndexError, ValueError):
                continue
        else:
            continue
        if filename:
            im = Image.open(filename)
            mimetype = Image.MIME[im.format]
            # Fix issue where Pillow on Windows returns APNG for PNG
            if mimetype == "image/apng":
                mimetype = "image/png"
            with open(filename, "rb") as fp:
                data = fp.read()
        uri = data_uri(mimetype, data)
        xml = xml.replace('a href="{}"'.format(src), "a")
        xml = xml.replace(
            'img src="{}" width="800px"'.format(src),
            'img src="{}" style="max-width:800px;"'.format(uri),
        )  # noqa: E501
        xml = xml.replace('img src="{}"'.format(src), 'img src="{}"'.format(uri))
        if not silent:
            kernel.send_display_data(
                {mimetype: base64.b64encode(data).decode("utf-8")},
                {mimetype: {"height": im.height, "width": im.width}},
            )
    with open(os.path.join(path, "output.xml"), "w") as fp:
        fp.write(xml)
