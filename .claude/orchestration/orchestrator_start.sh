#!/usr/bin/env bash
# Program that Claude Squad runs for the orchestrator instance (see launch_orchestrator.sh).
source "$(dirname "$0")/common.sh"
exec claude -n orchestrator "Read $ORCH/ORCHESTRATOR.md in full and follow it. Main checkout: $MAIN_ROOT. Then ask me for my list of changes."
