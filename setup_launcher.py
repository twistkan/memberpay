"""Installer for the reusable edition; never scans or imports original workspaces."""
import os
import sys
import shutil
import subprocess
import tempfile
import threading
import queue
from pathlib import Path
import tkinter as tk
from tkinter import ttk

APP_NAME = 'MemberPay'
INSTALL_ROOT = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'MemberPayGroups' / 'Application'


def bundle_root():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'payload' / APP_NAME


def legacy_root():
    return None  # Legacy imports require an explicit user choice inside the app.


def create_shortcut():
    def quoted(value):
        return "'" + str(value).replace("'", "''") + "'"
    script = f"""$ErrorActionPreference='Stop'
$desktop=[Environment]::GetFolderPath('DesktopDirectory')
$shell=New-Object -ComObject WScript.Shell
$shortcut=$shell.CreateShortcut((Join-Path $desktop 'MemberPay Groups.lnk'))
$shortcut.TargetPath={quoted(INSTALL_ROOT / 'MemberPay.exe')}
$shortcut.WorkingDirectory={quoted(INSTALL_ROOT)}
$shortcut.Description='MemberPay — independent group workspace'
$shortcut.IconLocation={quoted(str(INSTALL_ROOT / 'app_icon.ico')+',0')}
$shortcut.Save()
"""
    with tempfile.NamedTemporaryFile('w',suffix='.ps1',delete=False,encoding='utf-8-sig') as handle:
        handle.write(script)
        path=Path(handle.name)
    try:
        powershell=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
        result=subprocess.run([str(powershell),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(path)],capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'Could not create the desktop shortcut.')
    finally:
        path.unlink(missing_ok=True)


def install():
    source=bundle_root()
    if not (source/'MemberPay.exe').is_file():
        raise FileNotFoundError('The packaged MemberPay program is missing.')
    INSTALL_ROOT.mkdir(parents=True,exist_ok=True)
    # Only program files are eligible for installation, including on updates.
    for name in ('MemberPay.exe','_internal','app_icon.ico','licenses'):
        item=source/name
        if item.is_dir():shutil.copytree(item,INSTALL_ROOT/name,dirs_exist_ok=True)
        elif item.is_file():shutil.copy2(item,INSTALL_ROOT/name)
    icon=INSTALL_ROOT/'_internal/app_icon.ico'
    if icon.exists():shutil.copy2(icon,INSTALL_ROOT/'app_icon.ico')
    create_shortcut()


class Installer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('MemberPay Groups Setup')
        self.geometry('480x260')
        self.resizable(False,False)
        self.results=queue.Queue()
        frame=ttk.Frame(self,padding=28);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='MemberPay',font=('Segoe UI',20,'bold')).pack(anchor='w')
        ttk.Label(frame,text='Set up an independent group workspace.\nExisting apps and their data stay separate.',wraplength=420).pack(anchor='w',pady=12)
        self.status=tk.StringVar(value='Installing program files…')
        self.progress=ttk.Progressbar(frame,mode='indeterminate');self.progress.pack(fill='x',pady=12)
        ttk.Label(frame,textvariable=self.status,wraplength=420).pack(anchor='w')
        self.after(200,self.run_install)
    def run_install(self):
        self.progress.start(12)
        threading.Thread(target=self._install_worker,daemon=True).start()
        self.after(100,self.poll_result)
    def _install_worker(self):
        try:install();self.results.put(None)
        except Exception as exc:
            message=str(exc)
            try:
                INSTALL_ROOT.mkdir(parents=True,exist_ok=True)
                (INSTALL_ROOT/'setup-error.log').write_text(message,encoding='utf8')
            except OSError:pass
            self.results.put(message)
    def poll_result(self):
        try:result=self.results.get_nowait()
        except queue.Empty:self.after(100,self.poll_result);return
        self.progress.stop()
        if result:
            self.status.set('Setup could not complete: '+result)
        else:
            self.status.set('Ready. Opening your group setup…')
            subprocess.Popen([str(INSTALL_ROOT/'MemberPay.exe')],cwd=str(INSTALL_ROOT))
            self.after(900,self.destroy)


if __name__=='__main__':Installer().mainloop()
