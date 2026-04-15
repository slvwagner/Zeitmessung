import argparse
import pathlib
import re
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compiler", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--qstrdefs-py", required=True)
    parser.add_argument("--qstrdefs-port", required=True)
    parser.add_argument("--qstrdefs-collected", required=True)
    args, cpp_flags = parser.parse_known_args()

    merged = []
    for path_str in (args.qstrdefs_py, args.qstrdefs_port, args.qstrdefs_collected):
        merged.append(pathlib.Path(path_str).read_text(encoding="utf-8"))
    merged_text = "".join(merged)

    # Match the original sed step:
    #   sed 's/^Q(.*)/"&"/'
    quoted = re.sub(r'^Q\(.*\)$', lambda m: f'"{m.group(0)}"', merged_text, flags=re.MULTILINE)

    cmd = [args.compiler, "-E", *cpp_flags, "-"]
    proc = subprocess.run(cmd, input=quoted, text=True, capture_output=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        return proc.returncode

    # Match the original trailing sed step:
    #   sed 's/^"\(Q(.*)\)"$/\1/'
    unquoted = re.sub(r'^"(Q\(.*\))"$', r"\1", proc.stdout, flags=re.MULTILINE)
    pathlib.Path(args.output).write_text(unquoted, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
