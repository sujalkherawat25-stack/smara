"""Frozen native CLI entry: no legacy execution runtime bundled."""
import sys
from smara.native_runtime import main

if sys.argv[1:2] == ["--native-tools"]:
    from smara.native_tools import main as tools_main
    raise SystemExit(tools_main(sys.argv[2:]))
raise SystemExit(main())
