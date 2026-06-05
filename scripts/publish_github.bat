@echo off
git init
git branch -M main
git add .
git commit -m "Initial commit: CyberOffice Agent OS"
git remote remove origin 2>NUL
git remote add origin https://github.com/w1767178707/cyber-office-agent-os.git
git push -u origin main
