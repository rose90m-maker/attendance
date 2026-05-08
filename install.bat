@echo off
chcp 65001 >nul
title CAPS 동기화 설치
echo ============================================
echo   CAPS 출입 동기화 프로그램 설치
echo ============================================
echo.

REM 관리자 권한 확인
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [!] 관리자 권한이 필요합니다.
    echo     이 파일을 우클릭 → "관리자 권한으로 실행" 해주세요.
    echo.
    pause
    exit /b 1
)

REM Python 확인
python --version >nul 2>&1
if %errorLevel% neq 0 (
    echo [!] Python이 설치되어 있지 않습니다.
    echo     https://www.python.org/downloads/ 에서 설치 후 다시 실행하세요.
    echo.
    pause
    exit /b 1
)

echo [1/4] 필요한 패키지 설치 중...
python -m pip install pyodbc pymysql --quiet
if %errorLevel% neq 0 (
    echo [!] 패키지 설치 실패
    pause
    exit /b 1
)
echo   → pyodbc, pymysql 설치 완료
echo.

echo [2/4] caps_sync.py 복사 중...
if not exist "C:\Caps\ACServer" mkdir "C:\Caps\ACServer"
copy /Y "%~dp0caps_sync.py" "C:\Caps\ACServer\caps_sync.py" >nul
echo   → C:\Caps\ACServer\caps_sync.py
echo.

echo [3/4] 실행 파일 생성 중...
(
echo @echo off
echo chcp 65001 ^>nul
echo title CAPS 출입 동기화
echo echo ============================================
echo echo   CAPS 출입 동기화 프로그램
echo echo   종료: 이 창을 닫으세요 ^(X^)
echo echo ============================================
echo echo.
echo cd /d "C:\Caps\ACServer"
echo python caps_sync.py
echo pause
) > "C:\Caps\ACServer\CAPS동기화.bat"
echo   → C:\Caps\ACServer\CAPS동기화.bat
echo.

echo [4/4] 시작프로그램 등록 중...
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "LNK=%STARTUP%\CAPS동기화.lnk"

REM VBScript로 바로가기 생성
(
echo Set sh = CreateObject^("WScript.Shell"^)
echo Set lnk = sh.CreateShortcut^("%LNK%"^)
echo lnk.TargetPath = "C:\Caps\ACServer\CAPS동기화.bat"
echo lnk.WorkingDirectory = "C:\Caps\ACServer"
echo lnk.Description = "CAPS 출입 동기화"
echo lnk.WindowStyle = 7
echo lnk.Save
) > "%TEMP%\make_lnk.vbs"
cscript //nologo "%TEMP%\make_lnk.vbs"
del "%TEMP%\make_lnk.vbs"
echo   → 시작프로그램 등록 완료 (PC 부팅 시 자동 실행)
echo.

echo ============================================
echo   설치 완료!
echo.
echo   실행 방법:
echo     C:\Caps\ACServer\CAPS동기화.bat 더블클릭
echo     또는 PC 재부팅 시 자동 실행
echo ============================================
echo.

set /p RUN="지금 바로 실행할까요? (Y/n): "
if /i "%RUN%"=="" goto :run
if /i "%RUN%"=="y" goto :run
if /i "%RUN%"=="yes" goto :run
goto :end

:run
echo.
echo 동기화를 시작합니다...
cd /d "C:\Caps\ACServer"
python caps_sync.py

:end
pause
