#!/bin/bash
set -e

# Read lab number from .labrc file and validate it
# Usage: ./lab_number.sh
# Outputs: LAB number to stdout
# Exit codes: 0 - success, 1 - error

LABRC_FILE=".labrc"

# Check if .labrc exists
if [ ! -f "$LABRC_FILE" ]; then
  echo "❌ Error: $LABRC_FILE file not found!" >&2
  echo "Create $LABRC_FILE file with: echo 'LAB=0' > $LABRC_FILE" >&2
  exit 1
fi

# Read LAB value
LAB=$(grep -E '^LAB=' "$LABRC_FILE" | cut -d'=' -f2 | tr -d ' ')

# Check if LAB variable exists
if [ -z "$LAB" ]; then
  echo "❌ Error: LAB variable not found in $LABRC_FILE" >&2
  echo "Add to $LABRC_FILE: LAB=0" >&2
  exit 1
fi

# Validate LAB value (must be a non-negative integer)
if ! [[ "$LAB" =~ ^[0-9]+$ ]]; then
  echo "❌ Error: LAB must be a non-negative integer" >&2
  echo "Current value: LAB=$LAB" >&2
  echo "Fix $LABRC_FILE: echo 'LAB=0' > $LABRC_FILE" >&2
  exit 1
fi

# Success - output LAB number
echo "✅ Running checks for lab $LAB" >&2
echo "$LAB"
