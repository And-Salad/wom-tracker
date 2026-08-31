"""Launches WOM Tracker with no console window (double-click this file)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wom_tracker import main

main([])
