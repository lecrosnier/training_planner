#!/bin/bash
export PROJECT_DIR="/home/pi/training_planner"
export VENV_NAME="env"
export PYTHON_SCRIPT="bot.py"
cd "$PROJECT_DIR"
source "$PROJECT_DIR/$VENV_NAME/bin/activate"
python "$PROJECT_DIR/$PYTHON_SCRIPT"
