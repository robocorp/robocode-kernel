# -*- coding: utf-8 -*-
import os
import re
import robot
import sys
import uuid
from collections import OrderedDict

from IPython.utils.tokenutil import line_at_cursor
from ipykernel.kernelapp import IPKernelApp

from robot.running.model import TestSuite
from robot.running.builder.testsettings import TestDefaults
from robotkernel import __version__
from robotkernel.completion_finders import complete_libraries
from robotkernel.constants import CONTEXT_LIBRARIES, HAS_NBIMPORTER, VARIABLE_REGEXP
from robotkernel.display import DisplayKernel
from robotkernel.exceptions import BrokenOpenConnection
from robotkernel.builders import clean_items, populate_suite
from robotkernel.executors import execute_python, execute_robot
from robotkernel.listeners import (
    AppiumConnectionsListener, JupyterConnectionsListener, RobotKeywordsIndexerListener,
    RobotVariablesListener, RpaBrowserConnectionsListener, SeleniumConnectionsListener,
    WhiteLibraryListener
)
from robotkernel.monkeypatches import inject_libdoc_ipynb_support, inject_robot_ipynb_support
from robotkernel.selectors import (
    clear_selector_highlights, get_autoit_selector_completions, get_selector_completions,
    get_white_selector_completions, get_win32_selector_completions, is_autoit_selector,
    is_selector, is_white_selector, is_win32_selector
)
from robotkernel.utils import (
    close_current_connection, detect_robot_context, get_keyword_doc, get_lunr_completions,
    lunr_builder, lunr_query, scored_results, yield_current_connection
)


PID = os.getpid()

if HAS_NBIMPORTER:
    import nbimporter  # noqa


# noinspection PyAbstractClass,DuplicatedCode
class RobotKernel(DisplayKernel):
    implementation = "IRobot"
    implementation_version = __version__
    language = "robotframework"
    language_version = robot.__version__
    language_info = {
        "mimetype": "text/x-robotframework",
        "name": "Robot Framework",
        "file_extension": ".robot",
        "codemirror_mode": "robotframework",
        "pygments_lexer": "robotframework",
    }
    banner = "Robot Framework kernel"

    def __init__(self, **kwargs):
        super(RobotKernel, self).__init__(**kwargs)

        # Enable nbreader
        inject_robot_ipynb_support()
        inject_libdoc_ipynb_support()

        # History to repeat after kernel restart
        self.robot_history = OrderedDict()
        self.robot_cell_id = None  # current cell id from init_metadata
        self.robot_inspect_data = {}
        self.robot_variables = []
        self.robot_suite_variables = {}

        # Sticky connection cache (e.g. for webdrivers)
        self.robot_connections = []

        # Cache keywords
        self.new_keywords = []
        self.keywords = []

        # Searchable index for keyword autocomplete documentation
        builder = lunr_builder("dottedname", ["dottedname", "name"])
        self.robot_catalog = {
            "builder": builder,
            "index": None,
            "libraries": [],
            "keywords": {},
        }
        populator = RobotKeywordsIndexerListener(self.robot_catalog)
        populator.library_import("BuiltIn", {})
        for name, keywords in CONTEXT_LIBRARIES.items():
            # noinspection PyProtectedMember
            populator._library_import(keywords, name)

        # Create test suite
        self.suite = TestSuite(name="Robocode Lab", source=os.getcwd())
        self.defaults = TestDefaults(None)

    def do_shutdown(self, restart):
        super(RobotKernel, self).do_shutdown(restart)
        self.robot_history = OrderedDict()
        self.robot_variables = []
        self.robot_suite_variables = {}
        for driver in self.robot_connections:
            if hasattr(driver["instance"], "quit"):
                driver["instance"].quit()
        self.robot_connections = []

    def do_complete(self, code, cursor_pos):
        context = detect_robot_context(code, cursor_pos)
        cursor_pos = cursor_pos is None and len(code) or cursor_pos
        line, offset = line_at_cursor(code, cursor_pos)
        line_cursor = cursor_pos - offset
        needle = re.split(r"\s{2,}|\t| \| ", line[:line_cursor])[-1].lstrip()

        self.log.debug("Completing text: %s", needle)

        if needle and needle[0] in "$@&%":  # is variable completion
            self.log.debug("Context: Variable")
            matches = [
                m["ref"]
                for m in scored_results(
                    needle,
                    [
                        dict(ref=v)
                        for v in (self.robot_variables + VARIABLE_REGEXP.findall(code))
                    ],
                )
                if needle.lower() in m["ref"].lower()
            ]
            if len(line) > line_cursor and line[line_cursor] == "}":
                cursor_pos += 1
                needle += "}"

        elif is_selector(needle):
            self.log.debug("Context: Selenium or Appium selector")
            self.log.debug("Current WebDrivers: %s", self.robot_connections)
            matches = []
            for driver in yield_current_connection(
                self.robot_connections, ["RPA.Browser", "selenium", "jupyter", "appium"]
            ):
                matches = get_selector_completions(needle.rstrip(), driver)

        elif is_autoit_selector(needle):
            self.log.debug("Context: AutoIt selector")
            matches = get_autoit_selector_completions(needle)

        elif is_white_selector(needle):
            self.log.debug("Context: WhiteLibrary selector")
            matches = get_white_selector_completions(needle)

        elif is_win32_selector(needle):
            self.log.debug("Context: Win32 selector")
            matches = get_win32_selector_completions(needle)

        elif context == "__settings__" and any(
            [
                line.lower().startswith("library "),
                "import library " in line.lower(),
                "reload library " in line.lower(),
                "get library instance" in line.lower(),
            ]
        ):
            self.log.debug("Context: Library name")
            matches = complete_libraries(needle.lower())

        else:
            self.log.debug("Context: Keywords or Built-ins")
            # Clear selector completion highlights
            for driver in yield_current_connection(
                self.robot_connections, ["RPA.Browser", "selenium", "jupyter"]
            ):
                try:
                    clear_selector_highlights(driver)
                except BrokenOpenConnection:
                    close_current_connection(self.robot_connections, driver)
            matches = get_lunr_completions(
                needle,
                self.robot_catalog["index"],
                self.robot_catalog["keywords"],
                context,
            )

        self.log.debug("Available completions: %s", matches)

        return {
            "matches": matches,
            "cursor_end": cursor_pos,
            "cursor_start": cursor_pos - len(needle),
            "metadata": {},
            "status": "ok",
        }

    def do_inspect(self, code, cursor_pos, detail_level=0):
        cursor_pos = cursor_pos is None and len(code) or cursor_pos
        line, offset = line_at_cursor(code, cursor_pos)
        line_cursor = cursor_pos - offset
        left_needle = re.split(r"\s{2,}|\t| \| ", line[:line_cursor])[-1]
        right_needle = re.split(r"\s{2,}|\t| \| ", line[line_cursor:])[0]
        needle = left_needle.lstrip().lower() + right_needle.rstrip().lower()

        reply_content = {
            "status": "ok",
            "data": self.robot_inspect_data,
            "metadata": {},
            "found": bool(self.robot_inspect_data),
        }

        results = []
        if needle and lunr_query(needle):
            query = lunr_query(needle)
            results = self.robot_catalog["index"].search(query)
            results += self.robot_catalog["index"].search(query.strip("*"))
        for result in results:
            keyword = self.robot_catalog["keywords"][result["ref"]]
            if needle not in [keyword.name.lower(), result["ref"].lower()]:
                continue
            self.robot_inspect_data.update(get_keyword_doc(keyword))
            reply_content["found"] = True
            break

        return reply_content

    def init_metadata(self, parent):
        # Jupyter Lab sends deleted cells and the currently updated cell
        # id as message metadata, that allows to keep robot history in
        # sync with the displayed notebook:
        deleted_cells = (parent.get("metadata") or {}).get("deletedCells") or []
        for cell_id in deleted_cells:
            if cell_id in self.robot_history:
                del self.robot_history[cell_id]
        self.robot_cell_id = (parent.get("metadata") or {}).get("cellId") or None
        return super(RobotKernel, self).init_metadata(parent)

    def do_execute(
        self, code, silent, store_history=True, user_expressions=None, allow_stdin=False
    ):
        # Reload ipynb modules
        if HAS_NBIMPORTER:
            for name, module in tuple(sys.modules.items()):
                if "nbimporter.NotebookLoader" in repr(module):
                    del sys.modules[name]

        # Clear selector completion highlights
        for driver in yield_current_connection(
            self.robot_connections, ["RPA.Browser", "selenium", "jupyter"]
        ):
            try:
                clear_selector_highlights(driver)
            except BrokenOpenConnection:
                close_current_connection(self.robot_connections, driver)

        # Support %%python module ModuleName cell magic
        match = re.match("^%%python module ([a-zA-Z_]+)", code)
        if match is not None:
            module = match.groups()[0]
            return execute_python(
                self,
                code[len("%%python module {0:s}".format(module)) :],
                module,
                silent,
            )
        else:
            # Update variables
            self.robot_variables = []
            for historical in self.robot_history.values():
                self.robot_variables.extend(
                    VARIABLE_REGEXP.findall(historical, re.U & re.M)
                )
            self.robot_variables.extend(VARIABLE_REGEXP.findall(code, re.U & re.M))

            # Configure listeners
            self.listeners = [
                RpaBrowserConnectionsListener(self.robot_connections),
                SeleniumConnectionsListener(self.robot_connections),
                JupyterConnectionsListener(self.robot_connections),
                AppiumConnectionsListener(self.robot_connections),
                WhiteLibraryListener(self.robot_connections),
                RobotKeywordsIndexerListener(self.robot_catalog),
                RobotVariablesListener(self.robot_suite_variables),
            ]

            # Build suite
            try:
                populate_suite(code, self.suite, self.defaults)
            except Exception as e:
                if not silent:
                    self.send_error(
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

            # Keep new keywords in memory, they are used for constructing the widgets
            self.new_keywords = [item for item in self.suite.resource.keywords._items if item not in self.keywords]
            self.keywords = list(self.suite.resource.keywords._items)

            # Execute test case
            result = execute_robot(self, self.suite, silent)

            # Save history
            if result["status"] == "ok":
                self.robot_history[self.robot_cell_id or str(uuid.uuid4())] = code

            # Clean the tests that were run
            clean_items(self.suite.tests)

            return result


class RobotKernelApp(IPKernelApp):
    name = "robot-kernel"
    kernel_class = RobotKernel

    log_datefmt = "%Y/%m/%d %H:%M:%S"
    log_format = (
        "%(asctime)s.%(msecs)03d"
        " › %(levelname)s"
        " › %(name)s"
        f" › {PID}"
        " › %(message)s"
    )


if __name__ == "__main__":
    RobotKernelApp.launch_instance()
