#!/usr/bin/env python3
"""
repack.py

Debonosu Works 引擎通用打包工具。
功能：
1. 读取 index.json 和源文件目录。
2. 对每个文件进行 Zlib 压缩（raw deflate）。
3. 重新计算偏移量，重建索引。
4. 生成新的 .pak 文件。

注意：改动过的文件重压缩后字节流会不同，包大小与原始可能不一致，但结构正确可正常加载。
"""

import argparse
import json
import pathlib
import struct
import sys
import zlib

class PakBuilder:
    def __init__(self, header_info: dict):
        self.header_info = header_info
        self.data = bytearray()
        self.index_entries = []  # 按 index.json 顺序收集（目录 + 文件），用于重建索引
        self.current_offset = 0

    def add_dir(self, meta: dict):
        time_bytes = b"\x00" * 24
        if isinstance(meta.get("time_hex"), str):
            try:
                time_bytes = bytes.fromhex(meta["time_hex"])
            except ValueError:
                time_bytes = b"\x00" * 24

        self.index_entries.append(
            {
                "type": "dir",
                "name": pathlib.Path(meta["path"]).name,
                "child_count": meta.get("child_count", 0),
                "attributes": meta.get("attributes", 0x10),
                "time_bytes": time_bytes,
            }
        )

    def add_file(self, path: pathlib.Path, meta: dict):
        """读取文件，压缩，并记录元数据（偏移/尺寸重新计算，不依赖旧包）。"""
        if not path.is_file():
            raise FileNotFoundError(f"找不到文件: {path}")

        raw_data = path.read_bytes()
        uncompressed_size = len(raw_data)

        # time 字段
        if isinstance(meta.get("time_hex"), str):
            try:
                time_bytes = bytes.fromhex(meta["time_hex"])
            except ValueError:
                time_bytes = b"\x00" * 24
        else:
            time_bytes = b"\x00" * 24

        # 使用 raw deflate (wbits=-15)
        compressed_data = zlib.compress(raw_data, level=9, wbits=-15)
        compressed_size = len(compressed_data)

        entry = {
            "type": "file",
            "name": pathlib.Path(meta["path"]).name,
            "attributes": meta.get("attributes", 0),
            "offset": self.current_offset,
            "compressed_size": compressed_size,
            "uncompressed_size": uncompressed_size,
            "time_bytes": time_bytes,
        }

        self.index_entries.append(entry)
        self.data.extend(compressed_data)
        self.current_offset += compressed_size

    def build_index(self) -> bytes:
        """构建未压缩的二进制索引块：offset(int64)、usize(int64)、csize(int64)、flags(uint32)、time(24字节)、name\\0"""
        index_buffer = bytearray()

        for entry in self.index_entries:
            time_bytes = entry.get("time_bytes") or b"\x00" * 24
            time_bytes = time_bytes.ljust(24, b"\x00")[:24]

            if entry["type"] == "dir":
                offset = 0
                usize = entry["child_count"]
                csize = 0
                flags = entry["attributes"]
            else:
                offset = entry["offset"]
                usize = entry["uncompressed_size"]
                csize = entry["compressed_size"]
                flags = entry["attributes"]

            index_buffer.extend(struct.pack("<QQQI24s", offset, usize, csize, flags, time_bytes))

            try:
                name_bytes = entry["name"].encode("cp932")
            except UnicodeEncodeError:
                print(f"Warning: Filename {entry['name']} encoding fallback.")
                name_bytes = entry["name"].encode("utf-8")
            index_buffer.extend(name_bytes)
            index_buffer.append(0)  # null terminator

        return index_buffer

    def save(self, output_path: pathlib.Path):
        raw_index = self.build_index()
        raw_index_size = len(raw_index)

        compressed_index = zlib.compress(raw_index, level=9, wbits=-15)
        comp_index_size = len(compressed_index)

        header_offset = self.header_info.get("header_offset", 16)
        index_rel_offset = self.header_info.get("index_rel", 24)
        root_count = self.header_info.get("root_count", 0)
        unk1 = self.header_info.get("unknown1", 0)
        unk2 = self.header_info.get("unknown2", 0)

        global_header = struct.pack(
            '<4s I I I',
            b'PAK\x00',
            header_offset,
            0x00060010,  # 版本，保留默认
            0
        )

        ext_header = struct.pack(
            '<6I',
            index_rel_offset,   # Index Offset (relative to ExtHeader start)
            unk1,
            root_count,
            raw_index_size,     # Decompressed Size
            comp_index_size,    # Compressed Size
            unk2
        )

        print(f"Writing {output_path}...")
        print(f"  Index: Raw {raw_index_size} / Comp {comp_index_size}")
        print(f"  Data:  {len(self.data)} bytes")

        with open(output_path, 'wb') as f:
            f.write(global_header)
            f.write(ext_header)
            f.write(compressed_index)
            f.write(self.data)

def main():
    parser = argparse.ArgumentParser(description="Debonosu PAK Repacker")
    parser.add_argument("index", type=pathlib.Path, help="index.json")
    parser.add_argument("source", type=pathlib.Path, help="Input directory containing files")
    parser.add_argument("-o", "--out", type=pathlib.Path, default=pathlib.Path("game_new.pak"), help="Output PAK file")
    args = parser.parse_args()

    # 1. 加载索引
    try:
        index_data = json.loads(args.index.read_text(encoding="utf-8"))
        header_info = index_data.get("header", {})
        entries = index_data.get("entries", [])
    except Exception as e:
        print(f"Error loading index.json: {e}")
        sys.exit(1)

    builder = PakBuilder(header_info)

    # 2. 处理每个条目（目录 + 文件），顺序保持与 index.json 一致，保证 child_count 可解析
    print(f"Processing {len(entries)} entries...")
    for entry_info in entries:
        etype = entry_info.get("type")
        rel_path = entry_info.get("path")

        if etype == "dir":
            builder.add_dir(entry_info)
            continue

        if etype == "file":
            file_path = args.source / rel_path
            print(f"  Packing: {rel_path}", end='\r')
            try:
                builder.add_file(file_path, entry_info)
            except Exception as e:
                print(f"\nError packing {rel_path}: {e}")
                sys.exit(1)
        else:
            print(f"\nWarning: Unknown entry type {etype} for {rel_path}, skipped")

    print("\nEntry processing done.")

    # 3. 保存 PAK
    try:
        builder.save(args.out)
        print("Done!")
    except Exception as e:
        print(f"Error saving PAK: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
