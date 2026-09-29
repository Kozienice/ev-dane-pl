name: pobierz-dane-ev

on:
  workflow_dispatch:
  schedule:
    - cron: "17 5 * * 6"

permissions:
  contents: write

jobs:
  pobierz:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r wymagania.txt
      - name: polska
        run: python polska.py
        continue-on-error: true
      - name: europa
        run: python europa.py
        continue-on-error: true
      - name: zapisz zmiany
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add data
          if git diff --cached --quiet; then
            echo "brak zmian w danych"
          else
            git commit -m "dane: $(date -u +%Y-%m-%dT%H:%MZ)"
            git push
          fi
