@echo off
cd /d "c:\Users\RONAN\OneDrive - East West University\NAHID EXCEL\NRS SOFTWARE"
echo Staging changes...
git add .
echo Committing changes...
git commit -m "Automated deployment: %date% %time%"
echo Pushing to GitHub (which triggers deployment)...
git push origin main
echo Done!
pause
