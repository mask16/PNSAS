@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo  웰딩 장비 AS 처리 시스템 시작
echo  포트: 5004
echo  외부: http://10.164.38.45:5004/
echo  내부: http://43.201.229.107:5004/
echo ========================================

python -m pip install -r requirements.txt -q
python app.py
