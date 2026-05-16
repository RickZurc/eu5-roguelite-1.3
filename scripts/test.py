#!/usr/bin/env python3

from pathlib import Path

# Directory containing the files
INPUT_DIR = 'C:/Program Files (x86)/Steam/steamapps/common/Europa Universalis V/game/in_game/common/advances'

# Output file
OUTPUT_FILE = "combined.txt"

# Optional: file extensions to include
INCLUDE_EXTENSIONS = [".txt"]

input_path = Path(INPUT_DIR)
output_path = Path(OUTPUT_FILE)

# Open output file with UTF-8 BOM encoding
with output_path.open("w", encoding="utf-8-sig") as outfile:
    for file_path in sorted(input_path.rglob("*")):
        if file_path.is_file():

            if INCLUDE_EXTENSIONS and file_path.suffix not in INCLUDE_EXTENSIONS:
                continue

            print(f"Appending: {file_path}")

            try:
                # Open input files with UTF-8 BOM support
                with file_path.open("r", encoding="utf-8-sig") as infile:
                    outfile.write(infile.read())
                    outfile.write("\n")

            except Exception as e:
                outfile.write(f"\n[ERROR READING FILE: {e}]\n")

print(f"\nDone. Combined file saved as: {OUTPUT_FILE}")