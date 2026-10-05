from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Emit GitHub annotations containing failed pytest node names only."
    )
    parser.add_argument("junit_xml", type=Path)
    args = parser.parse_args()

    root = ET.parse(args.junit_xml).getroot()
    failed = []
    for case in root.iter("testcase"):
        if case.find("failure") is None and case.find("error") is None:
            continue
        classname = str(case.get("classname") or "pytest")
        name = str(case.get("name") or "unknown")
        failed.append(f"{classname}::{name}")

    if not failed:
        print("Pytest failed before reporting a test case.")
        return 0

    print(f"Failed tests: {len(failed)}")
    for node_id in failed:
        safe_name = node_id.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title=Failed test::{safe_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
