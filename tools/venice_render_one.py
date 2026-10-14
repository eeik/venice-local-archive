"""Make Markdown reading copies and message-image files for one browser export."""
import argparse
import json
import pathlib

from venice_finalize import render


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("readable_export", type=pathlib.Path)
    args = parser.parse_args()
    chats, images = render(args.readable_export)
    print(json.dumps({"conversations": len(chats), "messageImages": len(images)}, indent=2))
