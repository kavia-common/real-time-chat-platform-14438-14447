#!/bin/bash
cd /home/kavia/workspace/code-generation/real-time-chat-platform-14438-14447/chat_backend
source venv/bin/activate
flake8 .
LINT_EXIT_CODE=$?
if [ $LINT_EXIT_CODE -ne 0 ]; then
  exit 1
fi

