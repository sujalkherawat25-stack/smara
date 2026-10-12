"""Frozen native CLI entry: no legacy execution runtime bundled."""
import sys
from smara.native_runtime import main

if sys.argv[1:2] == ["--native-goals"]:
    from smara.native_management import main as manage_main
    raise SystemExit(manage_main(sys.argv[1:]))

if sys.argv[1:2] == ["--native-tools"]:
    from smara.native_tools import main as tools_main
    raise SystemExit(tools_main(sys.argv[2:]))
if sys.argv[1:2] == ["--native-browser"]:
    from smara.native_browser import main as browser_main
    raise SystemExit(browser_main(sys.argv[2:]))
raise SystemExit(main())
