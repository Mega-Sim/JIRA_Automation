# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for JiraSCCB
# Run: pyinstaller jira_sccb.spec

import sys
from pathlib import Path

block_cipher = None

# ttkbootstrap 테마 파일 경로
try:
    import ttkbootstrap as _tb
    _tb_dir = str(Path(_tb.__file__).parent)
except Exception:
    _tb_dir = None

# openpyxl 템플릿 파일 경로
try:
    import openpyxl as _ox
    _ox_dir = str(Path(_ox.__file__).parent)
except Exception:
    _ox_dir = None

datas = []
if _tb_dir:
    datas += [
        (str(Path(_tb_dir) / 'themes'), 'ttkbootstrap/themes'),
        (str(Path(_tb_dir) / 'localization'), 'ttkbootstrap/localization'),
    ]
if _ox_dir:
    datas += [
        (str(Path(_ox_dir) / 'reader'), 'openpyxl/reader'),
        (str(Path(_ox_dir) / 'writer'), 'openpyxl/writer'),
        (str(Path(_ox_dir) / 'styles'), 'openpyxl/styles'),
    ]

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'ttkbootstrap',
        'ttkbootstrap.themes',
        'ttkbootstrap.themes.standard',
        'ttkbootstrap.themes.user',
        'ttkbootstrap.dialogs',
        'ttkbootstrap.dialogs.dialogs',
        'ttkbootstrap.constants',
        'ttkbootstrap.scrolled',
        'ttkbootstrap.tooltip',
        'ttkbootstrap.toast',
        'ttkbootstrap.tableview',
        'ttkbootstrap.validation',
        'ttkbootstrap.localization',
        'PIL',
        'PIL.Image',
        'PIL.ImageTk',
        'PIL.ImageDraw',
        'PIL.ImageFont',
        'openpyxl',
        'openpyxl.styles',
        'openpyxl.utils',
        'openpyxl.workbook',
        'openpyxl.worksheet',
        'openpyxl.writer',
        'openpyxl.reader',
        'openpyxl.chart',
        'openpyxl.drawing',
        'requests',
        'requests.packages',
        'urllib3',
        'certifi',
        'charset_normalizer',
        'idna',
        'zoneinfo',
        'concurrent.futures',
        'threading',
        'webbrowser',
        're',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'pandas', 'scipy', 'pytest', 'cryptography'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='JiraSCCB',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,      # GUI 앱이므로 콘솔 창 없음
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,          # 아이콘 파일이 있으면 여기에 경로 지정: icon='app.ico'
)
