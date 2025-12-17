#!/usr/bin/env python
"""
Compile Lua 5.1 source to bytecode using the bundled 32-bit lua5.1.dll.

Run under 32-bit Python (e.g. .\\.venv32\\Scripts\\python.exe):
  python script/compiler.py input.lua -o output.luac
"""

import argparse
import ctypes
import struct
from pathlib import Path


def load_lua_dll():
    dll_path = Path(__file__).with_name("lua5.1.dll")
    if not dll_path.is_file():
        raise FileNotFoundError(f"lua5.1.dll not found at {dll_path}")
    return ctypes.WinDLL(str(dll_path))


def compile_lua(lua_src: str, chunk_name: str = "chunk", encoding: str = "utf-8") -> bytes:
    lua = load_lua_dll()

    lua.luaL_newstate.restype = ctypes.c_void_p
    lua.lua_close.argtypes = [ctypes.c_void_p]
    lua.luaL_openlibs.argtypes = [ctypes.c_void_p]
    lua.luaL_loadbuffer.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p]
    lua.luaL_loadbuffer.restype = ctypes.c_int
    lua.lua_dump.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    lua.lua_dump.restype = ctypes.c_int
    lua.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_size_t)]
    lua.lua_tolstring.restype = ctypes.c_char_p

    L = lua.luaL_newstate()
    if not L:
        raise RuntimeError("failed to create Lua state")
    lua.luaL_openlibs(L)

    src_bytes = lua_src.encode(encoding)
    status = lua.luaL_loadbuffer(L, ctypes.c_char_p(src_bytes), ctypes.c_size_t(len(src_bytes)), ctypes.c_char_p(chunk_name.encode("utf-8")))
    if status != 0:
        sz = ctypes.c_size_t(0)
        msg = lua.lua_tolstring(L, -1, ctypes.byref(sz))
        err = msg[: sz.value].decode("utf-8", errors="replace") if msg else f"load error code {status}"
        lua.lua_close(L)
        raise RuntimeError(f"lua load error: {err}")

    out = bytearray()
    LUA_WRITER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p)

    @LUA_WRITER
    def writer(_L, p, sz, _ud):
        if sz:
            out.extend(ctypes.string_at(p, sz))
        return 0

    dump_status = lua.lua_dump(L, writer, None)
    lua.lua_close(L)
    if dump_status != 0:
        raise RuntimeError(f"lua_dump failed with code {dump_status}")
    return bytes(out)


def main():
    parser = argparse.ArgumentParser(description="Compile Lua 5.1 source to bytecode via lua5.1.dll")
    parser.add_argument("input", type=Path, help="path to .lua file or a directory containing .lua files")
    parser.add_argument("-o", "--out", type=Path, help="output file (when input is file) or output directory (when input is directory)")
    parser.add_argument("--encoding", default="utf-8", help="text encoding for source files (default utf-8; use shift_jis for original game scripts)")
    args = parser.parse_args()

    if struct.calcsize("P") * 8 != 32:
        raise SystemExit("Please run under 32-bit Python (e.g., .\\.venv32\\Scripts\\python.exe).")

    if args.input.is_file():
        src_text = args.input.read_text(encoding=args.encoding)
        out_path = args.out if args.out else args.input.with_suffix(".scb")
        bytecode = compile_lua(src_text, chunk_name=str(args.input.name), encoding=args.encoding)
        out_path.write_bytes(bytecode)
        print(f"Compiled {args.input} -> {out_path} ({len(bytecode)} bytes)")
        return

    if not args.input.is_dir():
        raise SystemExit(f"Input not found: {args.input}")

    out_dir = args.out if args.out else args.input  # 默认写回同目录结构
    count = 0
    failures = []
    for lua_path in sorted(args.input.rglob("*.lua")):
        rel = lua_path.relative_to(args.input)
        dst = (out_dir / rel).with_suffix(".scb")
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            src_text = lua_path.read_text(encoding=args.encoding)
            bytecode = compile_lua(src_text, chunk_name=str(rel), encoding=args.encoding)
        except Exception as exc:  # Keep going if a decompiled file is malformed
            failures.append((rel, str(exc)))
            print(f"[FAIL] {rel}: {exc}")
            continue
        dst.write_bytes(bytecode)
        count += 1
        print(f"[OK] {rel} -> {dst}")

    if failures:
        print(f"Done with errors. Succeeded: {count}, Failed: {len(failures)}")
        for rel, msg in failures:
            print(f"  - {rel}: {msg}")
        raise SystemExit(1)

    print(f"Done. Compiled {count} files to {out_dir}")


if __name__ == "__main__":
    main()
