#!/bin/zsh
cd "$(dirname "$0")" || exit 1
python3 install_mac.py
printf '\n이 창을 닫아도 됩니다. 종료하려면 Enter를 누르세요.\n'
read
