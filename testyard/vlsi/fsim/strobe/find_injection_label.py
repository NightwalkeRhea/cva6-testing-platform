#!/usr/bin/env python3

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


DEFAULT_OBJDUMP = "riscv64-unknown-elf-objdump"
DEFAULT_DEFINE = "START_INJECTION_LABEL"
DEFAULT_STROBE_FILE = Path(__file__).with_name("strobe_rocket.sv")


def run_objdump(binary: Path, objdump: str) -> str:
	cmd = [objdump, "-t", str(binary)]
	try:
		res = subprocess.run(cmd, check=True, text=True, capture_output=True)
	except FileNotFoundError:
		print(f"Error: objdump executable not found: {objdump}", file=sys.stderr)
		sys.exit(1)
	except subprocess.CalledProcessError as exc:
		print("Error: objdump failed", file=sys.stderr)
		if exc.stderr:
			print(exc.stderr.strip(), file=sys.stderr)
		sys.exit(1)
	return res.stdout


def find_label_address(objdump_output: str, label_substring: str) -> int:
	pattern = re.compile(r"^([0-9a-fA-F]+)\s+\w\s+\S+\s+\S+\s+\S+\s+(.+)$")
	matches = []

	for line in objdump_output.splitlines():
		m = pattern.match(line)
		if not m:
			continue
		addr_hex = m.group(1)
		symbol = m.group(2).strip()
		if label_substring in symbol:
			matches.append((int(addr_hex, 16), symbol))

	if not matches:
		print(
			f"Error: no symbol containing '{label_substring}' found in objdump output",
			file=sys.stderr,
		)
		sys.exit(1)

	exact = [item for item in matches if item[1] == label_substring]
	if exact:
		if len(exact) > 1:
			print(
				f"Error: multiple exact matches found for '{label_substring}'",
				file=sys.stderr,
			)
			for _, sym in exact:
				print(f"  - {sym}", file=sys.stderr)
			sys.exit(1)
		return exact[0][0]

	if len(matches) > 1:
		print(
			f"Error: multiple symbols containing '{label_substring}' found; be more specific",
			file=sys.stderr,
		)
		for addr, sym in matches:
			print(f"  - 0x{addr:08x} {sym}", file=sys.stderr)
		sys.exit(1)

	return matches[0][0]


def replace_define_value(sv_path: Path, define_name: str, address: int) -> None:
	if not sv_path.exists():
		print(f"Error: strobe file not found: {sv_path}", file=sys.stderr)
		sys.exit(1)

	contents = sv_path.read_text(encoding="utf-8")

	replacement = f"`define {define_name} 32'h{address:08x}"
	define_re = re.compile(
		rf"^\s*`define\s+{re.escape(define_name)}\s+32'h[0-9a-fA-F]+\s*$",
		re.MULTILINE,
	)

	new_contents, count = define_re.subn(replacement, contents, count=1)

	if count == 0:
		print(
			f"Error: could not find define '{define_name}' with 32'h... format in {sv_path}",
			file=sys.stderr,
		)
		sys.exit(1)

	sv_path.write_text(new_contents, encoding="utf-8")
	print(f"Updated {sv_path}: {replacement}")


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description=(
			"Find a symbol address from RISCV objdump symbol table and update "
			"`define START_INJECTION in strobe_rocket.sv"
		)
	)
	parser.add_argument("binary", type=Path, help="Path to RISCV ELF/binary")
	parser.add_argument("label", help="Substring (or exact name) of the target symbol")
	parser.add_argument(
		"--objdump",
		default=os.environ.get("RISCV_OBJDUMP", DEFAULT_OBJDUMP),
		help=(
			"Objdump executable path/name. "
			"Defaults to RISCV_OBJDUMP env var or riscv64-unknown-elf-objdump"
		),
	)
	parser.add_argument(
		"--strobe-file",
		type=Path,
		default=DEFAULT_STROBE_FILE,
		help="Path to strobe_rocket.sv",
	)
	parser.add_argument(
		"--define-name",
		default=DEFAULT_DEFINE,
		help="SystemVerilog define to replace (default: START_INJECTION)",
	)
	return parser.parse_args()


def main() -> None:
	args = parse_args()

	if not args.binary.exists():
		print(f"Error: binary not found: {args.binary}", file=sys.stderr)
		sys.exit(1)

	objdump_output = run_objdump(args.binary, args.objdump)
	address = find_label_address(objdump_output, args.label)
	replace_define_value(args.strobe_file, args.define_name, address)


if __name__ == "__main__":
	main()
