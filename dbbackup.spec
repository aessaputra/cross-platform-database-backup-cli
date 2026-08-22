# PyInstaller single-file spec — bundles Python only, not DB binaries
from PyInstaller.utils.hooks import collect_all
a = Analysis(['dbbackup/cli.py'], pathex=['.'], binaries=[], datas=[], hiddenimports=['dbbackup.adapters.mysql','dbbackup.adapters.postgres','dbbackup.adapters.mongo','dbbackup.adapters.sqlite','dbbackup.core.backup','dbbackup.core.restore','dbbackup.core.scheduler'], hookspath=[], runtime_hooks=[], excludes=[])
pyz = PYZ(a.pure, a.zipped_data)
exe = EXE(pyz, a.scripts, a.binaries, a.zipfiles, a.datas, name='dbbackup', onefile=True, console=True)
