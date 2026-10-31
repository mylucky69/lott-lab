#!/usr/bin/env bash
# Commit data changes and push, surviving concurrent pushes from other workflows.
# Usage: save.sh "commit message"
set -u
git config user.name "lott-lab-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add -A -- data docs/data
git diff --cached --quiet && { echo "Không có thay đổi"; exit 0; }
git commit -q -m "$1"
for i in 1 2 3 4 5; do
  if git pull --rebase -X theirs -q; then
    # site files are generated: rebuild them from the merged data
    python -m lottlab build > /dev/null
    git add -A -- data docs/data
    git diff --cached --quiet || git commit -q -m "Dựng lại dữ liệu trang web"
    git push -q && exit 0
  else
    git rebase --abort 2>/dev/null || true
  fi
  sleep $((i * 5))
done
echo "Không đẩy được thay đổi" >&2
exit 1
