#!/usr/bin/env python3
import os
import re
import shutil
import sys

path = sys.argv[1] if len(sys.argv) > 1 else r"C:\paperChart\CONF\dircnf.txt"
backup = path + ".bak"

if os.path.exists(path):
    shutil.copy2(path, backup)
    with open(path, "r", encoding="cp932", newline="") as f:
        content = f.read()
else:
    content = ""

module_line = "        module = monitors/B650Video.exe /std_arg/ ;"


def find_block(text: str, start: int, word: str, depth: int = 0):
    """Find a block starting with `word` followed by `{` at the given brace depth.
    Returns (block_start, brace_open, brace_close, after_block) or None.
    """
    i = 0
    stack = []
    n = len(text)
    while i < n:
        c = text[i]
        if c == "{":
            stack.append(i)
        elif c == "}":
            stack.pop()
        elif c == "/" and i + 1 < n and text[i + 1] == "/":
            # skip comment line
            end = text.find("\n", i)
            if end < 0:
                end = n
            i = end
            continue
        else:
            if len(stack) == depth and text.startswith(word, i):
                # check word boundary
                j = i + len(word)
                if j < n and not text[j].isalnum() and text[j] != "_":
                    # skip whitespace to find {
                    k = j
                    while k < n and text[k] in " \t\r\n":
                        k += 1
                    if k < n and text[k] == "{":
                        # now find matching } starting from k
                        brace_open = k
                        brace_depth = 1
                        m = k + 1
                        while m < n and brace_depth > 0:
                            if text[m] == "{":
                                brace_depth += 1
                            elif text[m] == "}":
                                brace_depth -= 1
                            elif text[m] == "/" and m + 1 < n and text[m + 1] == "/":
                                end = text.find("\n", m)
                                if end < 0:
                                    end = n
                                m = end
                                continue
                            m += 1
                        return (i, brace_open, m - 1, m)
        i += 1
    return None


def inject_module_into_block(block: str, module_line: str) -> str:
    """block includes '{' and '}'. Insert/replace module line inside."""
    # Find existing module line inside (exclude leading '{'/trailing '}')
    inner = block[1:-1]
    m = re.search(r"(?m)^\s*module\s*=.*$", inner)
    if m:
        inner = inner[: m.start()] + module_line + inner[m.end() :]
    else:
        # insert after leading { content, before the last }
        # place just before the closing brace with a newline
        inner = inner.rstrip(" \t\r\n")
        if inner and not inner.endswith("\n"):
            inner += "\n"
        inner += module_line + "\n"
    return "{" + inner + "}"


def patch_command_section(content: str) -> str:
    cmd = find_block(content, 0, "command")
    if not cmd:
        # no command section; append a new one
        content = content.rstrip("\r\n") + "\ncommand\n{\n    new\n    {\n" + module_line + "\n    }\n    append\n    {\n" + module_line + "\n    }\n}\n"
        return content

    _, brace_open, brace_close, after_block = cmd
    before = content[: brace_open + 1]
    cmd_body = content[brace_open + 1 : brace_close]
    after = content[brace_close:]

    for word in ("new", "append"):
        blk = find_block(cmd_body, 0, word, depth=0)
        if blk:
            start_word, block_open, block_close, block_after = blk
            block_old = cmd_body[block_open : block_close + 1]
            block_new = inject_module_into_block(block_old, module_line)
            cmd_body = cmd_body[: block_open] + block_new + cmd_body[block_close + 1 :]
        else:
            # add new/append block
            cmd_body = cmd_body.rstrip(" \t\r\n")
            if cmd_body and not cmd_body.endswith("\n"):
                cmd_body += "\n"
            cmd_body += "    {}\n    {{\n{}\n    }}\n".format(word, module_line)

    content = before + cmd_body + after
    return content


content = patch_command_section(content)

with open(path, "w", encoding="cp932", newline="") as f:
    f.write(content)

print("Updated:", path)
